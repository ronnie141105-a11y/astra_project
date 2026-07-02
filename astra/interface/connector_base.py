"""
Connector interface definition.

Why `Protocol` rather than `ABC`
----------------------------------
`BlueSkyConnector` already inherits from `bluesky.network.client.Client`.
Adding an explicit second base class (an ABC) would create a multiple-
inheritance diamond that could silently break BlueSky's own MRO-dependent
metaclass machinery. Python's `typing.Protocol` avoids this entirely: a
class satisfies a `Protocol` automatically as long as it provides the
right attribute names and signatures — no explicit inheritance declaration
is needed, and no MRO is touched.

Concretely, this means:

* `BlueSkyConnector` (which inherits from BlueSky's `Client`) satisfies
  `ConnectorProtocol` without any change to its inheritance chain.
* `MockConnector` (which inherits from nothing but `object`) also satisfies
  `ConnectorProtocol` automatically.
* `StateReader` can type-annotate its `_connector` field as
  `ConnectorProtocol` and both concrete implementations slot in without
  any casting.

`@runtime_checkable` is added so that `isinstance(obj, ConnectorProtocol)`
works at runtime — useful in assertions and `StateReader`'s factory
methods.
"""

from typing import Optional, Protocol, runtime_checkable

from astra.interface.traffic_state import TrafficSnapshot


@runtime_checkable
class ConnectorProtocol(Protocol):
    """Minimal interface every simulator connector must satisfy.

    Concrete implementations:
    * `astra.interface.bluesky_connector.BlueSkyConnector` — live traffic
      from a running BlueSky simulation.
    * `astra.interface.mock_connector.MockConnector` — synthetic traffic
      generated in-process, requires no external simulator.
    """

    def connect(self) -> None:
        """Establish the connection to the data source.

        For BlueSkyConnector this opens the ZMQ sockets. For MockConnector
        this is a no-op that sets an internal 'running' flag.
        Must be called once before `poll()` produces any data.
        """
        ...

    def poll(self) -> None:
        """Advance the connector by one tick.

        For BlueSkyConnector: calls `update()`, which processes any pending
        ZMQ messages from BlueSky (both incoming ACDATA and outgoing queued
        stack commands).
        For MockConnector: advances the internal simulation clock by
        `sim_step_s` and propagates all aircraft positions.
        Non-blocking in both cases.
        """
        ...

    def latest_snapshot(self) -> Optional[TrafficSnapshot]:
        """Return the most recent traffic state, or None if not yet available.

        Returns:
            A TrafficSnapshot containing the current state of every aircraft,
            or None if `poll()` has not yet produced any data.
        """
        ...

    def has_active_node(self) -> bool:
        """Whether the underlying simulation is active and ready.

        For BlueSkyConnector: True once a BlueSky simulation node has
        announced itself on the network.
        For MockConnector: True once `connect()` has been called.

        Returns:
            True if commands can be sent and data will be received.
        """
        ...

    def send_command(self, command_text: str) -> None:
        """Send a raw ATC stack command to the simulator.

        For BlueSkyConnector: queues the command for transmission to BlueSky
        on the next `poll()` call.
        For MockConnector: parses and executes the command immediately
        (CRE, DEL, SPD, ALT, HDG, OP, HOLD are understood).

        Args:
            command_text: A BlueSky-format stack command string, e.g.
                "SPD KL204 250" or "CRE KL204,A320,52.30,4.80,90,30000,250".
        """
        ...

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
        """Create a new aircraft in the simulation.

        For BlueSkyConnector: registers the type in TypeRegistry (so
        subsequent ACDATA snapshots will carry the correct type string),
        then sends a CRE stack command.
        For MockConnector: directly inserts the aircraft into the internal
        state dict, bypassing command parsing.

        Args:
            callsign: Aircraft callsign, e.g. "KL204".
            aircraft_type: ICAO type designator, e.g. "A320".
            lat: Initial latitude, decimal degrees.
            lon: Initial longitude, decimal degrees.
            heading_deg: Initial true heading, degrees.
            altitude_ft: Initial altitude, feet AMSL.
            speed_kt: Initial ground speed, knots.
        """
        ...
