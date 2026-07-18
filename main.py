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
import dataclasses
import time

from astra.dashboard.server import run_dashboard_in_background
from astra.dashboard.store import CycleStore
from astra.interface.state_reader import StateReader
from astra.pipeline import CycleResult, Pipeline
from astra.utils.config import DEFAULT_CONFIG, SectorDefinition
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
    """Populate the mock connector with a small converging traffic scenario.

    Anchored in the Ho Chi Minh FIR (~10.8N, 106.7E) rather than the
    original Netherlands-based demo coordinates -- the dashboard's map
    now loads real Vietnam AIP-derived FIR/sector/airway/waypoint/navaid
    geometry (astra/dashboard/geo/*.json), and `computeBounds()` fits the
    view to the union of that geometry and observed traffic. Demo
    aircraft on the other side of the planet from the loaded FIR would
    make that bounding box degenerate (both the FIR and the traffic
    reduced to specks, thousands of NM apart). Matches the same area
    `astra/dashboard/scenario_presets.py`'s presets already use.

    Geometry: four aircraft on a symmetric 10 NM converging cross
    (matches `scenarios/thesis_converging_hotspot.scn`, validated during
    thesis data collection). Deliberately uses terminal-area speeds
    (~115-130 kt), not cruise speeds -- at cruise speed the group closes,
    crosses, and disperses again *within* a single 5-minute prediction
    horizon, so no future horizon ever catches it above
    `forecast_onset_threshold` and `ForecastEngine.predicted_onset_s`
    never fires, meaning `ResolutionEngine` never has anything eligible
    to resolve either. At these speeds the group starts below threshold
    (~44 points) and is genuinely forecast to cross it a few cycles in,
    reliably exercising the full detect -> track -> forecast -> resolve
    chain end to end.
    """
    reader.create_aircraft("HVN301", "A320", 10.96655, 106.70000, 180.0, 30000, 120)
    reader.create_aircraft("VJC302", "B738", 10.63345, 106.70000, 0.0, 30000, 130)
    reader.create_aircraft("PIC303", "A319", 10.79995, 106.86956, 270.0, 30500, 115)
    reader.create_aircraft("AXJ304", "B77W", 10.79995, 106.53044, 90.0, 30000, 125)
    reader.send_command("OP")
    _LOG.info("Mock traffic created (4 aircraft, converging). Simulation clock started.")


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
    # Opt-in sector list for the dashboard's "Complexity Forecast" page
    # (astra.complexity.sector) -- empty by default in DEFAULT_CONFIG, so
    # that page is a no-op until sectors are named here. Centred on the
    # same Ho Chi Minh FIR area (~10.8N, 106.7E) as `_setup_mock_traffic`
    # below and `astra/dashboard/scenario_presets.py`.
    config = dataclasses.replace(
        DEFAULT_CONFIG,
        sectors=[
            SectorDefinition(name="HCM-S2A", center_lat=10.80, center_lon=106.70, radius_nm=40.0),
            SectorDefinition(name="HCM-S2B", center_lat=10.95, center_lon=106.55, radius_nm=40.0),
        ],
    )

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

    pipeline = Pipeline(config, route_provider=reader.get_route)
    store = CycleStore()
    if not args.no_dashboard:
        # Pass the same `reader` the poll loop below uses, so the
        # Scenario Builder page's routes (create/edit/delete aircraft,
        # pause/resume/step/reset) act on the actual running simulation
        # rather than a separate copy of it. In --mock mode this also
        # means a scenario can be built entirely from the browser --
        # `_setup_mock_traffic()` above just seeds a default scene.
        run_dashboard_in_background(store, config, reader=reader)

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
