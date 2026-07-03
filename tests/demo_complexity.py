"""
Complexity Assessment Demonstration — ASTRA Prototype (Milestone 4)
=====================================================================

Demonstrates the full Cluster -> ComplexityRegion pipeline: cluster
detection (Milestone 3) followed by complexity scoring, on both the
observed snapshot and every predicted horizon.

Run with:
    python demo_complexity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.engine import ComplexityEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig

config = ASTRAConfig(poll_interval_s=1.0, history_length=60)
reader = StateReader.for_mock(config, sim_step_s=60.0)
reader.connect()

# A dense, mixed-type, head-on group (high complexity) and a slower,
# same-type, parallel pair (low complexity), plus an isolated aircraft.
#    callsign  type   lat     lon     hdg    alt_ft   gs_kt
AIRCRAFT = [
    ("KL204",  "A320", 47.00,  8.000,   0.0, 35000.0, 320.0),
    ("DLH721", "A321", 47.05,  8.000, 180.0, 35100.0, 320.0),
    ("BAW436", "B738", 47.02,  8.020,  90.0, 34900.0, 300.0),
    ("SWR101", "A319", 48.00, 10.000,  90.0, 33000.0, 250.0),
    ("UAE512", "B77W", 48.00, 10.030,  90.0, 33050.0, 250.0),
    ("QTR777", "A388", 10.00, 10.000,  45.0, 37000.0, 300.0),  # isolated -> noise
]
for callsign, actype, lat, lon, hdg, alt, spd in AIRCRAFT:
    reader.create_aircraft(callsign, actype, lat, lon, hdg, alt, spd)

reader.send_command("OP")
snapshot = reader.poll() or reader.current()

cluster_engine = ClusterEngine(config)
complexity_engine = ComplexityEngine(config)
trajectory_engine = TrajectoryEngine(config)


def print_regions(title: str, regions) -> None:
    print("=" * 88)
    print(f"  {title}")
    print("=" * 88)
    if not regions:
        print("  (no clusters detected)")
    for r in regions:
        c = r.cluster
        print(f"  {c.cluster_id:<20} members={sorted(c.member_callsigns)}")
        print(f"    complexity_score = {r.complexity_score:5.1f} / 100")
        comp = r.components
        print(
            f"    density={comp['density_ac_per_nm2']:.3f} ac/NM^2  "
            f"MTCA={int(comp['mtca_count'])}  LTCA={int(comp['ltca_count'])}  "
            f"hdg_div={comp['heading_div_deg']:.1f} deg  "
            f"alt_div={comp['alt_div_ft']:.0f} ft  "
            f"types={int(comp['type_mix_count'])}"
        )
    print()


# Observed snapshot.
observed_clusters = cluster_engine.detect(snapshot)
observed_regions = complexity_engine.assess_many(observed_clusters, snapshot)
print_regions(f"Observed complexity at simt={snapshot.timestamp_s:.0f}s", observed_regions)

# Every predicted horizon.
prediction = trajectory_engine.predict(snapshot)
clusters_by_horizon = cluster_engine.detect_all(prediction)
for horizon_min in prediction.horizon_list():
    predicted_snapshot = prediction.at(horizon_min)
    regions = complexity_engine.assess_many(
        clusters_by_horizon[horizon_min], predicted_snapshot
    )
    print_regions(f"Predicted complexity at T+{horizon_min} min", regions)

print("  Complexity assessment demo complete.")
print()
