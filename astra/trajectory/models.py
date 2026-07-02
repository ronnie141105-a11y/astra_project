"""
Trajectory prediction data model.

Two types are defined here:

PredictedSnapshot
    The predicted state of all aircraft at one future time horizon.
    Mirrors the API of TrafficSnapshot so that Phase 3 (DBSCAN clustering)
    can treat predicted states with the same code paths as observed states.

PredictionResult
    The complete output of one TrajectoryEngine.predict() call:
    one PredictedSnapshot per configured horizon, all derived from a single
    source TrafficSnapshot.

Immutability contract
---------------------
Both types are declared ``frozen=True`` which prevents reassignment of any
attribute (e.g. ``result.snapshots = x`` raises FrozenInstanceError).
The ``aircraft`` dict and ``snapshots`` dict inside are not deep-frozen
because Python's standard library offers no lightweight mechanism for that.
They are treated as logically immutable by convention: nothing in the
ASTRA codebase modifies them after construction.

Unit conventions
----------------
All fields follow the same ATM units as the rest of ASTRA (feet, knots,
fpm, NM, decimal degrees). The ``predicted_time_s`` value uses BlueSky's
``simt`` time base, matching ``AircraftState.timestamp_s``.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from astra.interface.traffic_state import AircraftState


@dataclass(frozen=True)
class PredictedSnapshot:
    """Predicted positions of all aircraft at one future time horizon.

    This is the Phase 2 counterpart of
    ``astra.interface.traffic_state.TrafficSnapshot``. Where a
    ``TrafficSnapshot`` represents *observed* positions at simulation
    time T, a ``PredictedSnapshot`` represents *computed* positions at
    T + ``horizon_min`` × 60 seconds.

    The ``AircraftState`` objects stored here set their ``timestamp_s``
    field to ``predicted_time_s``, so they carry the same type and field
    layout as observed states. Phase 3 (DBSCAN clustering) can therefore
    pass predicted snapshots through the same distance-computation and
    clustering code that will later operate on live traffic.

    Attributes:
        horizon_min:       Prediction horizon in minutes (e.g. 5, 10, 15, 30, 60).
        source_time_s:     Simulation time (s) when the prediction was computed.
        predicted_time_s:  Simulation time (s) of the predicted state.
                           Always equals ``source_time_s + horizon_min * 60``.
        aircraft:          Predicted aircraft states, keyed by upper-case callsign.
                           Each ``AircraftState.timestamp_s == predicted_time_s``.
    """

    horizon_min: int
    source_time_s: float
    predicted_time_s: float
    aircraft: Dict[str, AircraftState]

    # ------------------------------------------------------------------
    # Accessor API — mirrors TrafficSnapshot for drop-in compatibility
    # ------------------------------------------------------------------

    def get(self, callsign: str) -> Optional[AircraftState]:
        """Return the predicted state for one aircraft, or None.

        Args:
            callsign: Aircraft callsign — case-insensitive.

        Returns:
            Matching ``AircraftState`` at this horizon, or ``None`` if the
            aircraft is not in the snapshot.
        """
        return self.aircraft.get(callsign.upper())

    def as_list(self) -> List[AircraftState]:
        """Return all predicted aircraft states as a plain list.

        Returns:
            Unordered list of ``AircraftState`` objects.
        """
        return list(self.aircraft.values())

    def callsigns(self) -> List[str]:
        """Return all callsigns present at this horizon.

        Returns:
            Unordered list of callsign strings.
        """
        return list(self.aircraft.keys())

    def __len__(self) -> int:
        """Number of aircraft predicted at this horizon."""
        return len(self.aircraft)

    def __iter__(self) -> Iterator[AircraftState]:
        """Iterate over predicted ``AircraftState`` objects."""
        return iter(self.aircraft.values())


@dataclass(frozen=True)
class PredictionResult:
    """Complete trajectory prediction from a single ``TrafficSnapshot``.

    Produced by ``TrajectoryEngine.predict(snapshot)``. Contains one
    ``PredictedSnapshot`` per horizon in
    ``ASTRAConfig.prediction_horizons_min`` (default: 5, 10, 15, 30, 60
    minutes).

    Typical access pattern::

        result = engine.predict(snapshot)
        at_15 = result.at(15)          # PredictedSnapshot at T+15 min
        for ac in at_15:               # iterate predicted AircraftStates
            print(ac.callsign, ac.lat, ac.lon, ac.altitude_ft)

    The primary consumer of ``PredictionResult`` is Phase 3 (hotspot
    detection), which will call the DBSCAN clusterer on each
    ``PredictedSnapshot`` independently.

    Attributes:
        source_time_s:   Simulation time (s) of the source ``TrafficSnapshot``.
        aircraft_count:  Number of aircraft in the source snapshot.
        horizons_min:    Sorted tuple of horizon values (minutes) that were
                         computed.  Derived from
                         ``ASTRAConfig.prediction_horizons_min``.
        snapshots:       Mapping ``{horizon_min: PredictedSnapshot}``.
    """

    source_time_s: float
    aircraft_count: int
    horizons_min: Tuple[int, ...]
    snapshots: Dict[int, PredictedSnapshot]

    # ------------------------------------------------------------------
    # Accessor API
    # ------------------------------------------------------------------

    def at(self, horizon_min: int) -> PredictedSnapshot:
        """Return the ``PredictedSnapshot`` at a specific horizon.

        Args:
            horizon_min: Horizon in minutes. Must be a value that appears
                in ``self.horizons_min`` (i.e. one of the horizons that
                was configured and computed).

        Returns:
            The ``PredictedSnapshot`` for that horizon.

        Raises:
            KeyError: If ``horizon_min`` was not computed (not in config).
        """
        try:
            return self.snapshots[horizon_min]
        except KeyError:
            raise KeyError(
                f"Horizon {horizon_min} min was not computed. "
                f"Available horizons: {list(self.horizons_min)}"
            )

    def horizon_list(self) -> List[int]:
        """Return all computed horizons as a sorted list.

        Returns:
            Sorted list of horizon values in minutes.
        """
        return list(self.horizons_min)
