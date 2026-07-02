"""
Trajectory prediction engine.

The engine implements a deterministic, constant-velocity kinematic model:

    Horizontal  great-circle dead-reckoning at the aircraft's current
                ground_speed_kt and heading_deg.  Uses
                ``astra.utils.geodesy.move_position``, the same function
                that ``MockConnector.poll()`` uses — so the predicted
                positions are exactly consistent with what the mock
                connector would produce if the simulation ran forward by
                the same amount of time without any clearances.

    Vertical    linear extrapolation at the aircraft's current
                vertical_speed_fpm (feet per minute).

    Constant    heading, ground speed, and vertical speed are treated as
                constant throughout the prediction horizon.

Why constant velocity?
----------------------
For en-route cruise phases — the dominant scenario in ASTRA — aircraft
maintain constant heading, speed, and altitude for long stretches.  A
constant-velocity model is therefore both accurate and the correct
starting baseline before more complex models (wind-corrected, intent-
based) are introduced.  The thesis documents this explicitly as a
simplifying assumption and identifies acceleration and turning as future
work.

This also matches exactly the kinematic propagation used in
``MockConnector``, which means simulation outputs are mathematically
reproducible and directly verifiable: one TrajectoryEngine.predict() call
at horizon H minutes produces the same position as H*60 / sim_step_s
MockConnector poll() calls.

Reuse
-----
All infrastructure from Phase 1 is reused without modification:
- ``astra.interface.traffic_state.AircraftState`` — input and output type
- ``astra.interface.traffic_state.TrafficSnapshot``  — input type
- ``astra.utils.geodesy.move_position``  — horizontal displacement
- ``astra.utils.config.ASTRAConfig``     — horizon configuration
- ``astra.utils.logger``                 — logging
"""

from typing import Dict, List

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import move_position
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)


