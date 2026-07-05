"""
Offline mock connector for ASTRA.

Purpose
--------
`MockConnector` lets the entire ASTRA pipeline run without a BlueSky
process running. Its two concrete uses in a thesis project are:

1. **Offline development**: build and debug Phases 2–7 on a laptop with
   no BlueSky installed. The mock generates synthetic traffic that
   behaves plausibly — aircraft move at constant heading and speed until
   a clearance command changes their state — so the trajectory predictor,
   DBSCAN clusterer, complexity scorer and dashboard all see realistic
   input shapes.

2. **Reproducible unit testing**: tests can construct a `MockConnector`
   with a precisely-defined set of aircraft, call `poll()` a known number
   of times, and assert exact outputs from later pipeline stages. This is
   impossible with a live BlueSky connection (timing is non-deterministic)
   and awkward with the real connector (it requires mocking ZMQ).

Design decisions
-----------------
* `MockConnector` satisfies `ConnectorProtocol` (structural subtyping via
  `typing.Protocol`). No explicit inheritance from `ConnectorProtocol` is
  needed and none is declared — but the shapes match exactly.
* Position propagation uses `astra.utils.geodesy.move_position`, which is
  the same dead-reckoning function Phase 2 will use for trajectory
  prediction. This keeps the mock's behaviour consistent with what the
  trajectory predictor will later produce for a "constant heading/speed"
  aircraft.
* Internal aircraft state is stored in a mutable `_AircraftRecord` dataclass
  rather than the frozen `AircraftState`. The conversion to the immutable
  `AircraftState` happens only when `latest_snapshot()` is called.
* Stack command parsing is intentionally minimal: only the commands needed
  for Phase 1–6 testing are handled. Unknown commands are logged and
  ignored rather than raising exceptions, mirroring the lenient behaviour
  of a real ATC automation system.

Stack commands understood
--------------------------
``CRE  callsign,type,lat,lon,hdg,alt_ft,spd_kt``  Create aircraft.
``DEL  callsign``                                   Delete aircraft.
``OP``                                             Resume simulation clock.
``HOLD`` / ``PAUSE``                               Pause simulation clock.
``SPD  callsign  value_kt``                        Set ground speed (knots).
``ALT  callsign  value_ft``                        Set altitude (feet).
``HDG  callsign  value_deg``                       Set heading (degrees).
``VS   callsign  value_fpm``                       Set vertical speed (fpm).
"""

from dataclasses import dataclass
from threading import Lock
from typing import Dict, List, Optional

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.utils.geodesy import move_position
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)


@dataclass
class _AircraftRecord:
    """Mutable internal state for one aircraft in the mock simulation.

    All fields use standard ATM units (feet, knots, fpm, degrees),
    matching `AircraftState`. Mutable so that `poll()` can propagate
    positions in-place without allocating new objects each tick.
    """

    callsign: str
    aircraft_type: str
    lat: float
    lon: float
    heading_deg: float
    altitude_ft: float
    ground_speed_kt: float
    vertical_speed_fpm: float


