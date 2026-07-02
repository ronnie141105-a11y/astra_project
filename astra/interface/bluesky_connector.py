"""
BlueSky network adapter.

This module contains the ONLY code in the whole ASTRA codebase that talks
to BlueSky's networking layer directly. Everything it learns from BlueSky
is immediately translated into the simulator-agnostic `TrafficSnapshot` /
`AircraftState` model from `traffic_state.py` before leaving this module.

Implementation notes (how this was verified)
-----------------------------------------------
BlueSky's client/server networking code changed substantially in recent
releases (it now uses a "shared state" model rather than the
event()/stream() callback pattern shown in older third-party tutorials).
Rather than guessing, this module was written by installing the actual
current `bluesky-simulator` PyPI release and reading its source directly:

* `bluesky.network.client.Client` / `bluesky.network.node.Node` define the
  connection (`connect(hostname, recv_port, send_port)`) and the
  non-blocking per-tick `update()` method.
* `bluesky.simulation.screenio.ScreenIO.send_aircraft_data` is the actual
  function that publishes the `ACDATA` topic, at 5 Hz, with payload keys
  `simt, id, lat, lon, alt, tas, cas, gs, trk, vs, ...`. Units are
  BlueSky's internal SI units: metres, metres/second, except `trk`, which
  is already in degrees.
* `ACDATA` is registered as a BlueSky "state_publisher" (shared-state)
  topic. Subscribing to it via `self.subscribe('ACDATA').connect(callback)`
  means `callback` is invoked with a single argument: a `Store` object
  (an attribute-bag, NOT a dict -- so `store.lat`, not `store['lat']`)
  that BlueSky keeps internally updated to mirror the latest full ACDATA
  payload (confirmed by tracing `bluesky.network.sharedstate`).
* Sending a clearance to BlueSky is done by queuing a stack command with
  `bluesky.stack.stack(text)`, which is then transmitted to the active
  simulation node the next time `update()` (this class's `poll()`) runs,
  via `bluesky.stack.clientstack.process()` / `forward()`.

This module targets bluesky-simulator's current networking layer. If a
future BlueSky release changes these internals again, this is the single
file that needs to change.
"""

from typing import Optional

try:
    from bluesky.network.client import Client
    from bluesky.stack import stack as bs_queue_command
except ImportError as exc:  # pragma: no cover - environment guidance only
    raise ImportError(
        "The 'bluesky-simulator' package is required to run the ASTRA "
        "interface layer (it is not required to import other astra "
        "sub-packages). Install it with:\n\n"
        "    pip install bluesky-simulator\n\n"
        "and start the simulator, headless, in a separate process with:\n\n"
        "    python -m bluesky --headless\n"
    ) from exc

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.interface.type_registry import TypeRegistry
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger
from astra.utils.units import meters_to_feet, mps_to_fpm, mps_to_knots

_LOG = get_logger(__name__)