class TrajectoryEngine:
    """Deterministic constant-velocity trajectory predictor.

    Accepts a ``TrafficSnapshot`` and returns a ``PredictionResult``
    containing one ``PredictedSnapshot`` per configured horizon.

    Thread safety
    -------------
    ``TrajectoryEngine`` is stateless after construction — ``predict()``
    reads only the snapshot it receives and the config passed at init.
    It is therefore safe to call from multiple threads simultaneously,
    or to share a single instance across the whole ASTRA process.

    Example usage::

        engine = TrajectoryEngine(config)
        result = engine.predict(reader.current())
        at_15 = result.at(15)
        for ac in at_15:
            print(f"{ac.callsign}: ({ac.lat:.4f}, {ac.lon:.4f}) FL{ac.altitude_ft/100:.0f}")
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise the engine from the shared configuration.

        Args:
            config: Shared ASTRA configuration. Reads
                ``prediction_horizons_min`` (default [5, 10, 15, 30, 60])
                to know which horizons to compute.
        """
        self._config = config
        self._horizons: List[int] = sorted(config.prediction_horizons_min)
        _LOG.debug(
            "TrajectoryEngine initialised. Horizons: %s min", self._horizons
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def horizons_min(self) -> List[int]:
        """Sorted list of prediction horizons in minutes.

        Returns:
            A copy of the configured horizon list (sorted ascending).
        """
        return list(self._horizons)

    def predict(self, snapshot: TrafficSnapshot) -> PredictionResult:
        """Generate trajectory predictions for all configured horizons.

        Applies constant-velocity kinematics independently to every
        aircraft in ``snapshot``, producing one ``PredictedSnapshot``
        per horizon.

        If ``snapshot`` contains no aircraft, an empty ``PredictionResult``
        is returned — one ``PredictedSnapshot`` per horizon, each with an
        empty aircraft dict.

        Args:
            snapshot: Current observed traffic state from
                ``StateReader.current()`` or ``StateReader.poll()``.

        Returns:
            A ``PredictionResult`` containing one ``PredictedSnapshot``
            per configured horizon, ordered by ascending horizon value.
        """
        snapshots: Dict[int, PredictedSnapshot] = {}
        for h_min in self._horizons:
            snapshots[h_min] = self._predict_at_horizon(snapshot, h_min)

        result = PredictionResult(
            source_time_s=snapshot.timestamp_s,
            aircraft_count=len(snapshot),
            horizons_min=tuple(self._horizons),
            snapshots=snapshots,
        )
        _LOG.debug(
            "Prediction complete: %d aircraft × %d horizons from t=%.1f s",
            len(snapshot),
            len(self._horizons),
            snapshot.timestamp_s,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _predict_at_horizon(
        self, snapshot: TrafficSnapshot, horizon_min: int
    ) -> PredictedSnapshot:
        """Predict all aircraft states at a single time horizon.

        Args:
            snapshot:    Source traffic state.
            horizon_min: Time horizon in minutes.

        Returns:
            A ``PredictedSnapshot`` containing one predicted
            ``AircraftState`` per aircraft in ``snapshot``.
        """
        dt_s = horizon_min * 60.0
        predicted_time_s = snapshot.timestamp_s + dt_s

        aircraft: Dict[str, AircraftState] = {}
        for ac in snapshot:
            predicted_ac = self._predict_aircraft(ac, dt_s, predicted_time_s)
            aircraft[predicted_ac.callsign] = predicted_ac

        return PredictedSnapshot(
            horizon_min=horizon_min,
            source_time_s=snapshot.timestamp_s,
            predicted_time_s=predicted_time_s,
            aircraft=aircraft,
        )

    def _predict_aircraft(
        self,
        ac: AircraftState,
        dt_s: float,
        predicted_time_s: float,
    ) -> AircraftState:
        """Apply constant-velocity kinematics to one aircraft.

        Horizontal displacement uses great-circle dead-reckoning:
            distance_nm = ground_speed_kt × (dt_s / 3600)
            (lat, lon) = move_position(lat, lon, heading_deg, distance_nm)

        Vertical displacement uses linear extrapolation:
            Δalt_ft = vertical_speed_fpm × (dt_s / 60)

        Altitude is clamped to [0, ∞) — an aircraft cannot descend below
        ground level.  In practice this clamping is never triggered for
        en-route cruise aircraft within a 60-minute horizon.

        All state fields not directly affected by kinematics (callsign,
        aircraft_type, ground_speed_kt, heading_deg, vertical_speed_fpm)
        are carried forward unchanged, reflecting the constant-velocity
        assumption.

        Args:
            ac:                Current observed aircraft state.
            dt_s:              Prediction horizon in seconds.
            predicted_time_s:  Simulation timestamp for the predicted state.

        Returns:
            A new ``AircraftState`` (frozen) representing the aircraft's
            predicted position and altitude at ``predicted_time_s``.
        """
        # ── Horizontal: great-circle dead-reckoning ──────────────────
        distance_nm = ac.ground_speed_kt * (dt_s / 3600.0)
        new_lat, new_lon = move_position(
            ac.lat, ac.lon, ac.heading_deg, distance_nm
        )

        # ── Vertical: linear altitude extrapolation ──────────────────
        # vertical_speed_fpm is in feet/minute; dt_s is in seconds.
        # Δalt = vs_fpm × (dt_s / 60)  [ft]
        new_alt_ft = ac.altitude_ft + ac.vertical_speed_fpm * (dt_s / 60.0)
        new_alt_ft = max(0.0, new_alt_ft)   # clamp to ground level

        # ── Build predicted state (reuses AircraftState directly) ────
        return AircraftState(
            callsign=ac.callsign,
            lat=new_lat,
            lon=new_lon,
            altitude_ft=new_alt_ft,
            ground_speed_kt=ac.ground_speed_kt,           # constant
            heading_deg=ac.heading_deg,                    # constant
            vertical_speed_fpm=ac.vertical_speed_fpm,     # constant
            aircraft_type=ac.aircraft_type,
            timestamp_s=predicted_time_s,
        )