class MockConnector:
    """Offline, in-process traffic simulator implementing `ConnectorProtocol`.

    Aircraft move at constant heading and ground speed (straight-line
    great-circle dead reckoning). Altitude changes at the configured
    `vertical_speed_fpm`. All state changes are applied by `poll()` once
    per `sim_step_s`.
    """

    def __init__(self, sim_step_s: float = 1.0) -> None:
        """Create an empty mock connector.

        Args:
            sim_step_s: How many simulation seconds to advance on each
                call to `poll()`. Defaults to 1.0 s, matching the
                default `ASTRAConfig.poll_interval_s`. Increase this
                (e.g. to 30.0) to fast-forward through a scenario during
                testing.
        """
        if sim_step_s <= 0:
            raise ValueError("sim_step_s must be positive")

        self._sim_step_s: float = sim_step_s
        self._simt: float = 0.0
        self._running: bool = False
        self._active: bool = False  # True once connect() called
        self._aircraft: Dict[str, _AircraftRecord] = {}
        self._latest_snapshot: Optional[TrafficSnapshot] = None
        self._lock = Lock()

    # ------------------------------------------------------------------
    # ConnectorProtocol implementation
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Activate the mock (no network I/O).

        Sets `has_active_node()` to True. The simulation clock is NOT
        automatically started; call `send_command("OP")` to start it,
        matching the behaviour expected from a real BlueSky session.
        """
        with self._lock:
            self._active = True
        _LOG.info("MockConnector: connected (offline mode, no BlueSky needed).")

    def poll(self) -> None:
        """Advance the mock simulation by one `sim_step_s` tick.

        If the simulation is running (i.e. `OP` has been sent), each
        aircraft's position is propagated via great-circle dead reckoning.
        If paused (HOLD/PAUSE), aircraft remain stationary but
        `latest_snapshot()` is still updated to reflect the current state.
        """
        with self._lock:
            if self._running:
                self._simt += self._sim_step_s
                self._propagate_positions(self._sim_step_s)
            # Always rebuild the snapshot so callers see the latest static
            # state even if the clock is paused.
            self._latest_snapshot = self._build_snapshot()

    def latest_snapshot(self) -> Optional[TrafficSnapshot]:
        """Return the current traffic state.

        Returns:
            A TrafficSnapshot with one `AircraftState` per aircraft, or
            None if `poll()` has never been called.
        """
        with self._lock:
            return self._latest_snapshot

    def has_active_node(self) -> bool:
        """True once `connect()` has been called.

        Returns:
            Whether the mock is in an active (ready) state.
        """
        with self._lock:
            return self._active

    def send_command(self, command_text: str) -> None:
        """Parse and execute a BlueSky-format stack command.

        Recognised commands are listed in the module docstring. Unknown
        commands are logged at DEBUG level and silently ignored.

        Args:
            command_text: Stack command string, e.g. "SPD KL204 250".
        """
        text = command_text.strip()
        if not text:
            return
        # The first whitespace-delimited token is the command keyword.
        parts = text.split(None, 1)
        keyword = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

        handler = self._COMMAND_HANDLERS.get(keyword)
        if handler is None:
            _LOG.debug("MockConnector: ignoring unrecognised command '%s'", keyword)
            return
        handler(self, rest)

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
        """Insert an aircraft directly into the mock state.

        Bypasses command parsing for direct, type-safe setup. Equivalent
        to `send_command("CRE callsign,type,lat,lon,hdg,alt,spd")` but
        does not require string formatting.

        Args:
            callsign: Aircraft callsign, e.g. "KL204".
            aircraft_type: ICAO type designator, e.g. "A320".
            lat: Initial latitude, decimal degrees.
            lon: Initial longitude, decimal degrees.
            heading_deg: Initial true heading, degrees.
            altitude_ft: Initial altitude, feet AMSL.
            speed_kt: Initial ground speed, knots.
        """
        record = _AircraftRecord(
            callsign=callsign.upper(),
            aircraft_type=aircraft_type.upper(),
            lat=lat,
            lon=lon,
            heading_deg=heading_deg % 360.0,
            altitude_ft=altitude_ft,
            ground_speed_kt=speed_kt,
            vertical_speed_fpm=0.0,
        )
        with self._lock:
            self._aircraft[callsign.upper()] = record
        _LOG.debug(
            "MockConnector: created %s (%s) at (%.4f, %.4f) FL%.0f",
            callsign.upper(),
            aircraft_type.upper(),
            lat,
            lon,
            altitude_ft / 100.0,
        )

    # ------------------------------------------------------------------
    # Convenience helpers (not part of ConnectorProtocol)
    # ------------------------------------------------------------------

    def remove_aircraft(self, callsign: str) -> None:
        """Remove an aircraft from the mock by callsign.

        Args:
            callsign: The callsign to remove (case-insensitive).
        """
        with self._lock:
            self._aircraft.pop(callsign.upper(), None)

    #: Fields `update_aircraft()` is allowed to set directly. Deliberately
    #: excludes `callsign` (renaming is a create+delete, not an update).
    _EDITABLE_FIELDS = frozenset(
        {
            "aircraft_type",
            "lat",
            "lon",
            "heading_deg",
            "altitude_ft",
            "ground_speed_kt",
            "vertical_speed_fpm",
        }
    )

    def update_aircraft(self, callsign: str, **fields) -> bool:
        """Directly set one or more fields on an existing aircraft.

        Added for the Scenario Builder HMI page (edit-in-place), which
        needs to change an aircraft's state without going through the
        stack-command mini-language `SPD`/`ALT`/`HDG`/`VS` handle one
        field at a time. Unknown field names are ignored rather than
        raising, matching this module's existing lenient-parsing style.

        Args:
            callsign: The aircraft to edit (case-insensitive).
            **fields: Any of `aircraft_type`, `lat`, `lon`, `heading_deg`,
                `altitude_ft`, `ground_speed_kt`, `vertical_speed_fpm`.
                `heading_deg` is normalised into `[0, 360)`.

        Returns:
            True if the aircraft existed and was updated, False if no
            aircraft with that callsign is present.
        """
        with self._lock:
            record = self._aircraft.get(callsign.upper())
            if record is None:
                return False
            for name, value in fields.items():
                if name not in self._EDITABLE_FIELDS or value is None:
                    continue
                if name == "heading_deg":
                    value = float(value) % 360.0
                elif name == "aircraft_type":
                    value = str(value).upper()
                setattr(record, name, value)
            return True

    def reset(self) -> None:
        """Clear all aircraft and reset the simulation clock to zero.

        Used by the Scenario Builder's "Reset" control. Leaves the
        simulation paused (matching the state right after `connect()`)
        so the operator can build up a new scene before pressing Resume.
        """
        with self._lock:
            self._aircraft.clear()
            self._simt = 0.0
            self._running = False
            self._latest_snapshot = self._build_snapshot()

    def step(self, ticks: int = 1) -> None:
        """Force `ticks` propagation step(s), regardless of run state.

        Used by the Scenario Builder's "single-step" control so an
        operator can advance the scenario one `sim_step_s` tick at a
        time -- including while otherwise paused -- and watch the
        pipeline (trajectory/hotspot/complexity/tracking/forecast) react
        cycle by cycle. Each call always advances real simulation time
        by a positive `sim_step_s`, so it is safe for downstream stages
        that assume strictly increasing timestamps (unlike, say, forcing
        a snapshot refresh at an unchanged timestamp would be).

        Args:
            ticks: Number of `sim_step_s` steps to advance. Defaults to 1.
        """
        with self._lock:
            for _ in range(max(1, ticks)):
                self._simt += self._sim_step_s
                self._propagate_positions(self._sim_step_s)
            self._latest_snapshot = self._build_snapshot()

    def list_aircraft(self) -> List[Dict]:
        """Return the current raw state of every aircraft, for the builder UI.

        Unlike `latest_snapshot()`, this does not require `poll()` to
        have been called first and does not go through `AircraftState`
        -- it is a direct, immediate read of the mock's internal state,
        so a freshly spawned/edited aircraft always shows up right away
        in the Scenario Builder's table even while the sim is paused.

        Returns:
            One dict per aircraft (order not guaranteed), each with
            `callsign`, `aircraft_type`, `lat`, `lon`, `heading_deg`,
            `altitude_ft`, `ground_speed_kt`, `vertical_speed_fpm`.
        """
        with self._lock:
            return [
                {
                    "callsign": rec.callsign,
                    "aircraft_type": rec.aircraft_type,
                    "lat": rec.lat,
                    "lon": rec.lon,
                    "heading_deg": rec.heading_deg,
                    "altitude_ft": rec.altitude_ft,
                    "ground_speed_kt": rec.ground_speed_kt,
                    "vertical_speed_fpm": rec.vertical_speed_fpm,
                }
                for rec in self._aircraft.values()
            ]

    def is_running(self) -> bool:
        """Whether the simulation clock is currently advancing (OP vs HOLD)."""
        with self._lock:
            return self._running

    def set_running(self, running: bool) -> None:
        """Start or pause the mock simulation clock directly.

        Args:
            running: True to start, False to pause.
        """
        with self._lock:
            self._running = running

    @property
    def simt(self) -> float:
        """Current simulation time in seconds."""
        with self._lock:
            return self._simt

    def aircraft_callsigns(self) -> List[str]:
        """Return the callsigns of all aircraft currently in the mock.

        Returns:
            List of callsign strings.
        """
        with self._lock:
            return list(self._aircraft.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _propagate_positions(self, dt_s: float) -> None:
        """Move all aircraft forward by `dt_s` seconds.

        Called inside `poll()` while `self._lock` is already held, so
        this method must NOT acquire the lock itself.

        Args:
            dt_s: Time step in seconds.
        """
        for record in self._aircraft.values():
            # Convert speed to distance: gs_kt * (dt_s / 3600) gives NM.
            distance_nm = record.ground_speed_kt * (dt_s / 3600.0)
            record.lat, record.lon = move_position(
                record.lat, record.lon, record.heading_deg, distance_nm
            )
            # Altitude change: vs_fpm * (dt_s / 60) gives feet.
            record.altitude_ft += record.vertical_speed_fpm * (dt_s / 60.0)

    def _build_snapshot(self) -> TrafficSnapshot:
        """Convert the current mutable internal state to an immutable snapshot.

        Called inside `poll()` while `self._lock` is already held.

        Returns:
            A new `TrafficSnapshot` reflecting the current aircraft states.
        """
        aircraft = {}
        for callsign, rec in self._aircraft.items():
            aircraft[callsign] = AircraftState(
                callsign=rec.callsign,
                lat=rec.lat,
                lon=rec.lon,
                altitude_ft=rec.altitude_ft,
                ground_speed_kt=rec.ground_speed_kt,
                heading_deg=rec.heading_deg,
                vertical_speed_fpm=rec.vertical_speed_fpm,
                aircraft_type=rec.aircraft_type,
                timestamp_s=self._simt,
            )
        return TrafficSnapshot(timestamp_s=self._simt, aircraft=aircraft)

    # ------------------------------------------------------------------
    # Stack command handlers (called by send_command dispatch table)
    # Each receives `self` and the remainder of the command string after
    # the keyword has been stripped.
    # ------------------------------------------------------------------

    def _handle_cre(self, args: str) -> None:
        """Handle: CRE callsign,type,lat,lon,hdg,alt_ft,spd_kt"""
        # BlueSky's CRE format uses comma separators with no spaces.
        tokens = [t.strip() for t in args.split(",")]
        if len(tokens) < 7:
            _LOG.warning(
                "MockConnector CRE: expected 7 comma-separated args, got: '%s'", args
            )
            return
        try:
            self.create_aircraft(
                callsign=tokens[0],
                aircraft_type=tokens[1],
                lat=float(tokens[2]),
                lon=float(tokens[3]),
                heading_deg=float(tokens[4]),
                altitude_ft=float(tokens[5]),
                speed_kt=float(tokens[6]),
            )
        except ValueError as exc:
            _LOG.warning("MockConnector CRE: failed to parse args '%s': %s", args, exc)

    def _handle_del(self, args: str) -> None:
        """Handle: DEL callsign"""
        callsign = args.strip().upper()
        if callsign:
            self.remove_aircraft(callsign)
            _LOG.debug("MockConnector: deleted %s", callsign)

    def _handle_op(self, _args: str) -> None:
        """Handle: OP (start/resume simulation)"""
        with self._lock:
            self._running = True
        _LOG.debug("MockConnector: simulation RUNNING")

    def _handle_hold(self, _args: str) -> None:
        """Handle: HOLD or PAUSE (pause simulation)"""
        with self._lock:
            self._running = False
        _LOG.debug("MockConnector: simulation PAUSED")

    def _handle_spd(self, args: str) -> None:
        """Handle: SPD callsign value_kt"""
        parts = args.split()
        if len(parts) < 2:
            _LOG.warning("MockConnector SPD: expected 'callsign value', got: '%s'", args)
            return
        callsign = parts[0].upper()
        try:
            speed_kt = float(parts[1])
        except ValueError:
            _LOG.warning("MockConnector SPD: bad value '%s'", parts[1])
            return
        with self._lock:
            if callsign in self._aircraft:
                self._aircraft[callsign].ground_speed_kt = speed_kt
            else:
                _LOG.warning("MockConnector SPD: unknown callsign '%s'", callsign)

    def _handle_alt(self, args: str) -> None:
        """Handle: ALT callsign value_ft"""
        parts = args.split()
        if len(parts) < 2:
            _LOG.warning("MockConnector ALT: expected 'callsign value', got: '%s'", args)
            return
        callsign = parts[0].upper()
        try:
            alt_ft = float(parts[1])
        except ValueError:
            _LOG.warning("MockConnector ALT: bad value '%s'", parts[1])
            return
        with self._lock:
            if callsign in self._aircraft:
                self._aircraft[callsign].altitude_ft = alt_ft
            else:
                _LOG.warning("MockConnector ALT: unknown callsign '%s'", callsign)

    def _handle_hdg(self, args: str) -> None:
        """Handle: HDG callsign value_deg"""
        parts = args.split()
        if len(parts) < 2:
            _LOG.warning("MockConnector HDG: expected 'callsign value', got: '%s'", args)
            return
        callsign = parts[0].upper()
        try:
            hdg_deg = float(parts[1]) % 360.0
        except ValueError:
            _LOG.warning("MockConnector HDG: bad value '%s'", parts[1])
            return
        with self._lock:
            if callsign in self._aircraft:
                self._aircraft[callsign].heading_deg = hdg_deg
            else:
                _LOG.warning("MockConnector HDG: unknown callsign '%s'", callsign)

    def _handle_vs(self, args: str) -> None:
        """Handle: VS callsign value_fpm"""
        parts = args.split()
        if len(parts) < 2:
            _LOG.warning("MockConnector VS: expected 'callsign value', got: '%s'", args)
            return
        callsign = parts[0].upper()
        try:
            vs_fpm = float(parts[1])
        except ValueError:
            _LOG.warning("MockConnector VS: bad value '%s'", parts[1])
            return
        with self._lock:
            if callsign in self._aircraft:
                self._aircraft[callsign].vertical_speed_fpm = vs_fpm
            else:
                _LOG.warning("MockConnector VS: unknown callsign '%s'", callsign)

    # Dispatch table: maps uppercase keyword → handler method.
    # Defined as a class-level dict after all handler methods are defined.
    _COMMAND_HANDLERS = {
        "CRE": _handle_cre,
        "DEL": _handle_del,
        "OP": _handle_op,
        "HOLD": _handle_hold,
        "PAUSE": _handle_hold,  # PAUSE is a BlueSky alias for HOLD
        "SPD": _handle_spd,
        "ALT": _handle_alt,
        "HDG": _handle_hdg,
        "VS": _handle_vs,
    }
