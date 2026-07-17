"""
Route-aware trajectory prediction engine.

``TrajectoryEngine`` (the existing baseline) assumes every aircraft
holds its current heading for the entire prediction horizon. That
assumption is the correct *starting* baseline for en-route cruise, but
breaks down as soon as an aircraft is following a known route with an
upcoming turn: dead reckoning predicts it flying straight through the
turn, while the aircraft (real or simulated) actually turns onto the
next leg. Over ASTRA's 30-60 minute horizons this is not a corner case
-- it is the common case for any aircraft that has not yet reached its
next waypoint.

``RouteAwareTrajectoryEngine`` fixes this the minimal way: for each
aircraft, if a current route (ordered list of remaining waypoints) is
known, propagate along that polyline at the aircraft's current ground
speed using ``astra.trajectory.route_following.advance_along_route`` --
the exact same function ``MockConnector`` uses to actually fly the
aircraft. If no route is known for an aircraft (BlueSky live mode
doesn't expose one yet, or the aircraft was created without one), this
engine falls back to plain constant-velocity dead reckoning for that
aircraft only, via the shared ``predict_constant_velocity`` function
(the same one ``TrajectoryEngine`` itself calls) -- so mixed traffic
(some aircraft on filed routes, some vectored/unknown) is handled
correctly in the same call, and the two engines can never silently
disagree on what "no route known" should produce.

Deliberately NOT in scope for this engine (see
docs/PROJECT_STATUS.md's trajectory-prediction follow-up for the
reasoning): performance-based speed/altitude profiles (BADA/OpenAP),
top-of-climb/top-of-descent modelling, wind correction. Vertical motion
uses the same linear ``vertical_speed_fpm`` extrapolation as the
baseline. Adding route-following was judged the single highest-value,
lowest-risk improvement to make first -- it fixes a structural
prediction error (predicted heading provably wrong after a turn) rather
than a magnitude error (predicted speed slightly off) -- and performance
modelling is left as a later, separately-evaluated layer on top of this
one, not bundled in here.

Why this is not circular reasoning
-----------------------------------
This engine's *only* extra input, beyond what the baseline already
uses, is each aircraft's own current route/flight-plan -- obtained via
``StateReader.get_route()`` (or any equivalent callable passed in as
``route_provider``), which reflects intent known *right now*. It never
reads any connector's future simulated positions. The evaluation
harness (``scripts/evaluate_trajectory_predictors.py``) checks this
engine's predictions against ground truth obtained by *independently
running the simulation forward* after the prediction was made --
a genuine held-out comparison, not a comparison against information the
predictor itself was given.
"""

from typing import Callable, Dict, List, Optional, Tuple

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.engine import predict_constant_velocity
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.trajectory.route_following import advance_along_route
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: A callable returning one aircraft's remaining route, or None if not
#: known -- matches ``StateReader.get_route``'s signature exactly so a
#: bound method can be passed straight through without adapting it.
RouteProvider = Callable[[str], Optional[List[Tuple[float, float]]]]


