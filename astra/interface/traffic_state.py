"""
Simulator-agnostic traffic data model.

`AircraftState` and `TrafficSnapshot` are the ONLY objects that cross the
boundary out of `astra.interface` into the rest of the pipeline. They are
plain dataclasses with no BlueSky dependency whatsoever, by design (see
the `astra.interface` package docstring).

All values are stored in standard ATM units (feet, knots, fpm, degrees),
already converted by `bluesky_connector.py` -- nothing downstream should
ever need to know what units BlueSky itself uses internally.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional


@dataclass(frozen=True)
class AircraftState:
    """A single aircraft's state at one instant in simulation time.

    Frozen (immutable) because an AircraftState represents a historical
    fact ("aircraft X was here, doing this, at time T") that must never be
    mutated in place once observed -- later phases (e.g. trajectory
    prediction) build NEW AircraftState objects to represent predicted
    future states, rather than editing this one.
    """

    #: Aircraft callsign / flight identifier, e.g. "KL204". Always
    #: upper-case (BlueSky itself stores callsigns upper-case).
    callsign: str

    #: Latitude in decimal degrees (WGS84).
    lat: float

    #: Longitude in decimal degrees (WGS84).
    lon: float

    #: Altitude in feet above mean sea level.
    altitude_ft: float

    #: Ground speed in knots.
    ground_speed_kt: float

    #: True/track heading in degrees (0-360, 0 = true north).
    heading_deg: float

    #: Vertical speed in feet per minute. Positive = climbing.
    vertical_speed_fpm: float

    #: ICAO aircraft type designator, e.g. "A320". May be "UNKNOWN" if the
    #: type could not be determined (see `type_registry.py` for why this
    #: can happen with BlueSky's default aircraft-state stream).
    aircraft_type: str

    #: Simulation time in seconds since the BlueSky scenario started
    #: (BlueSky's own `simt`). This is NOT a wall-clock timestamp; it is
    #: the time base used consistently throughout the ASTRA pipeline so
    #: that trajectory prediction, hotspot timing, etc. all line up with
    #: the simulation rather than real time (which may run faster/slower
    #: than 1x in BlueSky).
    timestamp_s: float


@dataclass
class TrafficSnapshot:
    """The state of every aircraft in the simulation at one simulation time.

    This is intentionally a thin wrapper around a dict rather than a list:
    downstream code very frequently needs "give me aircraft X's state"
    (O(1) lookup) rather than "iterate position i" and keying by callsign
    also makes it trivial to detect aircraft that appeared/disappeared
    between two consecutive snapshots (set difference of `.callsigns()`).
    """

    #: Simulation time (seconds) this snapshot corresponds to.
    timestamp_s: float

    #: Mapping of callsign -> AircraftState for every aircraft present in
    #: the simulation at `timestamp_s`.
    aircraft: Dict[str, AircraftState] = field(default_factory=dict)

    def __len__(self) -> int:
        """Number of aircraft in this snapshot."""
        return len(self.aircraft)

    def __iter__(self) -> Iterator[AircraftState]:
        """Iterate over the AircraftState objects in this snapshot."""
        return iter(self.aircraft.values())

    def callsigns(self) -> List[str]:
        """Return all callsigns present in this snapshot.

        Returns:
            A list of callsign strings (no particular order is guaranteed,
            since the underlying storage is a dict).
        """
        return list(self.aircraft.keys())

    def get(self, callsign: str) -> Optional[AircraftState]:
        """Look up a single aircraft's state by callsign.

        Args:
            callsign: The callsign to look up. Matching is case-insensitive
                (the callsign is upper-cased before lookup, matching
                BlueSky's own convention).

        Returns:
            The matching AircraftState, or None if no aircraft with that
            callsign is present in this snapshot.
        """
        return self.aircraft.get(callsign.upper())

    def as_list(self) -> List[AircraftState]:
        """Return all aircraft states as a plain list.

        Returns:
            A list of AircraftState objects, useful wherever an
            order-agnostic, indexable collection is more convenient than
            the dict (e.g. building a numpy array of positions for
            clustering in Phase 3).
        """
        return list(self.aircraft.values())
