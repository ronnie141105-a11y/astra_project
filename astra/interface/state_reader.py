"""
StateReader: the pipeline-facing entry point of Phase 1.

Role in the pipeline
---------------------
`StateReader` is the single object every other ASTRA module (trajectory,
hotspot, complexity, prediction, resolution, dashboard) depends on for
traffic data. Nothing outside `astra.interface` should ever hold a direct
reference to a connector object. This rule creates a clear architectural
seam:

    BlueSky / MockConnector
            |
            v
    StateReader              <- everything else imports this
            |
            v
    TrafficSnapshot / AircraftState

Design decision — dependency injection
----------------------------------------
The previous version of this class constructed a `BlueSkyConnector`
internally. That was convenient to write but made three things impossible:

1. Running any downstream phase (trajectory, hotspot, …) in offline
   development without a live BlueSky process.
2. Writing unit tests for downstream phases, which need a
   `StateReader` producing a precisely-controlled sequence of snapshots.
3. Swapping the connector (e.g. to replay a recorded scenario from a file)
   without editing `StateReader`.

The refactored version accepts any `ConnectorProtocol`-compatible object
as a constructor argument. Convenience factory classmethods
(`for_bluesky`, `for_mock`) preserve the simple single-call setup that
`main.py` and most callers need, while leaving the door open to custom
connectors.
"""

from collections import deque
from typing import Deque, List, Optional

from astra.interface.connector_base import ConnectorProtocol
from astra.interface.traffic_state import TrafficSnapshot
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)


