"""
Data-collection / validation script for the `crossing_airways` preset
(see `astra/dashboard/scenario_presets_operational.py`).

Two real inbound flows (W1 from the NNE, W2 from due east) cross at
waypoint AC now -- a lead pair ~12 NM out on each track (mutually well
inside clustering range) plus a trailing aircraft on each, ~30-35 min
out. A third flow (W15, from the ENE) is genuinely still further out
(~19 and ~30 min) and deliberately not part of the immediate cluster --
see the preset's own docstring for why a three-way immediate cluster
would have been a tactical, not medium-term, scenario. This script
runs the real pipeline (MockConnector only) and reports:

1. The observed (t=0) cluster/complexity for the tracked W1/W2 pair and
   how it evolves across a few real polling cycles (track status,
   forecast onset, resolution candidates).
2. Direct ETA-to-AC analysis for all six aircraft (including the two
   W15 "later wave" ones that never enter the tracked cluster, per
   this project's structural constraint 1 -- see
   `scenario_presets.py`), showing the sustained-density story: the
   W1/W2 encounter resolves within a few minutes, then W15's traffic
   arrives at AC 15-30 minutes afterward.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.dashboard.scenario_presets import get_preset
from astra.interface.state_reader import StateReader
from astra.pipeline import Pipeline
from astra.resolution.engine import ResolutionEngine
from astra.tracking.models import FourDArhac
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm

PRESET_KEY = "crossing_airways"
SIM_STEP_S = 30.0
NUM_CYCLES = 20  # 10 minutes of real polling
AC_LAT, AC_LON = 10.939167, 107.188333


def main() -> None:
    config = ASTRAConfig()
    preset = get_preset(PRESET_KEY)

    print("=" * 78)
    print(f"Preset: {PRESET_KEY}  ({len(preset['aircraft'])} aircraft)")
    print("=" * 78)
    for ac in preset["aircraft"]:
        print(
            f"  {ac['callsign']:8s} {ac['aircraft_type']:5s} "
            f"lat={ac['lat']:.4f} lon={ac['lon']:.4f} hdg={ac['heading_deg']:6.1f} "
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

    print()
    print("=" * 78)
    print("ETA to AC at spawn speed (straight-line distance / ground speed)")
    print("=" * 78)
    for ac in preset["aircraft"]:
        dist_nm = haversine_distance_nm(ac["lat"], ac["lon"], AC_LAT, AC_LON)
        eta_min = dist_nm / ac["speed_kt"] * 60.0
        print(f"  {ac['callsign']:8s} dist_to_AC={dist_nm:6.1f} NM  ETA~{eta_min:5.1f} min")

    pipeline = Pipeline(config, route_provider=reader.get_route)

    cycles_payload = []
    results_by_cycle = []
    result = None
    for cycle in range(NUM_CYCLES):
        snapshot = reader.poll()
        if snapshot is None:
            continue
        result = pipeline.run_cycle(snapshot)

        obs = result.regions_by_horizon.get(0, [])
        print()
        print(f"--- cycle {cycle}  t={snapshot.timestamp_s:.0f}s ---")
        if not obs:
            print("  no observed cluster")
        for region in obs:
            print(f"  observed cluster: members={sorted(region.cluster.member_callsigns)} "
                  f"score={region.complexity_score:.1f} extent={region.cluster.horizontal_extent_nm:.1f}NM")
        for h in sorted(result.regions_by_horizon):
            if h == 0:
                continue
            regions_h = result.regions_by_horizon[h]
            if regions_h:
                best = max(regions_h, key=lambda r: r.complexity_score)
                print(f"    horizon={h:>3d}min  best_score={best.complexity_score:.1f}  "
                      f"members={sorted(best.cluster.member_callsigns)}")
        for tr in result.tracks:
            print(f"  track {tr.arhac_id}: status={tr.status} predicted_onset_s={tr.predicted_onset_s} "
                  f"urgency_rank={tr.forecast_urgency_rank} peak_complexity={tr.peak_complexity:.1f}")
        for rs in result.resolution_sets:
            print(f"  ResolutionSet (horizon={rs.evaluated_horizon_min}min): {len(rs.candidates)} candidates")
            for c in rs.candidates[:5]:
                after_str = f"{c.complexity_after:.1f}" if c.complexity_after is not None else "n/a"
                print(f"    {c.clearance_type:8s} {c.delta_value:+7.1f}  score={c.resolution_score:+.4f}  "
                      f"before={c.complexity_before:.1f}  after={after_str}")

        results_by_cycle.append(result)
        cycles_payload.append({
            "t_s": snapshot.timestamp_s,
            "observed_clusters": [
                {"members": sorted(r.cluster.member_callsigns), "score": r.complexity_score}
                for r in obs
            ],
            "tracks": [
                {
                    "arhac_id": tr.arhac_id, "status": tr.status,
                    "predicted_onset_s": tr.predicted_onset_s,
                    "urgency_rank": tr.forecast_urgency_rank,
                    "peak_complexity": tr.peak_complexity,
                }
                for tr in result.tracks
            ],
            "resolution_candidates": [
                {
                    "clearance_type": c.clearance_type, "delta_value": c.delta_value,
                    "resolution_score": c.resolution_score,
                    "complexity_before": c.complexity_before, "complexity_after": c.complexity_after,
                }
                for rs in result.resolution_sets for c in rs.candidates
            ],
        })

    # The W1/W2 pair closes fully within ~3 min -- faster than
    # ForecastEngine's within-cycle horizon crossing can register (the
    # same structural constraint 2 documented in scenario_presets.py:
    # at cruise speed a converging pair can close within a single
    # predicted horizon), so predicted_onset_s never gets set and
    # ResolutionEngine.resolve_many() never becomes eligible on its
    # own. Same technique as scenarios/domino_effect_demo.py and
    # arrival_sequencing_demo.py: hand-build an eligible track anchored
    # on the real peak region and call ResolutionEngine.resolve()
    # directly, to still show the real, ranked candidates this
    # encounter would generate.
    # Use the *first* cycle for this rather than the peak one: by the
    # time the pair's score peaks (~90s in), they are only ~1 min from
    # passing each other, so no future horizon's predicted cluster
    # still matches them (ResolutionEngine's _matched_region legitimately
    # returns nothing -- there is no meaningful "before" state left to
    # resolve). At the first cycle, the horizon-5 prediction still
    # shows the same matched pair (lower score, but still together),
    # which is what ResolutionEngine needs to have anything to compare
    # a candidate against.
    if results_by_cycle:
        early_result = results_by_cycle[0]
        early_region = next(
            (r for r in early_result.regions_by_horizon.get(0, [])
             if r.cluster.member_callsigns == frozenset({"HVN701", "QH703"})),
            None,
        )
        if early_region is not None:
            track = FourDArhac(
                arhac_id="CROSSING-DEMO",
                status="GROWING",
                track=[early_region],
                member_aircraft=early_region.cluster.member_callsigns,
                confidence=1.0,
                peak_complexity=early_region.complexity_score,
                peak_time_s=early_result.snapshot.timestamp_s,
                predicted_onset_s=early_result.snapshot.timestamp_s + 300.0,  # +5 min
                forecast_urgency_rank=1,
                last_updated_cycle_s=early_result.snapshot.timestamp_s,
            )
            engine = ResolutionEngine(config)
            resolution_set = engine.resolve(track, early_result.snapshot, early_result.regions_by_horizon)
            print()
            print("=" * 78)
            print(f"Hand-built-track ResolutionEngine candidates (evaluated horizon={resolution_set.evaluated_horizon_min}min, see comment above for why)")
            print("=" * 78)
            for c in resolution_set.candidates[:6]:
                after_str = f"{c.complexity_after:.1f}" if c.complexity_after is not None else "n/a"
                print(f"  {c.clearance_type:8s} {c.delta_value:+7.1f}  score={c.resolution_score:+.4f}  "
                      f"before={c.complexity_before:.1f}  after={after_str}")

    output_path = Path("/mnt/user-data/outputs/crossing_airways_demo_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"preset": PRESET_KEY, "cycles": cycles_payload}, indent=2))
    print()
    print(f"Full results written to {output_path}")


if __name__ == "__main__":
    main()
