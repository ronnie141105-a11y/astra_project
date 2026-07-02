"""
ASTRA prototype entry point — Phase 1 demonstration.

Demonstrates Phase 1 (data interface) only. Later phases will extend this
main loop as they are built, inserting their calls between `poll()` and
the print statement.

Usage
------
Live mode (requires a running BlueSky headless server):

    python -m bluesky --headless          # Terminal 1
    python main.py                        # Terminal 2

Offline mock mode (no BlueSky needed, for offline development/testing):

    python main.py --mock

In mock mode, four aircraft are created programmatically and the simulation
clock is started automatically. The mock advances one second per poll cycle.
"""

import argparse
import time

from astra.interface.state_reader import StateReader
from astra.interface.traffic_state import TrafficSnapshot
from astra.utils.config import DEFAULT_CONFIG
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: Maximum aircraft to print per snapshot before truncating.
_MAX_PRINT = 10


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="ASTRA Phase 1 demonstration."
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Run in offline mock mode. No BlueSky process is needed. "
            "Four synthetic aircraft are created automatically."
        ),
    )
    return parser.parse_args()


def _setup_mock_traffic(reader: StateReader) -> None:
    """Populate the mock connector with a small, representative traffic scenario.

    Four aircraft set up to converge in Swiss upper airspace — a scenario
    that will produce a non-trivial hotspot once Phase 3 is implemented.
    All positions are approximate reproductions of the Geneva / Zurich
    upper-airspace geometry used in the reference ASTRA papers.

    Args:
        reader: A StateReader backed by a MockConnector.
    """
    reader.create_aircraft("KL204",  "A320", 52.30,  4.80,  90.0, 30000, 250)
    reader.create_aircraft("BAW123", "B738", 52.32,  4.50, 270.0, 31000, 280)
    reader.create_aircraft("DLH456", "A319", 52.10,  4.90,   0.0, 29000, 260)
    reader.create_aircraft("EZY789", "A320", 52.28,  4.75, 180.0, 30500, 255)
    reader.send_command("OP")
    _LOG.info("Mock traffic created (4 aircraft). Simulation clock started.")


def _print_snapshot(snapshot: TrafficSnapshot) -> None:
    """Log a human-readable summary of one TrafficSnapshot.

    Args:
        snapshot: The snapshot to summarise.
    """
    _LOG.info("t=%6.0fs | %d aircraft", snapshot.timestamp_s, len(snapshot))
    for ac in snapshot.as_list()[:_MAX_PRINT]:
        _LOG.info(
            "  %-8s  lat=%8.4f  lon=%9.4f  alt=%6.0fft  "
            "gs=%5.0fkt  hdg=%5.1f  vs=%6.0ffpm  type=%s",
            ac.callsign,
            ac.lat,
            ac.lon,
            ac.altitude_ft,
            ac.ground_speed_kt,
            ac.heading_deg,
            ac.vertical_speed_fpm,
            ac.aircraft_type,
        )
    if len(snapshot) > _MAX_PRINT:
        _LOG.info("  … and %d more aircraft", len(snapshot) - _MAX_PRINT)


def main() -> None:
    """Entry point: connect, poll, and print traffic state continuously."""
    args = _parse_args()
    config = DEFAULT_CONFIG

    if args.mock:
        _LOG.info("Starting ASTRA in OFFLINE MOCK mode.")
        reader = StateReader.for_mock(config, sim_step_s=config.poll_interval_s)
        reader.connect()
        _setup_mock_traffic(reader)
    else:
        _LOG.info("Starting ASTRA in LIVE mode. Connecting to BlueSky...")
        reader = StateReader.for_bluesky(config)
        reader.connect()
        _LOG.info(
            "Waiting for a BlueSky simulation node "
            "(start BlueSky with: python -m bluesky --headless) ..."
        )
        while not reader.has_active_simulator():
            reader.poll()
            time.sleep(0.5)
        _LOG.info("BlueSky node active. Polling every %.1fs.", config.poll_interval_s)

    _LOG.info("Running. Press Ctrl+C to stop.")
    try:
        while True:
            snapshot = reader.poll()
            if snapshot is not None:
                _print_snapshot(snapshot)
            time.sleep(config.poll_interval_s)
    except KeyboardInterrupt:
        _LOG.info("Stopped by user.")


if __name__ == "__main__":
    main()
