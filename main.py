"""
ASTRA prototype entry point.

Connects to BlueSky (or a mock), polls traffic state, and runs the full
Milestone 2-7 pipeline (trajectory -> cluster -> complexity -> tracking ->
forecast -> resolution) every cycle via `astra.pipeline.Pipeline`. Since
Milestone 8, this is also the dashboard's live-loop owner (design review
OQ-2(A)): each cycle's `CycleResult` is pushed into a `CycleStore` that
the dashboard's Flask server (running in a background thread of this
same process) reads from -- see `astra.dashboard`.

Usage
-----
Live mode (requires a running BlueSky headless server):

    python -m bluesky --headless          # Terminal 1
    python main.py                        # Terminal 2

Offline mock mode (no BlueSky needed, for offline development/testing):

    python main.py --mock

Either mode opens the dashboard at http://127.0.0.1:8050/ by default
(see `ASTRAConfig.dashboard_host`/`dashboard_port`). Add `--no-dashboard`
to run the console-only loop without starting the Flask server.
"""

import argparse
import time

from astra.dashboard.server import run_dashboard_in_background
from astra.dashboard.store import CycleStore
from astra.interface.state_reader import StateReader
from astra.pipeline import CycleResult, Pipeline
from astra.utils.config import DEFAULT_CONFIG
from astra.utils.logger import get_logger

# "astra.main" (not __name__, which is "__main__" when run as a script and
# would fall outside the "astra" logger hierarchy get_logger() configures).
_LOG = get_logger("astra.main")

#: Maximum open tracks to print per cycle before truncating.
_MAX_PRINT = 10


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="ASTRA prototype.")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in offline mock mode (no BlueSky process needed).",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Run the console-only loop without starting the Milestone 8 dashboard.",
    )
    return parser.parse_args()


def _setup_mock_traffic(reader: StateReader) -> None:
    """Populate the mock connector with a small converging traffic scenario."""
    reader.create_aircraft("KL204", "A320", 52.30, 4.80, 90.0, 30000, 250)
    reader.create_aircraft("BAW123", "B738", 52.32, 4.50, 270.0, 31000, 280)
    reader.create_aircraft("DLH456", "A319", 52.10, 4.90, 0.0, 29000, 260)
    reader.create_aircraft("EZY789", "A320", 52.28, 4.75, 180.0, 30500, 255)
    reader.send_command("OP")
    _LOG.info("Mock traffic created (4 aircraft). Simulation clock started.")


def _format_best(result: CycleResult, arhac_id: str) -> str:
    """One-line summary of a track's best ranked clearance, or '-' if none."""
    resolution_set = next(
        (rs for rs in result.resolution_sets if rs.track.arhac_id == arhac_id), None
    )
    best = resolution_set.best() if resolution_set is not None else None
    if best is None:
        return "-"
    return f"{best.clearance_type} {best.target_callsign} (score={best.resolution_score:+.2f})"


def _print_cycle(result: CycleResult) -> None:
    """Log a one-line summary per open track, plus its best clearance if any."""
    snapshot = result.snapshot
    _LOG.info(
        "t=%6.0fs | %d aircraft | %d open track(s)",
        snapshot.timestamp_s,
        len(snapshot),
        len(result.tracks),
    )
    for track in result.tracks[:_MAX_PRINT]:
        _LOG.info(
            "  ARHAC %-8s status=%-11s peak=%5.1f urgency_rank=%s best=%s",
            track.arhac_id[:8],
            track.status,
            track.peak_complexity,
            track.forecast_urgency_rank,
            _format_best(result, track.arhac_id),
        )
    if len(result.tracks) > _MAX_PRINT:
        _LOG.info("  ... and %d more track(s)", len(result.tracks) - _MAX_PRINT)


def main() -> None:
    """Entry point: connect, poll, run the pipeline, and log results continuously."""
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

    pipeline = Pipeline(config)
    store = CycleStore()
    if not args.no_dashboard:
        run_dashboard_in_background(store, config)

    _LOG.info("Running. Press Ctrl+C to stop.")
    try:
        while True:
            snapshot = reader.poll()
            if snapshot is not None:
                result = pipeline.run_cycle(snapshot)
                store.update(result)
                _print_cycle(result)
            time.sleep(config.poll_interval_s)
    except KeyboardInterrupt:
        _LOG.info("Stopped by user.")


if __name__ == "__main__":
    main()
