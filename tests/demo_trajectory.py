"""
Trajectory Prediction Demonstration — ASTRA Prototype
=======================================================

Demonstrates the Phase 2 trajectory prediction pipeline end-to-end using
the offline MockConnector (no BlueSky process required).

What this script shows:
  1. Configuration loading (ASTRAConfig)
  2. StateReader construction via the for_mock() factory
  3. Five aircraft creation across Swiss/German upper airspace
  4. A single observed TrafficSnapshot
  5. TrajectoryEngine.predict() producing a PredictionResult
  6. Formatted prediction tables at each configured horizon
     (5, 10, 15, 30, 60 minutes)

Run with:
    python demo_trajectory.py

Expected output: one observed TrafficSnapshot table, followed by one
predicted-position table per horizon, showing each aircraft's
dead-reckoned position and altitude at T+horizon.
"""

import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.interface.state_reader import StateReader
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig

# ── 1. Configuration ──────────────────────────────────────────────────────────
config = ASTRAConfig(
    poll_interval_s=1.0,
    history_length=60,
    # prediction_horizons_min defaults to [5, 10, 15, 30, 60]
)

# ── 2. Build StateReader backed by MockConnector ──────────────────────────────
reader = StateReader.for_mock(config, sim_step_s=60.0)
reader.connect()

# ── 3. Create five aircraft across Swiss / southern-German upper airspace ──────
#
#    callsign   type   lat      lon     hdg    alt_ft   gs_kt   description
#    ─────────────────────────────────────────────────────────────────────────
#    KL204      A320   47.50    7.60     90    33000     465    Eastbound, FL330
#    DLH721     A321   48.20    8.30    180    35000     470    Southbound, FL350
#    BAW436     B738   47.80    8.80    270    33000     450    Westbound, FL330
#    SWR101     A319   47.20    8.00      0    31000     440    Northbound, FL310
#    UAE512     B77W   47.60    7.90     45    37000     490    NE-bound, FL370

AIRCRAFT = [
    #  callsign    type    lat      lon    hdg     alt_ft  gs_kt
    ("KL204",  "A320",  47.50,   7.60,  90.0,  33000.0,  465.0),
    ("DLH721", "A321",  48.20,   8.30, 180.0,  35000.0,  470.0),
    ("BAW436", "B738",  47.80,   8.80, 270.0,  33000.0,  450.0),
    ("SWR101", "A319",  47.20,   8.00,   0.0,  31000.0,  440.0),
    ("UAE512", "B77W",  47.60,   7.90,  45.0,  37000.0,  490.0),
]

for callsign, actype, lat, lon, hdg, alt, spd in AIRCRAFT:
    reader.create_aircraft(callsign, actype, lat, lon, hdg, alt, spd)

# ── 4. Start the simulation clock and obtain one observed TrafficSnapshot ─────
reader.send_command("OP")
snapshot = reader.poll()
if snapshot is None:
    snapshot = reader.current()

# ── 5. Print the observed snapshot ─────────────────────────────────────────────
print("=" * 78)
print(f"  ASTRA Trajectory Demo — observed TrafficSnapshot at "
      f"simt={snapshot.timestamp_s:.0f}s")
print("=" * 78)
print(f"  {'Callsign':<10} {'Type':<6} {'Lat':>8} {'Lon':>9} "
      f"{'Alt (ft)':>10} {'GS (kt)':>8} {'Hdg':>6}")
print("  " + "-" * 66)
for ac in sorted(snapshot.as_list(), key=lambda a: a.callsign):
    print(f"  {ac.callsign:<10} {ac.aircraft_type:<6} "
          f"{ac.lat:>8.4f} {ac.lon:>9.4f} "
          f"{ac.altitude_ft:>10.0f} {ac.ground_speed_kt:>8.1f} "
          f"{ac.heading_deg:>6.1f}")
print("  " + "-" * 66)
print(f"  Total: {len(snapshot)} aircraft")
print()

# ── 6. Run trajectory prediction ────────────────────────────────────────────────
engine = TrajectoryEngine(config)
result = engine.predict(snapshot)

print(f"  Prediction horizons: {result.horizon_list()} minutes")
print()

# ── 7. Print a prediction table for each horizon ────────────────────────────────
for horizon_min in result.horizon_list():
    predicted = result.at(horizon_min)
    print("=" * 78)
    print(f"  Predicted positions at T+{horizon_min} min "
          f"(simt={predicted.predicted_time_s:.0f}s)")
    print("=" * 78)
    print(f"  {'Callsign':<10} {'Type':<6} {'Lat':>8} {'Lon':>9} "
          f"{'Alt (ft)':>10} {'GS (kt)':>8} {'Hdg':>6}")
    print("  " + "-" * 66)
    for ac in sorted(predicted.as_list(), key=lambda a: a.callsign):
        print(f"  {ac.callsign:<10} {ac.aircraft_type:<6} "
              f"{ac.lat:>8.4f} {ac.lon:>9.4f} "
              f"{ac.altitude_ft:>10.0f} {ac.ground_speed_kt:>8.1f} "
              f"{ac.heading_deg:>6.1f}")
    print("  " + "-" * 66)
    print(f"  Total: {len(predicted)} aircraft")
    print()

print("  Trajectory prediction demo complete. Constant-velocity dead-reckoning")
print("  produced consistent predicted positions across all configured horizons.")
print()
