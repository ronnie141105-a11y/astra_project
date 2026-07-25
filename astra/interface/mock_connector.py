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

Async streaming (real-time dashboard)
--------------------------------------
`stream()` (near the bottom of this class) is an async generator that
turns the same `poll()` tick this class has always used into a
real-time asyncio telemetry feed: it calls `poll()` on a wall-clock
timer (`asyncio.sleep`) and yields the resulting `TrafficSnapshot`, at
a configurable rate (Hz).

This is purely an additional way to drive the connector -- every
synchronous method above (`poll()`, `connect()`, `create_aircraft()`,
the Scenario Builder passthroughs, etc.) is unchanged and still works
exactly as before; `stream()` just calls them on a timer. It exists so
`astra.pipeline.AsyncPipeline` and `astra.dashboard.server` (FastAPI +
WebSockets) can `async for` frames instead of owning a manual poll
loop plus `time.sleep()` on a background thread, the way the old Flask
dashboard's `main.py` loop did.
"""

import asyncio
import time
from dataclasses import dataclass
from threading import Lock
from typing import AsyncIterator, Dict, List, Optional, Tuple

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.route_following import (
    advance_along_route,
    remaining_route_distance_nm,
    top_of_descent_distance_nm,
)
from astra.utils.geodesy import bearing_deg, move_position
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: Landing profile: descent rate (ft/min) used both to compute the
#: dynamic Top of Descent point (`top_of_descent_distance_nm`) and to
#: actually fly the descent once past it. ~2,000 ft/min is a typical
#: continuous-descent rate.
_LANDING_VERTICAL_RATE_FPM = 2000.0
#: Both profiles: seconds after passing/reaching the final waypoint at
#: which a "LANDING" or "OVERFLIGHT" aircraft is automatically
#: despawned and removed from the simulation entirely (map + aircraft
#: table). 3 minutes gives an operator a moment to see the aircraft
#: land/overfly before it disappears, without leaving it stuck on
#: screen indefinitely.
_ROUTE_END_DESPAWN_S = 180.0
#: Valid values for `_AircraftRecord.flight_type` / `create_aircraft`'s
#: `flight_type` argument (besides `None`, meaning "no forced profile").
VALID_FLIGHT_TYPES = ("LANDING", "OVERFLIGHT")

#: Recommended bounds for `stream()`'s `hz` argument -- 1-5 Hz matches
#: standard ATC radar-style HMI refresh rates. Values outside this
#: range are still accepted (e.g. a test wanting a very fast tick) but
#: are logged once at WARNING rather than rejected outright.
_RECOMMENDED_MIN_HZ = 1.0
_RECOMMENDED_MAX_HZ = 5.0


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
    #: Remaining waypoints (lat, lon) to fly toward, in order, when this
    #: aircraft was spawned onto an airway. `None`/empty = plain
    #: constant-heading dead reckoning (the original mock behaviour).
    route_waypoints: Optional[List[Tuple[float, float]]] = None
    #: Index into `route_waypoints` of the waypoint currently being flown to.
    route_index: int = 0
    #: Scenario Builder "spawn aircraft" profile -- only meaningful
    #: alongside `route_waypoints`. `"LANDING"`: descend to FL100 at 40
    #: NM from the final waypoint, reach FL000 exactly at it, then stop
    #: (aircraft has landed). `"OVERFLIGHT"`: hold cruise flight level
    #: across the whole route, then despawn 5 minutes after passing the
    #: final waypoint. `None`: legacy behaviour -- follow the route with
    #: whatever altitude/vertical-speed the aircraft already has, no
    #: forced descent or despawn.
    flight_type: Optional[str] = None
    #: Simulation time (`self._simt`) at which `route_waypoints` first
    #: became empty -- i.e. the final waypoint was reached. `None` until
    #: then. Drives the "LANDING" stop and the "OVERFLIGHT" 5-minute
    #: despawn timer, both of which need "how long ago did this aircraft
    #: finish its route", not just "has it finished".
    route_completed_at_simt: Optional[float] = None


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
        self._speed_multiplier: float = 1.0
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
        """Advance the mock simulation by one tick (`sim_step_s` scaled by
        the current speed multiplier -- see `set_speed_multiplier()`).

        If the simulation is running (i.e. `OP` has been sent), each
        aircraft's position is propagated via great-circle dead reckoning.
        If paused (HOLD/PAUSE), aircraft remain stationary but
        `latest_snapshot()` is still updated to reflect the current state.
        """
        with self._lock:
            if self._running:
                effective_step_s = self._sim_step_s * self._speed_multiplier
                self._simt += effective_step_s
                self._propagate_positions(effective_step_s)
            # Always rebuild the snapshot so callers see the latest static
            # state even if the clock is paused.
            self._latest_snapshot = self._build_snapshot()

    def set_speed_multiplier(self, multiplier: float) -> None:
        """Scale how much simulated time each `poll()` call advances by.

        E.g. `multiplier=5` with the default 1.0s `sim_step_s` means each
        `poll()` (still called once per real-world `poll_interval_s`, by
        whatever loop is driving this connector) advances the simulation
        clock by 5 simulated seconds instead of 1 -- the traffic, tracks,
        and forecasts all evolve 5x faster on screen without changing how
        often the dashboard actually refreshes. Purely a multiplier on
        `poll()`'s own tick; `step_simulation()` (explicit single/multi-tick
        stepping, e.g. the Scenario Builder's "step" action) is
        unaffected, since that already takes an explicit tick count.

        Args:
            multiplier: Must be > 0. 1.0 is real-time (the default).
        """
        if multiplier <= 0:
            raise ValueError("multiplier must be positive")
        with self._lock:
            self._speed_multiplier = multiplier

    def get_speed_multiplier(self) -> float:
        """Current `poll()` speed multiplier (see `set_speed_multiplier`)."""
        with self._lock:
            return self._speed_multiplier

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
        route_waypoints: Optional[List[Tuple[float, float]]] = None,
        flight_type: Optional[str] = None,
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
            heading_deg: Initial true heading, degrees. Ignored (overridden
                by the bearing to the first waypoint) when
                `route_waypoints` is given.
            altitude_ft: Initial altitude, feet AMSL.
            speed_kt: Initial ground speed, knots.
            route_waypoints: Optional `[(lat, lon), ...]` airway to follow
                -- see `_advance_along_route()`. `None`/empty falls back
                to plain constant-heading dead reckoning.
            flight_type: Optional Scenario Builder spawn profile, one of
                `VALID_FLIGHT_TYPES` ("LANDING"/"OVERFLIGHT"). Only
                meaningful alongside `route_waypoints` -- ignored
                (logged once) otherwise, since both profiles are defined
                relative to "the final waypoint". See
                `_AircraftRecord.flight_type`'s docstring for what each
                one does.

        Raises:
            ValueError: `flight_type` is not `None` and not one of
                `VALID_FLIGHT_TYPES`.
        """
        if flight_type is not None and flight_type not in VALID_FLIGHT_TYPES:
            raise ValueError(f"flight_type must be one of {VALID_FLIGHT_TYPES} or None, got {flight_type!r}")
        if flight_type is not None and not route_waypoints:
            _LOG.warning(
                "flight_type=%r given without route_waypoints; ignoring (no route end to profile against).",
                flight_type,
            )
            flight_type = None

        normalized_route: Optional[List[Tuple[float, float]]] = None
        initial_heading = heading_deg % 360.0
        if route_waypoints:
            normalized_route = [(float(wp_lat), float(wp_lon)) for wp_lat, wp_lon in route_waypoints]
            initial_heading = bearing_deg(lat, lon, normalized_route[0][0], normalized_route[0][1])

        record = _AircraftRecord(
            callsign=callsign.upper(),
            aircraft_type=aircraft_type.upper(),
            lat=lat,
            lon=lon,
            heading_deg=initial_heading,
            altitude_ft=altitude_ft,
            ground_speed_kt=speed_kt,
            vertical_speed_fpm=0.0,
            route_waypoints=normalized_route,
            route_index=0,
            flight_type=flight_type,
        )
        with self._lock:
            self._aircraft[callsign.upper()] = record
        _LOG.debug(
            "MockConnector: created %s (%s) at (%.4f, %.4f) FL%.0f%s%s",
            callsign.upper(),
            aircraft_type.upper(),
            lat,
            lon,
            altitude_ft / 100.0,
            f", following {len(normalized_route)}-point route" if normalized_route else "",
            f" [{flight_type}]" if flight_type else "",
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
                    "on_route": bool(rec.route_waypoints),
                    "flight_type": rec.flight_type,
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

    def get_route(self, callsign: str) -> Optional[List[Tuple[float, float]]]:
        """Return one aircraft's remaining filed/cleared route, if any.

        This exposes the *same* route data ``_advance_along_route()``
        already consumes each tick -- i.e. information legitimately known
        right now (the aircraft's current flight plan / cleared airway),
        not anything about where the simulation will actually take it.
        ``RouteAwareTrajectoryEngine`` (Phase 2) uses this as its route
        source: it independently recomputes the aircraft's future
        position from this intent using the same
        ``astra.trajectory.route_following.advance_along_route()``
        function this connector uses, rather than ever reading this
        connector's own future simulated states.

        Args:
            callsign: Aircraft callsign -- case-insensitive.

        Returns:
            Ordered ``[(lat, lon), ...]`` of remaining waypoints ahead of
            the aircraft, or ``None`` if the aircraft is unknown or not
            currently following a route (plain dead-reckoning applies).
        """
        with self._lock:
            record = self._aircraft.get(callsign.upper())
            if record is None or not record.route_waypoints:
                return None
            return list(record.route_waypoints)

    def get_flight_profile(self, callsign: str) -> Optional[Dict]:
        """Return one aircraft's Scenario Builder spawn profile, if any.

        Companion to `get_route()`, for the same reason: exposes
        information legitimately known right now (this aircraft was
        spawned with a "LANDING" profile and is descending at this
        rate) so `RouteAwareTrajectoryEngine` can predict the *same*
        Top of Descent-aware altitude profile this connector is
        actually flying, instead of assuming a flat/constant vertical
        speed for the whole prediction horizon -- which is exactly what
        used to cause false-positive hotspots during timeline scrubbing
        (a descending aircraft was predicted as still at cruise
        altitude, or vice versa past its actual landing).

        Args:
            callsign: Aircraft callsign -- case-insensitive.

        Returns:
            ``None`` if the aircraft is unknown or has no forced
            profile (``flight_type is None``, including "OVERFLIGHT" --
            which holds cruise level throughout, so it needs no special
            prediction handling beyond the plain constant-vertical-speed
            case `TrajectoryEngine`/`RouteAwareTrajectoryEngine` already
            do). Otherwise ``{"flight_type": "LANDING", "vertical_rate_fpm": ...}``.
        """
        with self._lock:
            record = self._aircraft.get(callsign.upper())
            if record is None or record.flight_type != "LANDING":
                return None
            return {"flight_type": record.flight_type, "vertical_rate_fpm": _LANDING_VERTICAL_RATE_FPM}

    # ------------------------------------------------------------------
    # Async streaming (real-time dashboard)
    # ------------------------------------------------------------------

    async def stream(
        self,
        hz: float = 1.0,
        *,
        max_frames: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[TrafficSnapshot]:
        """Async-generate TrafficSnapshots in real time, at hz frames/second.

        Wraps this connector's own synchronous poll() + latest_snapshot()
        on a wall-clock asyncio.sleep() timer -- no new simulation logic,
        just a real-time cadence around the existing tick. Each yielded
        snapshot corresponds to exactly one poll() call, so the mock's
        simulation clock (self.simt) still advances by sim_step_s per
        frame, same as it always has; hz controls only how often that
        happens in wall-clock time, independent of sim_step_s.

        Intended consumer: astra.pipeline.AsyncPipeline.stream(), which
        runs the full trajectory/hotspot/complexity pipeline on each
        yielded snapshot and re-yields the result; astra.dashboard.server
        then broadcasts that over WebSockets.

        Args:
            hz: Frames per second to poll and yield at. Must be > 0.
                1-5 Hz is the recommended range for a human-watched ATC
                HMI; values outside that range are accepted but logged
                once at WARNING, not rejected.
            max_frames: If given, stop after yielding this many frames
                (mainly for tests).
            stop_event: If given, checked before each frame; the
                generator returns once it is set, instead of yielding
                another frame.

        Yields:
            One TrafficSnapshot per tick, oldest first.

        Raises:
            ValueError: hz is not positive.
        """
        if hz <= 0:
            raise ValueError(f"hz must be positive, got {hz}")
        if not (_RECOMMENDED_MIN_HZ <= hz <= _RECOMMENDED_MAX_HZ):
            _LOG.warning(
                "MockConnector.stream(): hz=%.2f is outside the recommended "
                "%.1f-%.1f Hz range for a live ATC HMI.",
                hz,
                _RECOMMENDED_MIN_HZ,
                _RECOMMENDED_MAX_HZ,
            )

        period_s = 1.0 / hz
        frames_yielded = 0
        next_tick = time.monotonic()
        while max_frames is None or frames_yielded < max_frames:
            if stop_event is not None and stop_event.is_set():
                return

            self.poll()
            snapshot = self.latest_snapshot()
            if snapshot is not None:
                yield snapshot
                frames_yielded += 1

            next_tick += period_s
            sleep_s = next_tick - time.monotonic()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
            else:
                next_tick = time.monotonic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _propagate_positions(self, dt_s: float) -> None:
        """Move all aircraft forward by `dt_s` seconds.

        Called inside `poll()`/`step()` while `self._lock` is already
        held, so this method must NOT acquire the lock itself. Also
        applies each aircraft's Scenario Builder spawn profile (see
        `_AircraftRecord.flight_type`):

        - Plain route (no profile) or no route at all: unchanged
          original behaviour -- follow the route (or dead-reckon) with
          whatever altitude/vertical-speed the aircraft already has.
          Never despawned automatically.
        - "LANDING": position is capped at the final waypoint (does not
          slide past it once reached -- see `advance_along_route`'s
          `cap_at_route_end`), ground speed is zeroed once there (it
          has landed, so it stops). Altitude follows a dynamic Top of
          Descent profile (`top_of_descent_distance_nm`): held level
          until exactly far enough out to descend continuously at
          `_LANDING_VERTICAL_RATE_FPM` and reach 0 ft (FL000) at the
          final waypoint -- no instant altitude jump the way a fixed
          "start descending at 40 NM regardless of cruise altitude"
          rule would produce for any aircraft not already near FL100.
        - "OVERFLIGHT": position continues straight past the final
          waypoint as normal (cruise flight level held throughout, no
          altitude override).

        Both "LANDING" and "OVERFLIGHT" aircraft are automatically
        despawned (removed entirely -- map and aircraft-table alike,
        since both simply reflect `list_aircraft()`/`_build_snapshot()`
        over `self._aircraft`) `_ROUTE_END_DESPAWN_S` after reaching/
        passing the final waypoint, so neither is left stuck on screen
        indefinitely.

        Args:
            dt_s: Time step in seconds.
        """
        despawn: List[Tuple[str, str]] = []
        for record in self._aircraft.values():
            already_completed_before_this_tick = record.route_completed_at_simt is not None
            # Convert speed to distance: gs_kt * (dt_s / 3600) gives NM.
            distance_nm = record.ground_speed_kt * (dt_s / 3600.0)
            if record.route_waypoints:
                self._advance_along_route(
                    record, distance_nm, cap_at_route_end=(record.flight_type == "LANDING")
                )
                if not record.route_waypoints and record.route_completed_at_simt is None:
                    record.route_completed_at_simt = self._simt
                    if record.flight_type == "LANDING":
                        # Landed -- stop moving. (Position is already
                        # pinned to the final waypoint by
                        # cap_at_route_end above.) Altitude is pinned to
                        # exactly 0 ft here too: the continuous descent
                        # below tracks it very close to 0 by this point,
                        # but "landed" should read as exactly FL000, not
                        # a few residual feet from tick discretization.
                        record.ground_speed_kt = 0.0
                        record.vertical_speed_fpm = 0.0
                        record.altitude_ft = 0.0
            else:
                # (Ground speed is already zeroed once a "LANDING"
                # aircraft has landed, so this is a no-op distance-0
                # move for it -- no special-case needed.)
                record.lat, record.lon = move_position(
                    record.lat, record.lon, record.heading_deg, distance_nm
                )

            if record.flight_type == "LANDING":
                # Ramp on every tick up through -- and including -- the
                # very tick that completes the route (hence checking
                # whether it was *already* completed before this tick,
                # not its state after), so the final tick still lands
                # exactly on FL000 instead of stopping one tick short of
                # it. Once landed, altitude/vs are already pinned to 0
                # above -- nothing further to compute.
                if not already_completed_before_this_tick:
                    distance_to_end_nm = remaining_route_distance_nm(
                        record.lat, record.lon, record.route_waypoints
                    )
                    tod_distance_nm = top_of_descent_distance_nm(
                        record.altitude_ft, record.ground_speed_kt, _LANDING_VERTICAL_RATE_FPM
                    )
                    if distance_to_end_nm <= tod_distance_nm:
                        # Past Top of Descent -- descend continuously at
                        # the configured rate (never an instant jump:
                        # this applies the same -fpm every tick from the
                        # moment TOD is crossed, so altitude and
                        # distance-to-go run down in lockstep all the
                        # way to the final waypoint).
                        record.vertical_speed_fpm = -_LANDING_VERTICAL_RATE_FPM
                        record.altitude_ft = max(
                            0.0, record.altitude_ft + record.vertical_speed_fpm * (dt_s / 60.0)
                        )
                    else:
                        # Still cruising -- more than TOD distance to go.
                        record.vertical_speed_fpm = 0.0
            elif record.flight_type == "OVERFLIGHT":
                # Cruise flight level held throughout -- no altitude
                # override.
                record.altitude_ft += record.vertical_speed_fpm * (dt_s / 60.0)
            else:
                # Altitude change: vs_fpm * (dt_s / 60) gives feet.
                record.altitude_ft += record.vertical_speed_fpm * (dt_s / 60.0)

            if (
                record.flight_type in VALID_FLIGHT_TYPES
                and record.route_completed_at_simt is not None
                and self._simt - record.route_completed_at_simt >= _ROUTE_END_DESPAWN_S
            ):
                despawn.append((record.callsign, record.flight_type))

        for callsign, flight_type in despawn:
            self._aircraft.pop(callsign, None)
            _LOG.debug(
                "MockConnector: despawned %s (%s, %.0fs past its final waypoint).",
                callsign,
                flight_type,
                _ROUTE_END_DESPAWN_S,
            )

    def _advance_along_route(
        self, record: "_AircraftRecord", distance_nm: float, cap_at_route_end: bool = False
    ) -> None:
        """Move `record` toward its remaining route waypoints.

        Thin wrapper around ``astra.trajectory.route_following.advance_along_route``
        (mutates `record` in place) -- kept as a method for call-site
        compatibility with `_propagate_positions()`. The route-stepping
        geometry itself lives in the shared module so that
        ``RouteAwareTrajectoryEngine`` (Phase 2) can reuse the exact same
        computation when predicting a route-following aircraft's future
        position, rather than risking a second, possibly-diverging
        implementation of "how a route is flown".

        Args:
            record: The aircraft to move (mutated in place).
            distance_nm: Distance available to travel this tick.
            cap_at_route_end: Passed straight through to
                ``advance_along_route`` -- ``True`` for a "LANDING"
                profile aircraft (stop exactly at the final waypoint
                rather than sliding past it), ``False`` (default) for
                everything else, matching prior behaviour.
        """
        result = advance_along_route(
            record.lat,
            record.lon,
            record.heading_deg,
            record.route_waypoints,
            distance_nm,
            cap_at_route_end=cap_at_route_end,
        )
        record.lat = result.lat
        record.lon = result.lon
        record.heading_deg = result.heading_deg
        record.route_waypoints = result.remaining_waypoints or None
        record.route_index = 0  # remaining_waypoints is already trimmed to "ahead"

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
