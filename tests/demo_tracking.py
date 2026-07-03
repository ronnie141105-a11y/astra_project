"""
4DARHAC Tracking Demonstration — ASTRA Prototype (Milestone 5)
=================================================================

Drives `MockConnector` through a scripted sequence of manual `poll()`
cycles, feeding each cycle's observed cluster/complexity output into
`TrackerEngine`, to show a `FourDArhac` being opened, confirmed, growing,
peaking, dissipating, and finally closed as stale.

Uses near-stationary aircraft (very low ground speed) so that the
complexity swings driving the lifecycle come from scripted HDG/ALT/SPD
commands issued between poll cycles, not from incidental kinematics --
making the demonstration deterministic and easy to follow.

Run with:
    python demo_tracking.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.engine import ComplexityEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.tracking.engine import TrackerEngine
from astra.utils.config import ASTRAConfig

config = ASTRAConfig()  # defaults: confirm_cycles=2, stale_cycles=3, jaccard_threshold=0.5
reader = StateReader.for_mock(config, sim_step_s=30.0)
reader.connect()

# Three near-stationary aircraft, tightly clustered, identical type/alt/heading
# at t=0 -- complexity starts low; scripted commands below are what actually
# drive the lifecycle, not natural drift.
reader.create_aircraft("AC1", "A320", 47.000, 8.000, 0.0, 35000.0, 2.0)
reader.create_aircraft("AC2", "A320", 47.000, 8.020, 0.0, 35000.0, 2.0)
reader.create_aircraft("AC3", "A320", 47.010, 8.010, 0.0, 35000.0, 2.0)
reader.send_command("OP")

cluster_engine = ClusterEngine(config)
complexity_engine = ComplexityEngine(config)
tracker = TrackerEngine(config)


def run_cycle(label: str):
    """Poll once, run the Milestone 3/4 pipeline, then advance the tracker.

    Returns:
        ``(tracks, regions, snapshot)`` for the caller to inspect.
    """
    snapshot = reader.poll() or reader.current()
    clusters = cluster_engine.detect(snapshot)
    regions = complexity_engine.assess_many(clusters, snapshot)
    tracks = tracker.update({0: regions})

    print(f"--- {label} (t={snapshot.timestamp_s:.0f}s) " + "-" * 40)
    if not regions:
        print("  (no cluster detected this cycle)")
    for region in regions:
        print(
            f"  cluster members={sorted(region.cluster.member_callsigns)} "
            f"complexity={region.complexity_score:5.1f}"
        )
    for track in tracks:
        print(
            f"  ARHAC {track.arhac_id[:8]}  status={track.status:<11} "
            f"peak={track.peak_complexity:5.1f}  confidence={track.confidence:.2f}  "
            f"priority={track.priority}  members={sorted(track.member_aircraft)}"
        )
    print()
    return tracks, regions, snapshot


print("=" * 88)
print("  Milestone 5 — 4DARHAC tracking demo")
print("=" * 88)
print()

# Phase 1: stable formation -> CANDIDATE, then CONFIRMED.
run_cycle("Cycle 1 (first detection)")
run_cycle("Cycle 2 (second consecutive detection)")

# Phase 2: diverge headings and spread altitude (still within the 1000ft
# vertical gate) -> heading/altitude diversity rises -> GROWING.
reader.send_command("HDG AC2 090")
reader.send_command("HDG AC3 180")
reader.send_command("ALT AC2 35700")
run_cycle("Cycle 3 (headings/altitude diverging)")
run_cycle("Cycle 4 (diversity holds -> flattens to PEAK)")

# Phase 3: bring headings and altitude back together -> DISSIPATING.
reader.send_command("HDG AC2 0")
reader.send_command("HDG AC3 0")
reader.send_command("ALT AC2 35000")
run_cycle("Cycle 5 (re-aligning -> DISSIPATING)")

# Phase 4: AC2 and AC3 sprint directly away from AC1 in opposite
# directions until the cluster genuinely dissolves (rather than assuming
# a fixed number of cycles), then the demo holds for
# tracking_stale_cycles more cycles to watch the track close.
reader.send_command("SPD AC2 900")
reader.send_command("HDG AC2 090")
reader.send_command("SPD AC3 900")
reader.send_command("HDG AC3 270")

_MAX_BREAKUP_CYCLES = 10
cycle_num = 6
cluster_gone = False
for _ in range(_MAX_BREAKUP_CYCLES):
    _tracks, regions, _snapshot = run_cycle(f"Cycle {cycle_num} (formation breaking up)")
    cycle_num += 1
    if not regions:
        cluster_gone = True
        break

if not cluster_gone:
    print(
        f"  (formation did not fully break up within "
        f"{_MAX_BREAKUP_CYCLES} cycles -- try a higher departure speed)"
    )

closed = False
for _ in range(config.tracking_stale_cycles):
    tracks, _regions, _snapshot = run_cycle(f"Cycle {cycle_num} (waiting for staleness)")
    cycle_num += 1
    if any(track.status == "CLOSED" for track in tracks):
        closed = True
        break

if not closed:
    print(
        f"  (track did not close within tracking_stale_cycles="
        f"{config.tracking_stale_cycles} -- unexpected, check staleness logic)"
    )

print("  Tracking demo complete.")
print()