class StateReader:
    """Polls a connector and maintains a bounded history of TrafficSnapshots.

    The connector is injected at construction time (see `for_bluesky()` and
    `for_mock()` factory classmethods for the common setup patterns).
    """

    def __init__(self, connector: ConnectorProtocol, config: ASTRAConfig) -> None:
        """Construct the reader around an already-instantiated connector.

        The connector is NOT connected here; call `connect()` explicitly
        so that connection errors surface at a predictable, visible point
        in the caller's code rather than silently during construction.

        Args:
            connector: Any object satisfying `ConnectorProtocol`
                (BlueSkyConnector, MockConnector, or a custom
                implementation).
            config: Shared ASTRA configuration. `config.history_length`
                controls the size of the rolling snapshot buffer.
        """
        self._connector: ConnectorProtocol = connector
        self._config: ASTRAConfig = config
        self._history: Deque[TrafficSnapshot] = deque(maxlen=config.history_length)

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def for_bluesky(cls, config: ASTRAConfig) -> "StateReader":
        """Create a StateReader wired to a live BlueSky simulation.

        Constructs a `BlueSkyConnector` internally and wraps it. This is
        the factory used by `main.py` and any code that requires a real
        BlueSky process. Does NOT open the connection — call `connect()`
        on the returned reader.

        Args:
            config: Shared ASTRA configuration (host, ports, …).

        Returns:
            A StateReader backed by a BlueSkyConnector.
        """
        # Deferred import: keeps BlueSky import isolated to this factory
        # and to bluesky_connector.py. All other ASTRA code remains free
        # of the bluesky-simulator dependency.
        from astra.interface.bluesky_connector import BlueSkyConnector
        from astra.interface.type_registry import TypeRegistry

        type_registry = TypeRegistry()
        connector = BlueSkyConnector(config=config, type_registry=type_registry)
        return cls(connector=connector, config=config)

    @classmethod
    def for_mock(cls, config: ASTRAConfig, sim_step_s: float = 1.0) -> "StateReader":
        """Create a StateReader backed by the offline MockConnector.

        No BlueSky installation or running process is needed. Traffic must
        be added via `create_aircraft()` calls (or `send_command("CRE …")`).
        Start the simulation clock with `send_command("OP")`.

        Args:
            config: Shared ASTRA configuration.
            sim_step_s: How many simulation seconds the mock advances on
                each `poll()` call. Default 1.0 matches
                `ASTRAConfig.poll_interval_s`.

        Returns:
            A StateReader backed by a MockConnector.
        """
        from astra.interface.mock_connector import MockConnector

        connector = MockConnector(sim_step_s=sim_step_s)
        return cls(connector=connector, config=config)

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the connection to the underlying data source.

        For BlueSkyConnector: opens ZMQ sockets to the BlueSky server.
        For MockConnector: sets the internal 'active' flag (no I/O).

        Must be called once before `poll()` produces data.
        """
        self._connector.connect()

    def poll(self) -> Optional[TrafficSnapshot]:
        """Advance the connector by one tick and record any new snapshot.

        Calls `connector.poll()` unconditionally, then checks whether the
        snapshot timestamp has changed since the last recorded entry. This
        guards against duplicate entries when `poll_interval_s` is shorter
        than the simulator's own publish rate (BlueSky publishes at 5 Hz;
        if the main loop polls at 10 Hz it would otherwise record the same
        snapshot twice).

        Returns:
            The newly recorded TrafficSnapshot if one was appended, or
            None if the snapshot timestamp has not changed since the
            previous call (i.e. the simulator did not publish new data).
        """
        self._connector.poll()
        snapshot = self._connector.latest_snapshot()

        if snapshot is None:
            return None

        if (
            self._history
            and self._history[-1].timestamp_s == snapshot.timestamp_s
        ):
            # Same simulation tick as last time; skip to avoid duplicates.
            return None

        self._history.append(snapshot)
        _LOG.debug(
            "New snapshot at t=%.1fs: %d aircraft",
            snapshot.timestamp_s,
            len(snapshot),
        )
        return snapshot

    def current(self) -> Optional[TrafficSnapshot]:
        """Return the most recent snapshot recorded in history.

        Returns:
            The latest TrafficSnapshot, or None if `poll()` has not yet
            produced any data.
        """
        return self._history[-1] if self._history else None

    def history(self, last_n: Optional[int] = None) -> List[TrafficSnapshot]:
        """Return all recorded snapshots, ordered oldest to newest.

        Args:
            last_n: If given, return only the most recent `last_n`
                snapshots. If None, return the full retained history (up
                to `ASTRAConfig.history_length` entries).

        Returns:
            A list of TrafficSnapshot objects.
        """
        if last_n is None:
            return list(self._history)
        return list(self._history)[-last_n:]

    def has_active_simulator(self) -> bool:
        """Whether the underlying simulator is active and ready.

        For BlueSkyConnector: True once a BlueSky simulation node has
        announced itself on the network.
        For MockConnector: True once `connect()` has been called.

        Returns:
            True if commands can be sent and data will be received.
        """
        return self._connector.has_active_node()

    # ------------------------------------------------------------------
    # Pass-through helpers — callers should use StateReader as their
    # single contact point for the simulator, never the connector directly.
    # ------------------------------------------------------------------

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
        """Create an aircraft in the simulation.

        Delegates to `connector.create_aircraft()`, which handles
        connector-specific details (TypeRegistry for BlueSkyConnector,
        direct dict insertion for MockConnector).

        Args:
            callsign: Aircraft callsign, e.g. "KL204".
            aircraft_type: ICAO type designator, e.g. "A320".
            lat: Initial latitude, decimal degrees.
            lon: Initial longitude, decimal degrees.
            heading_deg: Initial true heading, degrees.
            altitude_ft: Initial altitude, feet AMSL.
            speed_kt: Initial ground speed, knots.
        """
        self._connector.create_aircraft(
            callsign, aircraft_type, lat, lon, heading_deg, altitude_ft, speed_kt
        )

    def send_command(self, command_text: str) -> None:
        """Send a raw ATC stack command to the simulator.

        Args:
            command_text: A BlueSky-format stack command, e.g.
                "SPD KL204 250" or "OP".
        """
        self._connector.send_command(command_text)

    # ------------------------------------------------------------------
    # Scenario Builder passthroughs (MockConnector only)
    # ------------------------------------------------------------------

    @property
    def is_mock(self) -> bool:
        """True if this reader is backed by `MockConnector`."""
        from astra.interface.mock_connector import MockConnector

        return isinstance(self._connector, MockConnector)

    def _require_mock(self, action: str):
        if not self.is_mock:
            raise TypeError(
                f"{action} requires a MockConnector-backed StateReader "
                "(Scenario Builder only works in --mock mode)."
            )
        return self._connector

    def remove_aircraft(self, callsign: str) -> None:
        """Delete one aircraft by callsign. Mock-mode only."""
        self._require_mock("remove_aircraft").remove_aircraft(callsign)

    def update_aircraft(self, callsign: str, **fields) -> bool:
        """Edit one or more fields of an existing aircraft. Mock-mode only."""
        return self._require_mock("update_aircraft").update_aircraft(callsign, **fields)

    def list_aircraft(self) -> List[dict]:
        """Direct, immediate read of every aircraft's raw state. Mock-mode only."""
        return self._require_mock("list_aircraft").list_aircraft()

    def reset_simulation(self) -> None:
        """Delete all aircraft and reset the sim clock to zero. Mock-mode only."""
        self._require_mock("reset_simulation").reset()

    def step_simulation(self, ticks: int = 1) -> None:
        """Force the sim forward by `ticks` tick(s), regardless of pause state. Mock-mode only."""
        self._require_mock("step_simulation").step(ticks)

    def is_simulation_running(self) -> bool:
        """Whether the sim clock is currently advancing. Mock-mode only."""
        return self._require_mock("is_simulation_running").is_running()

    def simulation_time_s(self) -> float:
        """Current simulation clock, in seconds. Mock-mode only."""
        return self._require_mock("simulation_time_s").simt