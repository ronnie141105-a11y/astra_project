"""
BlueSky adapter layer.

Design decision: the anti-corruption layer
--------------------------------------------
This is the ONLY package in the codebase allowed to import BlueSky. Every
other ASTRA package (trajectory, hotspot, complexity, prediction,
resolution, dashboard) depends exclusively on the plain-Python data model
defined in `traffic_state.py` (`AircraftState`, `TrafficSnapshot`).

This matters for three concrete reasons in this project:

1. The brief is explicit that "BlueSky is ONLY the traffic simulator" and
   "all ASTRA logic must be implemented as an independent Python
   application". Confining BlueSky imports to one package is what makes
   that statement actually true and verifiable (it can be checked with a
   simple `grep -r "import bluesky" astra/` once the project is finished:
   it should only ever match files in this directory).
2. BlueSky's networking API has changed materially between releases (its
   client/server layer was substantially refactored to a shared-state
   model in recent versions). By isolating that surface here, a future
   BlueSky upgrade -- or swapping BlueSky for a different simulator
   entirely -- only requires changes inside `astra/interface`.
3. It makes the rest of the system trivially testable: unit tests for
   trajectory prediction, clustering, complexity, etc. can construct
   `TrafficSnapshot` objects by hand, with no simulator running at all.

Module overview
----------------
traffic_state.py     Simulator-agnostic data model (AircraftState,
                      TrafficSnapshot).
type_registry.py      Local cache of callsign -> aircraft type, because
                      BlueSky's broadcast aircraft-state stream (ACDATA)
                      does not include aircraft type (verified against the
                      installed bluesky-simulator source; see module
                      docstring for detail).
bluesky_connector.py  Thin subclass of bluesky.network.client.Client that
                      turns BlueSky's ACDATA shared-state stream into
                      TrafficSnapshot objects.
state_reader.py       Polling + bounded history buffer built on top of
                      BlueSkyConnector; this is the object the rest of the
                      pipeline (and main.py) actually talks to.
"""
