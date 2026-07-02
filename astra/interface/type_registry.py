"""
Callsign -> aircraft type registry.

Why this class exists
-----------------------
This is a documented, verified workaround for a real gap in BlueSky's
networking API, discovered by reading the installed `bluesky-simulator`
package source directly (not assumed):

* BlueSky's periodic aircraft-state broadcast (the "ACDATA" topic,
  published by `bluesky.simulation.screenio.ScreenIO.send_aircraft_data`)
  sends `simt, id, lat, lon, alt, tas, cas, gs, trk, vs, ...` -- but
  deliberately does NOT include the aircraft type string. This was
  confirmed both in the publisher itself and in BlueSky's own client-side
  proxy object (`bluesky.core.trafficproxy.TrafficProxy`), which mirrors
  exactly the same field set and likewise has no `type` field.
* Aircraft type is therefore only ever visible to a BlueSky *client* at
  the moment the client itself issues the `CRE` (create aircraft) stack
  command, since that command's arguments include the type.

Rather than reaching into BlueSky internals to patch this (which would
violate "BlueSky is ONLY the traffic simulator" -- we are not allowed to
modify or extend BlueSky's own classes), `TypeRegistry` simply remembers
the type for every aircraft ASTRA itself creates, and falls back to a
configurable "UNKNOWN" sentinel for aircraft that already existed in the
simulation before ASTRA connected (e.g. aircraft created by a hand-written
.scn scenario file rather than by ASTRA).
"""

from threading import Lock
from typing import Dict

#: Sentinel aircraft type used when no type is known for a callsign.
UNKNOWN_TYPE = "UNKNOWN"


class TypeRegistry:
    """Thread-safe callsign -> aircraft type cache.

    Thread safety matters because, from Phase 7 onward, the dashboard may
    poll the BlueSky connector from a different thread than the one
    issuing resolution commands (Phase 6); a plain dict would risk a race
    condition between `register()` and `lookup()`.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._types: Dict[str, str] = {}
        self._lock = Lock()

    def register(self, callsign: str, aircraft_type: str) -> None:
        """Record the type of an aircraft, e.g. right before/after issuing CRE.

        Args:
            callsign: The aircraft's callsign (case-insensitive).
            aircraft_type: ICAO type designator, e.g. "A320".
        """
        with self._lock:
            self._types[callsign.upper()] = aircraft_type.upper()

    def lookup(self, callsign: str) -> str:
        """Look up the type of an aircraft.

        Args:
            callsign: The aircraft's callsign (case-insensitive).

        Returns:
            The registered ICAO type designator, or `UNKNOWN_TYPE` if this
            callsign was never registered (e.g. it pre-existed in the
            simulation before ASTRA connected to it).
        """
        with self._lock:
            return self._types.get(callsign.upper(), UNKNOWN_TYPE)

    def forget(self, callsign: str) -> None:
        """Remove a callsign from the registry, e.g. once it leaves the sim.

        Args:
            callsign: The aircraft's callsign (case-insensitive).
        """
        with self._lock:
            self._types.pop(callsign.upper(), None)
