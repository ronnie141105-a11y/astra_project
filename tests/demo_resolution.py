"""
AI Resolution Demonstration — ASTRA Prototype (Milestone 7)
=================================================================

Drives the full pipeline (state -> trajectory prediction -> cluster
detection -> complexity assessment -> tracking -> forecast ->
resolution) through a scripted sequence of manual `poll()` cycles,
showing `ResolutionEngine` generate and rank candidate ATC clearances
for the most urgent open `FourDArhac` track each cycle.

Run with:
    python demo_resolution.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.engine import ComplexityEngine
from astra.forecast.engine import ForecastEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.resolution.engine import ResolutionEngine
from astra.tracking.engine import TrackerEngine
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig

config = ASTRAConfig()
reader = StateReader.for_mock(config, sim_step_s=30.0)
reader.connect()

# A converging 3-aircraft geometry: observed complexity starts below the
# forecast onset threshold, but the 5-minute predicted horizon already
# crosses it -- giving ForecastEngine a predicted onset/urgency rank to
# hand to ResolutionEngine from the very first confirmed cycle.
reader.create_aircraft("AC1", "A320", 47.000, 7.880, 90.0, 35000.0, 15.0)
reader.create_aircraft("AC2", "A320", 47.000, 8.120, 270.0, 35000.0, 15.0)
reader.create_aircraft("AC3", "A320", 47.090, 8.000, 180.0, 34000.0, 8.0)
reader.send_command("OP")

trajectory_engine = TrajectoryEngine(config)
cluster_engine = ClusterEngine(config)
complexity_engine = ComplexityEngine(config)
tracker = TrackerEngine(config)
forecaster = ForecastEngine(config)
resolver = ResolutionEngine(config)


def build_regions_by_horizon(snapshot):
    """Run trajectory prediction + clustering + complexity for every horizon.

    Returns:
        ``{0: observed_regions, 5: ..., 10: ..., ...}`` -- exactly the
        shape ``TrackerEngine.update()`` / ``ForecastEngine`` /
        ``ResolutionEngine`` all expect.
    """
    observed_clusters = cluster_engine.detect(snapshot)
    regions_by_horizon = {0: complexity_engine.assess_many(observed_clusters, snapshot)}

    prediction = trajectory_engine.predict(snapshot)
    clusters_by_horizon = cluster_engine.detect_all(prediction)
    for horizon_min in prediction.horizon_list():
        predicted_snapshot = prediction.at(horizon_min)
        regions_by_horizon[horizon_min] = complexity_engine.assess_many(
            clusters_by_horizon[horizon_min], predicted_snapshot
        )
    return regions_by_horizon


def run_cycle(label: str):
    """Poll once, run the full M2-M7 pipeline for this cycle.

    Returns:
        ``(tracks, resolution_sets, snapshot)`` for the caller to inspect.
    """
    snapshot = reader.poll() or reader.current()
    regions_by_horizon = build_regions_by_horizon(snapshot)
    tracks = tracker.update(regions_by_horizon)
    forecaster.forecast_many(tracks, regions_by_horizon)
    resolution_sets = resolver.resolve_many(tracks, snapshot, regions_by_horizon)

    print(f"--- {label} (t={snapshot.timestamp_s:.0f}s) " + "-" * 30)
    for track in tracks:
        rank = track.forecast_urgency_rank if track.forecast_urgency_rank is not None else "-"
        print(
            f"  ARHAC {track.arhac_id[:8]}  status={track.status:<11} "
            f"peak={track.peak_complexity:5.1f}  urgency_rank={rank}"
        )
    if not resolution_sets:
        print("  (no track eligible for resolution this cycle)")
    for rs in resolution_sets:
        print(
            f"  ResolutionSet for {rs.track.arhac_id[:8]} "
            f"@ horizon={rs.evaluated_horizon_min}min  candidates={len(rs)}"
        )
        for c in rs.candidates:
            print(
                f"    {c.clearance_type:12s} -> {c.target_callsign:<6} "
                f"delta={c.delta_value:+7.1f}  "
                f"complexity {c.complexity_before:5.1f} -> "
                f"{c.complexity_after if c.complexity_after is None else f'{c.complexity_after:5.1f}'}  "
                f"score={c.resolution_score:+.3f}"
            )
        best = rs.best()
        if best is not None:
            print(f"    BEST -> {best.clearance_type} on {best.target_callsign} (score={best.resolution_score:+.3f})")
    print()
    return tracks, resolution_sets, snapshot


print("=" * 96)
print("  Milestone 7 — AI resolution demo")
print("=" * 96)
print()

# Phase 1: the converging geometry is detected and confirmed; by cycle 2
# the 5-minute predicted horizon has already crossed the onset threshold,
# giving the track a predicted onset and urgency rank so ResolutionEngine
# has something to act on.
run_cycle("Cycle 1 (first detection)")
run_cycle("Cycle 2 (confirmed -> forecast onset -> resolution begins)")
run_cycle("Cycle 3 (holding course)")
run_cycle("Cycle 4 (holding course)")

print("  Resolution demo complete.")
print()
