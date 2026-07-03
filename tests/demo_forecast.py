"""
4DARHAC Forecast Demonstration — ASTRA Prototype (Milestone 6)
=================================================================

Drives the full pipeline (state -> trajectory prediction -> cluster
detection -> complexity assessment -> tracking -> forecast) through a
scripted sequence of manual `poll()` cycles, showing `ForecastEngine`
estimate onset/peak/dissipation times, confidence and urgency rank for
a `FourDArhac` track from this cycle's real predicted-horizon clusters.

Run with:
    python demo_forecast.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.engine import ComplexityEngine
from astra.forecast.engine import ForecastEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.tracking.engine import TrackerEngine
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig

config = ASTRAConfig()
reader = StateReader.for_mock(config, sim_step_s=30.0)
reader.connect()

# Same near-stationary formation as the Milestone 5 tracking demo (GS=2kt)
# so the observed lifecycle is identical/deterministic; a higher SPD is
# scripted in later cycles specifically to make the *predicted* horizons
# diverge, giving ForecastEngine something to interpolate over.
reader.create_aircraft("AC1", "A320", 47.000, 8.000, 0.0, 35000.0, 2.0)
reader.create_aircraft("AC2", "A320", 47.000, 8.020, 0.0, 35000.0, 2.0)
reader.create_aircraft("AC3", "A320", 47.010, 8.010, 0.0, 35000.0, 2.0)
reader.send_command("OP")

trajectory_engine = TrajectoryEngine(config)
cluster_engine = ClusterEngine(config)
complexity_engine = ComplexityEngine(config)
tracker = TrackerEngine(config)
forecaster = ForecastEngine(config)


def build_regions_by_horizon(snapshot):
    """Run trajectory prediction + clustering + complexity for every horizon.

    Returns:
        ``{0: observed_regions, 5: ..., 10: ..., ...}`` -- exactly the
        shape ``TrackerEngine.update()`` and ``ForecastEngine`` expect.
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


def _fmt(value, suffix="s"):
    """Right-align a possibly-None seconds value for column output."""
    return f"{value:6.0f}{suffix}" if value is not None else "   n/a "


def run_cycle(label: str):
    """Poll once, run the full M2-M6 pipeline for this cycle.

    Returns:
        ``(tracks, regions_by_horizon, snapshot)`` for the caller to inspect.
    """
    snapshot = reader.poll() or reader.current()
    regions_by_horizon = build_regions_by_horizon(snapshot)
    tracks = tracker.update(regions_by_horizon)
    forecaster.forecast_many(tracks, regions_by_horizon)

    print(f"--- {label} (t={snapshot.timestamp_s:.0f}s) " + "-" * 30)
    observed = regions_by_horizon[0]
    if not observed:
        print("  (no cluster detected this cycle)")
    for region in observed:
        print(
            f"  cluster members={sorted(region.cluster.member_callsigns)} "
            f"complexity={region.complexity_score:5.1f}"
        )
    for track in tracks:
        rank = track.forecast_urgency_rank if track.forecast_urgency_rank is not None else "-"
        print(
            f"  ARHAC {track.arhac_id[:8]}  status={track.status:<11} "
            f"peak={track.peak_complexity:5.1f}  confidence={track.confidence:.2f}  "
            f"onset={_fmt(track.predicted_onset_s)}  "
            f"dissipation={_fmt(track.predicted_dissipation_s)}  "
            f"peak_time={_fmt(track.predicted_peak_time_s)}  urgency_rank={rank}"
        )
    print()
    return tracks, regions_by_horizon, snapshot


print("=" * 96)
print("  Milestone 6 — 4DARHAC forecast demo")
print("=" * 96)
print()

# Phase 1: stable formation -> CANDIDATE, then CONFIRMED (forecast begins).
run_cycle("Cycle 1 (first detection)")
run_cycle("Cycle 2 (second consecutive detection -> CONFIRMED)")

# Phase 2: diverge headings/altitude and pick up speed -> observed
# complexity rises (GROWING/PEAK) while the higher SPD makes the
# *predicted* horizons increasingly diverge from the observed cluster.
reader.send_command("HDG AC2 090")
reader.send_command("HDG AC3 180")
reader.send_command("ALT AC2 35700")
reader.send_command("SPD AC2 60")
reader.send_command("SPD AC3 60")
run_cycle("Cycle 3 (headings/altitude diverging -> GROWING)")
run_cycle("Cycle 4 (diversity holds -> flattens to PEAK)")

# Phase 3: re-align headings and slow back down -> observed DISSIPATING;
# the predicted horizons from Cycle 3/4 (still fast + diverging) should
# already have shown a forecast dissipation ahead of this actually
# happening.
reader.send_command("HDG AC2 0")
reader.send_command("HDG AC3 0")
reader.send_command("ALT AC2 35000")
reader.send_command("SPD AC2 2")
reader.send_command("SPD AC3 2")
run_cycle("Cycle 5 (re-aligning -> DISSIPATING)")
run_cycle("Cycle 6 (holding)")

print("  Forecast demo complete.")
print()