class RouteAwareTrajectoryEngine:
    """Route-following trajectory predictor with a dead-reckoning fallback.

    Same public API as ``TrajectoryEngine`` (``predict()`` returns a
    ``PredictionResult``), so it is a drop-in replacement anywhere a
    ``TrajectoryEngine`` is used -- including inside
    ``astra.pipeline.Pipeline``, if/when route-aware prediction is
    promoted from an evaluated alternative to the default. For this
    thesis, both engines are kept side by side and compared explicitly
    (see ``scripts/evaluate_trajectory_predictors.py``) rather than one
    silently replacing the other.

    Thread safety: stateless after construction, same as
    ``TrajectoryEngine`` -- ``route_provider`` is expected to be safe to
    call concurrently (``StateReader.get_route`` is).
    """

    def __init__(self, config: ASTRAConfig, route_provider: RouteProvider) -> None:
        """Initialise the engine.

        Args:
            config: Shared ASTRA configuration (reads
                ``prediction_horizons_min``, same as ``TrajectoryEngine``).
            route_provider: Callable returning an aircraft's current
                remaining route (``[(lat, lon), ...]``) given its
                callsign, or ``None`` if no route is known for it.
                Typically ``state_reader.get_route`` -- passed in rather
                than taking a ``StateReader`` directly so this engine
                stays decoupled from the interface layer and easy to
                unit-test with a plain dict/function.
        """
        self._config = config
        self._horizons: List[int] = sorted(config.prediction_horizons_min)
        self._route_provider = route_provider
        _LOG.debug("RouteAwareTrajectoryEngine initialised. Horizons: %s min", self._horizons)

    @property
    def horizons_min(self) -> List[int]:
        """Sorted list of prediction horizons in minutes."""
        return list(self._horizons)

    def predict(self, snapshot: TrafficSnapshot) -> PredictionResult:
        """Generate route-aware trajectory predictions for all horizons.

        For each aircraft in ``snapshot``, independently: fetches its
        current route via ``route_provider``; if one exists, propagates
        along it at the aircraft's current ground speed for each
        configured horizon; otherwise predicts that aircraft exactly as
        ``TrajectoryEngine`` would.

        Args:
            snapshot: Current observed traffic state.

        Returns:
            A ``PredictionResult`` with the same shape as
            ``TrajectoryEngine.predict()``'s -- one ``PredictedSnapshot``
            per configured horizon.
        """
        # Fetch each aircraft's route once per predict() call (not once
        # per horizon) -- the route is a property of "now", and every
        # horizon's prediction for one aircraft is computed by
        # travelling further along the *same* fetched route, exactly as
        # a single longer MockConnector tick would.
        routes: Dict[str, Optional[List[Tuple[float, float]]]] = {
            ac.callsign: self._route_provider(ac.callsign) for ac in snapshot
        }

        snapshots: Dict[int, PredictedSnapshot] = {}
        for h_min in self._horizons:
            snapshots[h_min] = self._predict_at_horizon(snapshot, h_min, routes)

        return PredictionResult(
            source_time_s=snapshot.timestamp_s,
            aircraft_count=len(snapshot),
            horizons_min=tuple(self._horizons),
            snapshots=snapshots,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _predict_at_horizon(
        self,
        snapshot: TrafficSnapshot,
        horizon_min: int,
        routes: Dict[str, Optional[List[Tuple[float, float]]]],
    ) -> PredictedSnapshot:
        dt_s = horizon_min * 60.0
        predicted_time_s = snapshot.timestamp_s + dt_s

        aircraft: Dict[str, AircraftState] = {}
        for ac in snapshot:
            route = routes.get(ac.callsign)
            if route:
                predicted_ac = self._predict_along_route(ac, route, dt_s, predicted_time_s)
            else:
                # No known route for this aircraft: identical result to
                # TrajectoryEngine's own dead-reckoning prediction (same
                # shared function, not a re-implementation).
                predicted_ac = predict_constant_velocity(ac, dt_s, predicted_time_s)
            aircraft[predicted_ac.callsign] = predicted_ac

        return PredictedSnapshot(
            horizon_min=horizon_min,
            source_time_s=snapshot.timestamp_s,
            predicted_time_s=predicted_time_s,
            aircraft=aircraft,
        )

    def _predict_along_route(
        self,
        ac: AircraftState,
        route: List[Tuple[float, float]],
        dt_s: float,
        predicted_time_s: float,
    ) -> AircraftState:
        """Predict one aircraft's state by flying its known route.

        Horizontal: ``advance_along_route`` at the aircraft's current
        ground speed -- turns at each waypoint exactly where the route
        says to, continues straight (dead reckoning) past the last
        waypoint if the horizon extends beyond the filed route.

        Vertical: same linear ``vertical_speed_fpm`` extrapolation as
        ``TrajectoryEngine`` (reuses ``predict_constant_velocity`` for
        that part directly, then overwrites horizontal position/heading
        with the route-following result) -- waypoint-level altitude
        constraints are deliberately out of scope for this engine (see
        module docstring).
        """
        distance_nm = ac.ground_speed_kt * (dt_s / 3600.0)
        result = advance_along_route(ac.lat, ac.lon, ac.heading_deg, route, distance_nm)

        # Reuse the shared vertical/altitude math verbatim; only the
        # horizontal position and heading differ from dead reckoning.
        dead_reckoned = predict_constant_velocity(ac, dt_s, predicted_time_s)

        return AircraftState(
            callsign=ac.callsign,
            lat=result.lat,
            lon=result.lon,
            altitude_ft=dead_reckoned.altitude_ft,
            ground_speed_kt=ac.ground_speed_kt,
            heading_deg=result.heading_deg,
            vertical_speed_fpm=ac.vertical_speed_fpm,
            aircraft_type=ac.aircraft_type,
            timestamp_s=predicted_time_s,
        )
