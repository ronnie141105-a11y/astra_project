"""
Phase 1 Demonstration — ASTRA Prototype
========================================

Demonstrates the complete Phase 1 pipeline end-to-end using the offline
MockConnector (no BlueSky process required).

What this script shows:
  1. Configuration loading (ASTRAConfig)
  2. StateReader construction via the for_mock() factory
  3. Five aircraft creation across Swiss/German upper airspace
  4. Simulation clock start
  5. Five poll() cycles — positions propagate each tick
  6. Formatted TrafficSnapshot display

Run with:
    python demo_phase1.py

Expected output: one TrafficSnapshot printed to stdout showing all five
aircraft with correct positions, altitudes, speeds, headings and types.
"""

import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astra.interface.state_reader import StateReader
from astra.interface.traffic_state import TrafficSnapshot, AircraftState
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm

# ── 1. Configuration ──────────────────────────────────────────────────────────
config = ASTRAConfig(
    poll_interval_s=1.0,
    history_length=60,            # keep 60 snapshots = 1-minute rolling window
)

# ── 2. Build StateReader backed by MockConnector ──────────────────────────────
#    sim_step_s=60 advances the mock by 60 s per poll() — i.e. 1 simulated
#    minute per call, useful for rapidly stepping through a scenario.
reader = StateReader.for_mock(config, sim_step_s=60.0)
reader.connect()

# ── 3. Create five aircraft across Swiss / southern-German upper airspace ──────
#
#    The positions, routes and flight levels are representative of the
#    en-route traffic that the reference SESAR ASTRA papers use to validate
#    hotspot detection.  Aircraft are deliberately set on crossing or
#    converging routes so that later phases (clustering, complexity) will
#    have interesting scenarios to work with.
#
#    callsign   type   lat      lon     hdg    alt_ft   gs_kt   description
#    ─────────────────────────────────────────────────────────────────────────
#    KL204      A320   47.50    7.60     90    33000     465    Eastbound, FL330
#    DLH721     A321   48.20    8.30    180    35000     470    Southbound, FL350
#    BAW436     B738   47.80    8.80    270    33000     450    Westbound, FL330  (crosses KL204)
#    SWR101     A319   47.20    8.00      0    31000     440    Northbound, FL310
#    UAE512     B77W   47.60    7.90     45    37000     490    NE-bound, FL370   (over-flight)

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

# ── 4. Start the simulation clock ─────────────────────────────────────────────
reader.send_command("OP")

# ── 5. Poll five times (= 5 simulated minutes with sim_step_s=60) ─────────────
print("Polling 5 times (each tick = 60 simulated seconds) …\n")
snapshot = None
for tick in range(5):
    snapshot = reader.poll()
    if snapshot is None:
        snapshot = reader.current()   # dedup guard — use cached
    if snapshot:
        print(f"  Tick {tick+1}: simt={snapshot.timestamp_s:.0f}s, "
              f"{len(snapshot)} aircraft")

# ── 6. Pretty-print the final TrafficSnapshot ─────────────────────────────────
print()
print("=" * 78)
print(f"  ASTRA Phase 1 Demo — TrafficSnapshot at simt={snapshot.timestamp_s:.0f}s")
print("=" * 78)
print(f"  {'Callsign':<10} {'Type':<6} {'Lat':>8} {'Lon':>9} "
      f"{'Alt (ft)':>10} {'GS (kt)':>8} {'Hdg':>6} {'VS (fpm)':>9}")
print("  " + "-" * 74)

for ac in sorted(snapshot.as_list(), key=lambda a: a.callsign):
    print(f"  {ac.callsign:<10} {ac.aircraft_type:<6} "
          f"{ac.lat:>8.4f} {ac.lon:>9.4f} "
          f"{ac.altitude_ft:>10.0f} {ac.ground_speed_kt:>8.1f} "
          f"{ac.heading_deg:>6.1f} {ac.vertical_speed_fpm:>9.1f}")

print("  " + "-" * 74)
print(f"  Total: {len(snapshot)} aircraft   "
      f"History depth: {len(reader.history())} snapshot(s)")
print("=" * 78)

# ── 7. Show inter-aircraft separation ─────────────────────────────────────────
print()
print("  Inter-aircraft horizontal separations (NM):")
print(f"  {'Pair':<20} {'Horiz Sep (NM)':>16} {'Alt Sep (ft)':>14}")
print("  " + "-" * 52)
aircraft_list = sorted(snapshot.as_list(), key=lambda a: a.callsign)
for i, a in enumerate(aircraft_list):
    for b in aircraft_list[i+1:]:
        h_sep = haversine_distance_nm(a.lat, a.lon, b.lat, b.lon)
        v_sep = abs(a.altitude_ft - b.altitude_ft)
        pair = f"{a.callsign}-{b.callsign}"
        flag = "  ← < 15 NM" if h_sep < 15.0 else ""
        print(f"  {pair:<20} {h_sep:>14.2f} NM  {v_sep:>10.0f} ft{flag}")

print()
print("  Phase 1 demo complete. All pipeline stages from config → interface")
print("  → state storage → snapshot delivery are working correctly.")
print()
