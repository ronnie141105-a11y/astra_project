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
wind correction, or any altitude modelling beyond linear
``vertical_speed_fpm`` extrapolation for aircraft with no known Scenario
Builder profile. Top of Descent modelling *is* now handled, but only
for aircraft explicitly spawned with a "LANDING" profile (see
``profile_provider``/``ProfileProvider`` and
``_predict_along_route``'s docstring) -- there is no attempt to infer
an implicit TOD for arbitrary en-route traffic with no such profile.
Adding route-following (and, on top of it, LANDING-profile TOD
modelling) was judged the highest-value, lowest-risk improvement to
make first -- it fixes a structural prediction error (predicted
heading/altitude provably wrong after a turn or before/after a known
descent) rather than a magnitude error (predicted speed slightly off)
-- and full performance modelling for arbitrary traffic is left as a
later, separately-evaluated layer on top of this one, not bundled in
here.

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
from astra.trajectory.route_following import (
    advance_along_route,
    remaining_route_distance_nm,
    top_of_descent_distance_nm,
)
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: A callable returning one aircraft's remaining route, or None if not
#: known -- matches ``StateReader.get_route``'s signature exactly so a
#: bound method can be passed straight through without adapting it.
RouteProvider = Callable[[str], Optional[List[Tuple[float, float]]]]

#: A callable returning one aircraft's Scenario Builder spawn profile
#: (currently just ``{"flight_type": "LANDING", "vertical_rate_fpm": ...}``,
#: or ``None`` if the aircraft has no forced profile), matching
#: ``StateReader.get_flight_profile``'s signature.
ProfileProvider = Callable[[str], Optional[Dict]]


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

    def __init__(
        self,
        config: ASTRAConfig,
        route_provider: RouteProvider,
        profile_provider: Optional[ProfileProvider] = None,
    ) -> None:
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
            profile_provider: Optional callable returning an aircraft's
                Scenario Builder spawn profile (see ``ProfileProvider``),
                or ``None`` if not supplied (every aircraft is then
                predicted with plain constant-vertical-speed
                extrapolation, matching this engine's original
                behaviour). Typically ``state_reader.get_flight_profile``.
                Only "LANDING"-profiled aircraft with a known route get
                the Top of Descent-aware altitude treatment described
                in ``_predict_along_route``'s docstring -- everything
                else (including "OVERFLIGHT", which holds cruise level
                throughout by design) is unaffected.
        """
        self._config = config
        self._horizons: List[int] = sorted(config.prediction_horizons_min)
        self._route_provider = route_provider
        self._profile_provider = profile_provider
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
        # Same "fetch once per predict() call, not once per horizon"
        # reasoning as `routes` above -- a profile is also a property
        # of "now" (this aircraft is currently flying a LANDING
        # profile), not something that changes per horizon.
        profiles: Dict[str, Optional[Dict]] = (
            {ac.callsign: self._profile_provider(ac.callsign) for ac in snapshot}
            if self._profile_provider is not None
            else {}
        )

        snapshots: Dict[int, PredictedSnapshot] = {}
        for h_min in self._horizons:
            snapshots[h_min] = self._predict_at_horizon(snapshot, h_min, routes, profiles)

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
        profiles: Dict[str, Optional[Dict]],
    ) -> PredictedSnapshot:
        dt_s = horizon_min * 60.0
        predicted_time_s = snapshot.timestamp_s + dt_s

        aircraft: Dict[str, AircraftState] = {}
        for ac in snapshot:
            route = routes.get(ac.callsign)
            if route:
                predicted_ac = self._predict_along_route(
                    ac, route, dt_s, predicted_time_s, profiles.get(ac.callsign)
                )
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
        profile: Optional[Dict] = None,
    ) -> AircraftState:
        """Predict one aircraft's state by flying its known route.

        Horizontal: ``advance_along_route`` at the aircraft's current
        ground speed -- turns at each waypoint exactly where the route
        says to, and stops exactly at the final waypoint
        (``cap_at_route_end=True``) rather than projecting straight
        past it if the horizon extends beyond the filed route: a
        *predicted* trajectory has no business showing an aircraft
        flying beyond its destination/transfer point just because the
        prediction horizon is longer than the remaining route. (This
        differs deliberately from ``MockConnector``'s own use of the
        same function, which does NOT cap -- an actually-simulated
        aircraft that outruns its filed route keeps flying, since that
        is real simulated motion, not a bounded prediction.)

        Vertical: two cases.

        - No profile, or a profile that isn't "LANDING" (e.g.
          "OVERFLIGHT", which holds cruise level by design): same
          linear ``vertical_speed_fpm`` extrapolation as
          ``TrajectoryEngine`` (reuses ``predict_constant_velocity``
          directly).
        - ``profile["flight_type"] == "LANDING"``: altitude is derived
          from the *same* Top of Descent physics
          ``MockConnector`` actually flies
          (``top_of_descent_distance_nm``), not a flat extrapolation of
          whatever ``vertical_speed_fpm`` happened to be at the
          snapshot instant. This is what prevents the false-positive
          hotspots a naive constant-vertical-speed prediction produces
          for a descending aircraft: a "still level" assumption
          overstates its predicted altitude at the horizon (understating
          how much vertical separation it actually gains by then), and
          a "still descending at the same signed rate held forever"
          assumption undershoots (predicting it below the ground/past
          its actual level-off at FL000). Computed once from the
          aircraft's *current* altitude/ground speed (the correct
          reference point whether it's still cruising -- TOD is still
          ahead of it -- or already mid-descent -- it is, by
          definition, sitting exactly at its own TOD point right now,
          so this reproduces its real remaining profile exactly): the
          predicted altitude ramps linearly from current altitude at
          (or before) TOD down to 0 ft at the final waypoint, mirroring
          `MockConnector._propagate_positions`'s tick-by-tick descent
          exactly, just evaluated directly at the horizon distance
          instead of stepped tick-by-tick.
        """
        distance_nm = ac.ground_speed_kt * (dt_s / 3600.0)
        result = advance_along_route(
            ac.lat, ac.lon, ac.heading_deg, route, distance_nm, cap_at_route_end=True
        )

        if profile is not None and profile.get("flight_type") == "LANDING":
            vertical_rate_fpm = profile.get("vertical_rate_fpm", 2000.0)
            tod_distance_nm = top_of_descent_distance_nm(
                ac.altitude_ft, ac.ground_speed_kt, vertical_rate_fpm
            )
            distance_to_end_nm = remaining_route_distance_nm(
                result.lat, result.lon, result.remaining_waypoints
            )
            if tod_distance_nm <= 0:
                predicted_altitude_ft = 0.0 if distance_to_end_nm <= 0 else ac.altitude_ft
                predicted_vs_fpm = 0.0
            else:
                ramp_frac = max(0.0, min(1.0, distance_to_end_nm / tod_distance_nm))
                predicted_altitude_ft = ac.altitude_ft * ramp_frac
                predicted_vs_fpm = -vertical_rate_fpm if distance_to_end_nm < tod_distance_nm else 0.0
        else:
            # Reuse the shared vertical/altitude math verbatim; only the
            # horizontal position and heading differ from dead reckoning.
            dead_reckoned = predict_constant_velocity(ac, dt_s, predicted_time_s)
            predicted_altitude_ft = dead_reckoned.altitude_ft
            predicted_vs_fpm = ac.vertical_speed_fpm

        return AircraftState(
            callsign=ac.callsign,
            lat=result.lat,
            lon=result.lon,
            altitude_ft=predicted_altitude_ft,
            ground_speed_kt=ac.ground_speed_kt,
            heading_deg=result.heading_deg,
            vertical_speed_fpm=predicted_vs_fpm,
            aircraft_type=ac.aircraft_type,
            timestamp_s=predicted_time_s,
        )