class BlueSkyConnector(Client):
    """Adapter between a running BlueSky simulation and ASTRA's data model.

    Subclasses `bluesky.network.client.Client` directly (this is BlueSky's
    own intended extension point for external tools -- the same base
    class BlueSky's own console/QtGL clients use), and overrides nothing
    except adding an ACDATA subscription. All BlueSky-specific decoding
    happens in the private `_on_acdata` callback.
    """

    def __init__(self, config: ASTRAConfig, type_registry: TypeRegistry) -> None:
        """Construct the connector. Does NOT connect to BlueSky yet.

        Args:
            config: Shared ASTRA configuration (host/ports, etc.).
            type_registry: Registry used to recover aircraft type, which
                BlueSky's ACDATA stream does not itself carry (see
                `type_registry.py` for why).
        """
        super().__init__()
        self._config = config
        self._type_registry = type_registry
        self._latest_snapshot: Optional[TrafficSnapshot] = None

        # Subscribing here (before connect()) is safe: BlueSky's Node base
        # class creates its ZMQ sockets in its own __init__, which has
        # already run via super().__init__() above.
        self.subscribe("ACDATA").connect(self._on_acdata)

    def connect(self) -> None:
        """Open the network connection to the configured BlueSky instance.

        Satisfies `ConnectorProtocol.connect()`. Internally calls the
        BlueSky `Client.connect(hostname, recv_port, send_port)` parent
        method via `super()`, which is the only correct way to reach it
        once this no-argument override is in place (calling `self.connect()`
        directly would recurse infinitely).

        Must be called once before `poll()` will receive any data.
        """
        _LOG.info(
            "Connecting to BlueSky at %s (recv=%d, send=%d)",
            self._config.bluesky_host,
            self._config.bluesky_recv_port,
            self._config.bluesky_send_port,
        )
        # super().connect() refers to bluesky.network.client.Client.connect(),
        # which accepts (hostname, recv_port, send_port) as keyword arguments.
        super().connect(
            hostname=self._config.bluesky_host,
            recv_port=self._config.bluesky_recv_port,
            send_port=self._config.bluesky_send_port,
        )

    def connect_to_simulator(self) -> None:
        """Deprecated alias for `connect()`. Kept for backward compatibility."""
        self.connect()

    def has_active_node(self) -> bool:
        """Whether a BlueSky simulation node has announced itself yet.

        Commands sent before a node is active have nowhere to go. main.py
        / higher layers should check this (or use `wait_for_simulator`)
        before issuing CRE / clearance commands.

        Returns:
            True once at least one simulation node is connected and
            selected as active.
        """
        return self.act_id is not None

    def poll(self) -> None:
        """Process any pending incoming/outgoing BlueSky network traffic.

        Non-blocking. Must be called frequently (see
        `ASTRAConfig.poll_interval_s`) for the connector to stay current;
        this single call is what both (a) receives new ACDATA updates and
        (b) flushes any commands queued via `send_command`.
        """
        self.update()

    def latest_snapshot(self) -> Optional[TrafficSnapshot]:
        """Return the most recently received TrafficSnapshot.

        Returns:
            The latest snapshot, or None if no ACDATA has been received
            yet (e.g. immediately after connecting, before BlueSky's first
            5 Hz publish tick).
        """
        return self._latest_snapshot

    def send_command(self, command_text: str) -> None:
        """Queue a raw BlueSky stack command for transmission.

        Args:
            command_text: A BlueSky stack command, e.g. "SPD KL204 250".
                Transmission to the simulation node happens on the next
                `poll()` call, not synchronously.
        """
        if not self.has_active_node():
            _LOG.warning(
                "send_command('%s') called with no active BlueSky node; "
                "command will be dropped. Call poll() until "
                "has_active_node() is True before sending commands.",
                command_text,
            )
            return
        bs_queue_command(command_text)

    def create_aircraft(
        self,
        callsign: str,
        aircraft_type: str,
        lat: float,
        lon: float,
        heading_deg: float,
        altitude_ft: float,
        speed_kt: float,
    ) -> None:
        """Create an aircraft in the simulation and remember its type.

        Convenience wrapper around BlueSky's `CRE` stack command
        (`CRE acid,type,lat,lon,hdg,alt,spd`), used mainly for local
        testing/demos of the pipeline without a hand-written scenario
        file. Also registers the type in the TypeRegistry, which is the
        only place this information can come from (see
        `type_registry.py`).

        Args:
            callsign: New aircraft's callsign, e.g. "TEST01".
            aircraft_type: ICAO type designator, e.g. "A320".
            lat: Initial latitude, decimal degrees.
            lon: Initial longitude, decimal degrees.
            heading_deg: Initial heading, degrees.
            altitude_ft: Initial altitude, feet.
            speed_kt: Initial speed, knots.
        """
        self._type_registry.register(callsign, aircraft_type)
        command = (
            f"CRE {callsign},{aircraft_type},{lat},{lon},"
            f"{heading_deg},{altitude_ft},{speed_kt}"
        )
        self.send_command(command)

    # ------------------------------------------------------------------
    # Internal: BlueSky -> ASTRA translation
    # ------------------------------------------------------------------
    def _on_acdata(self, data) -> None:
        """Callback invoked by BlueSky's client machinery on ACDATA updates.

        Args:
            data: A BlueSky `Store` object (attribute access, not dict
                access) mirroring the latest full ACDATA payload. Expected
                attributes: `simt` (float, s), `id` (list[str]), `lat`,
                `lon` (degrees), `alt` (m), `trk` (degrees), `gs` (m/s),
                `vs` (m/s). See module docstring for how this was
                verified against BlueSky's source.
        """
        callsigns = getattr(data, "id", []) or []
        lat = getattr(data, "lat", [])
        lon = getattr(data, "lon", [])
        alt_m = getattr(data, "alt", [])
        trk_deg = getattr(data, "trk", [])
        gs_mps = getattr(data, "gs", [])
        vs_mps = getattr(data, "vs", [])
        simt = float(getattr(data, "simt", 0.0))

        aircraft = {}
        for i, callsign in enumerate(callsigns):
            callsign = str(callsign).upper()
            aircraft[callsign] = AircraftState(
                callsign=callsign,
                lat=float(lat[i]),
                lon=float(lon[i]),
                altitude_ft=meters_to_feet(float(alt_m[i])),
                ground_speed_kt=mps_to_knots(float(gs_mps[i])),
                # BlueSky's ACDATA exposes track angle ('trk'), not
                # autopilot-selected heading ('hdg'); without wind the two
                # coincide. We surface this as "heading" in ASTRA's model
                # since that is the operationally relevant quantity for
                # complexity/clustering, while being explicit here about
                # the simplification for anyone reading this later.
                heading_deg=float(trk_deg[i]),
                vertical_speed_fpm=mps_to_fpm(float(vs_mps[i])),
                aircraft_type=self._type_registry.lookup(callsign),
                timestamp_s=simt,
            )

        self._latest_snapshot = TrafficSnapshot(timestamp_s=simt, aircraft=aircraft)
