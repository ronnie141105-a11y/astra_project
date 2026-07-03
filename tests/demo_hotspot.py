"""
Cluster Detection Demonstration — ASTRA Prototype (Milestone 3)
=================================================================

Demonstrates DBSCAN-based cluster detection on both the observed
snapshot and every predicted horizon, using the offline MockConnector.

Run with:
    python demo_hotspot.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig

config = ASTRAConfig(poll_interval_s=1.0, history_length=60)
reader = StateReader.for_mock(config, sim_step_s=60.0)
reader.connect()

# Pair 1: parallel, non-converging -> stays clustered at every horizon.
# Pair 2: head-on, ~130 NM apart -> not clustered now, clusters briefly
#         around T+10 min as they cross, then separates again.
# UAE512: isolated -> always noise.
#    callsign  type   lat     lon      hdg    alt_ft   gs_kt
AIRCRAFT = [
    ("KL204",  "A320", 47.00,  8.000,   90.0, 35000.0, 300.0),
    ("DLH721", "A321", 47.03,  8.000,   90.0, 35000.0, 300.0),  # ~1.8NM north, same track
    ("BAW436", "B738", 48.00,  8.300,   90.0, 33000.0, 400.0),
    ("SWR101", "A319", 48.00, 11.913,  270.0, 33000.0, 400.0),  # ~145NM east, closing
    ("UAE512", "B77W", 10.00, 10.000,   45.0, 37000.0, 300.0),
]
for callsign, actype, lat, lon, hdg, alt, spd in AIRCRAFT:
    reader.create_aircraft(callsign, actype, lat, lon, hdg, alt, spd)

reader.send_command("OP")
snapshot = reader.poll() or reader.current()

cluster_engine = ClusterEngine(config)
trajectory_engine = TrajectoryEngine(config)


def print_clusters(title: str, clusters) -> None:
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)
    if not clusters:
        print("  (no clusters detected)")
    for c in clusters:
        print(
            f"  {c.cluster_id:<20} members={sorted(c.member_callsigns)} "
            f"centroid=({c.centroid_lat:.4f},{c.centroid_lon:.4f}) "
            f"extent={c.horizontal_extent_nm:.2f} NM"
        )
    print()


# Observed snapshot.
observed_clusters = cluster_engine.detect(snapshot)
print_clusters(f"Observed clusters at simt={snapshot.timestamp_s:.0f}s", observed_clusters)

# Every predicted horizon.
prediction = trajectory_engine.predict(snapshot)
clusters_by_horizon = cluster_engine.detect_all(prediction)
for horizon_min in prediction.horizon_list():
    print_clusters(
        f"Predicted clusters at T+{horizon_min} min", clusters_by_horizon[horizon_min]
    )

print("  Cluster detection demo complete.")
print()
