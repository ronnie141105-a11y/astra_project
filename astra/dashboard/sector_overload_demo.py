"""
Data-collection / validation script for the `sector_overload` preset
(see `astra/dashboard/scenario_presets_operational.py`).

Spawns ~40 aircraft across 11 real route segments (MockConnector only)
and runs them forward through the real pipeline, tracking each of HCM
ACC Sectors 1, 2, 5, 6 and 7's traffic count/complexity over a
simulated hour.

Why this script does NOT use `astra.complexity.sector.SectorComplexityEngine`
as-is: that engine deliberately (and, for a compact terminal sector,
reasonably) approximates a sector as a single circle
(`SectorDefinition`). HCM ACC's real Sector 1/2/5 polygons are large
and irregular enough (confirmed empirically while building this
script -- a vertex-average-centroid circle wide enough to cover one of
them has a 150-300 NM radius) that a circle approximation makes
neighbouring sectors overlap almost entirely, so every aircraft gets
double- or triple-counted into 3-4 "sectors" at once and every sector
looks saturated from t=0 -- exactly the false-positive "already
overloaded" reading this preset is designed to avoid. Real polygon
membership doesn't have that problem, so this script reimplements
`SectorComplexityEngine.update()`'s own pattern (synthesize a `Cluster`
covering one sector's current members, `ComplexityEngine.assess()` it)
using `scenario_geo.sector_containing()`'s ray-casting polygon test for
membership instead of a circle -- same scoring engine, accurate
geometry. `SectorComplexityEngine` itself is left untouched; this is a
one-off variant local to this script, not a change to the production
sector-complexity path.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.complexity.engine import ComplexityEngine
from astra.dashboard import scenario_geo as geo
from astra.dashboard.scenario_presets import get_preset
from astra.hotspot.models import Cluster
from astra.interface.state_reader import StateReader
from astra.pipeline import Pipeline
from astra.utils.config import ASTRAConfig

PRESET_KEY = "sector_overload"
SECTOR_NUMBERS = ["1", "2", "5", "6", "7"]
SIM_STEP_S = 60.0
TOTAL_SIM_S = 3600.0  # 60 minutes
SAMPLE_EVERY_S = 300.0  # record every 5 min


def _polygon_sector_regions(config: ASTRAConfig, complexity_engine: ComplexityEngine, snapshot, sectors_by_number):
    """One ComplexityRegion per sector number, using real polygon membership."""
    regions = {}
    for number, polys in sectors_by_number.items():
        members = frozenset(
            ac.callsign for ac in snapshot.as_list()
            if geo.sector_containing(ac.lat, ac.lon, polys) is not None
        )
        if not members:
            continue
        lats = [snapshot.aircraft[cs].lat for cs in members]
        lons = [snapshot.aircraft[cs].lon for cs in members]
        cluster = Cluster(
            cluster_id=f"sector:{number}",
            source="observed",
            horizon_min=0,
            valid_at_s=snapshot.timestamp_s,
            member_callsigns=members,
            centroid_lat=sum(lats) / len(lats),
            centroid_lon=sum(lons) / len(lons),
            centroid_alt_ft=0.0,
            horizontal_extent_nm=max(config.complexity_min_extent_nm, 40.0),
        )
        regions[number] = complexity_engine.assess(cluster, snapshot)
    return regions


def main() -> None:
    config = ASTRAConfig()
    complexity_engine = ComplexityEngine(config)
    sectors_by_number = {n: geo.sectors_by_number(n) for n in SECTOR_NUMBERS}

    preset = get_preset(PRESET_KEY)
    print("=" * 78)
    print(f"Preset: {PRESET_KEY}  ({len(preset['aircraft'])} aircraft)")
    print("=" * 78)
    for ac in preset["aircraft"]:
        print(
            f"  {ac['callsign']:8s} {ac['aircraft_type']:6s} "
            f"lat={ac['lat']:.3f} lon={ac['lon']:.3f} hdg={ac['heading_deg']:5.1f} "
            f"alt={ac['altitude_ft']:.0f}ft gs={ac['speed_kt']}kt "
            f"wps_ahead={len(ac.get('route_waypoints') or [])}"
        )

    reader = StateReader.for_mock(config, sim_step_s=SIM_STEP_S)
    reader.connect()
    for ac in preset["aircraft"]:
        reader.create_aircraft(
            ac["callsign"], ac["aircraft_type"], ac["lat"], ac["lon"],
            ac["heading_deg"], ac["altitude_ft"], ac["speed_kt"],
            route_waypoints=ac.get("route_waypoints"),
        )
    reader.send_command("OP")

    pipeline = Pipeline(config, route_provider=reader.get_route)

    samples = {n: [] for n in SECTOR_NUMBERS}
    t = 0.0
    next_sample_t = 0.0
    result = None
    while t <= TOTAL_SIM_S:
        snapshot = reader.poll()
        if snapshot is None:
            break
        t = snapshot.timestamp_s
        result = pipeline.run_cycle(snapshot)
        if t >= next_sample_t:
            regions = _polygon_sector_regions(config, complexity_engine, snapshot, sectors_by_number)
            for number in SECTOR_NUMBERS:
                region = regions.get(number)
                samples[number].append({
                    "t_s": t,
                    "aircraft_count": len(region.cluster) if region else 0,
                    "complexity_score": region.complexity_score if region else 0.0,
                })
            next_sample_t += SAMPLE_EVERY_S

    print()
    print("=" * 78)
    print("Sector snapshot at t=0 (real polygon membership)")
    print("=" * 78)
    total_t0 = 0
    for number in SECTOR_NUMBERS:
        first = samples[number][0] if samples[number] else None
        if first:
            print(f"  Sector {number}: count={first['aircraft_count']:2d}  score={first['complexity_score']:.1f}")
            total_t0 += first["aircraft_count"]
    print(f"  (sum of per-sector counts at t=0: {total_t0} -- some aircraft on segments outside all 5 "
          f"target sectors, or in transit between sectors, are not double-counted here since a real "
          f"polygon can only contain a point once)")

    print()
    print("=" * 78)
    print("Per-sector trend over the simulated hour (every 5 min)")
    print("=" * 78)
    peak_overall = None
    for number, series in samples.items():
        counts = [s["aircraft_count"] for s in series]
        scores = [s["complexity_score"] for s in series]
        peak_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else None
        peak = series[peak_idx] if peak_idx is not None else None
        print(f"  Sector {number}:")
        print(f"    counts over time: {counts}")
        print(f"    scores over time: {[round(s, 1) for s in scores]}")
        if peak:
            print(f"    peak: t={peak['t_s']:.0f}s (~{peak['t_s']/60:.0f} min)  "
                  f"count={peak['aircraft_count']}  score={peak['complexity_score']:.1f}")
            if peak_overall is None or peak["complexity_score"] > peak_overall[1]["complexity_score"]:
                peak_overall = (number, peak)

    print()
    if peak_overall:
        number, peak = peak_overall
        print(f"Highest predicted sector complexity: Sector {number} at t={peak['t_s']:.0f}s "
              f"(~{peak['t_s']/60:.0f} min), count={peak['aircraft_count']}, score={peak['complexity_score']:.1f}")
        t0_score = samples[number][0]["complexity_score"] if samples[number] else None
        if t0_score is not None:
            print(f"  (vs t=0 in that same sector: score={t0_score:.1f} -- "
                  f"{'a genuine future peak, not already visible now' if peak['t_s'] > 0 and peak['complexity_score'] > t0_score else 'peak was already at t=0'})")

    if result is not None:
        print()
        print("=" * 78)
        print(f"Final cycle (DBSCAN-based, not sector-based): {len(result.tracks)} tracks open, "
              f"{sum(len(rs.candidates) for rs in result.resolution_sets)} resolution candidates issued this cycle")
        print("=" * 78)

    output_path = Path("/mnt/user-data/outputs/sector_overload_demo_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "preset": PRESET_KEY,
        "aircraft_count": len(preset["aircraft"]),
        "sector_history": {f"Sector {n}": v for n, v in samples.items()},
        "peak_sector": {"sector": f"Sector {peak_overall[0]}", **peak_overall[1]} if peak_overall else None,
    }
    output_path.write_text(json.dumps(payload, indent=2))
    print()
    print(f"Full results written to {output_path}")


if __name__ == "__main__":
    main()
