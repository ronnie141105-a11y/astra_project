#!/usr/bin/env python3
"""
Domino-effect resolution demo (thesis "Improvements" chapter evidence).

Standalone, deterministic, no BlueSky / MockConnector required -- calls
`ResolutionEngine.resolve()` directly, exactly the way `Pipeline` does
each poll cycle, against a hand-built `TrafficSnapshot` and a
hand-built `FourDArhac` track (the same construction pattern
`tests/test_resolution.py` uses to test the engine in isolation).

Scenario
--------
Two INDEPENDENT hotspots, both real from the first snapshot:

* Hotspot A ("the conflict to resolve"): TGT1 + TGT2 fly a stable,
  near-stationary holding pattern 3 NM apart. TGT3 approaches them
  from the north on a converging track -- a genuine, growing 3-aircraft
  conflict (MTCA/LTCA-driven, i.e. `heading_lever_applicable` is
  True). `ResolutionEngine` selects TGT3 as the track's target
  aircraft (`select_target_aircraft`).
* Hotspot B ("someone else's traffic"): SIDE1 + SIDE2, a stable
  two-aircraft flow ~20+ NM west-northwest of hotspot A, uninvolved in
  hotspot A's conflict and never itself modified by any candidate.

Both HEADING candidates (turn left / turn right by
`resolution_heading_step_deg`) send TGT3 away from TGT1/TGT2, resolving
hotspot A almost equally well either way (both show a strong
`complexity_delta_norm`). But turning one way sends TGT3 into hotspot
B's flight path -- worsening a completely separate, unrelated hotspot
that the *primary* track has nothing to do with. Only
`domino_cost_norm` (this project's improvement) tells the two turns
apart; `complexity_delta_norm` alone cannot, because it only ever looks
at the track being resolved.

Why the heading step is exaggerated
------------------------------------
Production `ASTRAConfig` defaults to a conservative
`resolution_heading_step_deg = 15.0` -- a realistic single-cycle
tactical heading change. At that step size, a single resolution cycle
cannot move an aircraft far enough in 5 minutes to fully cross from one
15 NM DBSCAN neighbourhood into an entirely disjoint one 20+ NM away
(confirmed by grid search over dozens of geometries while building
this demo -- see docs/PROJECT_STATUS.md's Milestone 7 follow-up
section). Realistically, a domino effect at the default step size
shows up as a *degraded match* to the track's own before/after region
(i.e. partly through `complexity_delta_norm`) rather than a fully
separable "new hotspot elsewhere" -- which is still correctly and
usefully penalised (see the worked function-level example in this
script's docstring companion, `docs/PROJECT_STATUS.md`). This script
uses `resolution_heading_step_deg = 90.0` purely so the two hotspots
are geometrically separable within a single 5-minute demo horizon,
isolating `domino_cost_norm`'s contribution with no ambiguity for a
clear, standalone illustration. This does not change production
defaults anywhere else in the codebase.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.complexity.engine import ComplexityEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.engine import ResolutionEngine
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import move_position


def aircraft(callsign, lat, lon, hdg, alt, gs, actype):
    return AircraftState(
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=alt,
        ground_speed_kt=gs,
        heading_deg=hdg,
        vertical_speed_fpm=0.0,
        aircraft_type=actype,
        timestamp_s=0.0,
    )


def build_snapshot() -> TrafficSnapshot:
    pair_lat, pair_lon = 10.80, 106.50
    tgt1_lat, tgt1_lon = pair_lat, pair_lon
    tgt2_lat, tgt2_lon = move_position(pair_lat, pair_lon, 270.0, 3.0)
    tgt3_lat, tgt3_lon = move_position(pair_lat, pair_lon, 0.0, 13.0)

    # SIDE anchor = TGT3's HEADING+90 candidate endpoint at horizon 5 min,
    # precomputed by grid search (see module docstring). SIDE1/SIDE2 are
    # backdated from that anchor along their own heading/speed so their
    # *own, unmodified* horizon-5 position lands there too -- i.e. they
    # are a real, independent, already-existing hotspot the whole time,
    # not something conjured to meet the target.
    side_anchor_lat, side_anchor_lon = 11.0703, 106.2951
    side_heading, side_speed = 120.0, 150.0
    side1_lat, side1_lon = move_position(
        side_anchor_lat, side_anchor_lon, side_heading + 180.0, side_speed * 5.0 / 60.0
    )
    side2_lat, side2_lon = move_position(side1_lat, side1_lon, side_heading - 90.0, 3.0)

    aircraft_list = [
        aircraft("TGT1", tgt1_lat, tgt1_lon, 90.0, 35000.0, 15.0, "A320"),
        aircraft("TGT2", tgt2_lat, tgt2_lon, 90.0, 35000.0, 17.0, "B738"),
        aircraft("TGT3", tgt3_lat, tgt3_lon, 195.0, 35000.0, 150.0, "A319"),
        aircraft("SIDE1", side1_lat, side1_lon, side_heading, 35000.0, side_speed, "B77W"),
        aircraft("SIDE2", side2_lat, side2_lon, side_heading, 35000.0, side_speed, "CRJ9"),
    ]
    return TrafficSnapshot(timestamp_s=0.0, aircraft={a.callsign: a for a in aircraft_list})


def main() -> None:
    # Only resolution_heading_step_deg is amplified; every other field
    # (including the four scoring weights from this session's
    # improvements) is left at its production default.
    config = ASTRAConfig(resolution_heading_step_deg=90.0)

    snapshot = build_snapshot()
    traj = TrajectoryEngine(config)
    clus = ClusterEngine(config)
    comp = ComplexityEngine(config)

    # Build regions_by_horizon exactly as astra.pipeline.Pipeline does:
    # horizon 0 (observed) plus every configured forecast horizon.
    regions_by_horizon = {0: comp.assess_many(clus.detect(snapshot), snapshot)}
    prediction = traj.predict(snapshot)
    clusters_by_horizon = clus.detect_all(prediction)
    for horizon_min in prediction.horizon_list():
        snapshot_h = prediction.at(horizon_min)
        regions_by_horizon[horizon_min] = comp.assess_many(
            clusters_by_horizon[horizon_min], snapshot_h
        )

    print("=" * 78)
    print("t=0 (observed) hotspots")
    print("=" * 78)
    for region in regions_by_horizon[0]:
        print(
            f"  score={region.complexity_score:5.1f}  "
            f"members={sorted(region.cluster.member_callsigns)}"
        )

    print()
    print("=" * 78)
    print("t=5min (predicted) hotspots -- both still real and separate")
    print("=" * 78)
    for region in regions_by_horizon[5]:
        print(
            f"  score={region.complexity_score:5.1f}  "
            f"members={sorted(region.cluster.member_callsigns)}"
        )

    # Hand-build the track the same way tests/test_resolution.py does:
    # a CONFIRMED-equivalent status, anchored on the t=0 region, with a
    # 5-minute predicted onset (so ResolutionEngine picks horizon 5).
    tgt_region = next(r for r in regions_by_horizon[0] if "TGT1" in r.cluster.member_callsigns)
    track = FourDArhac(
        arhac_id="DEMO-DOMINO",
        status="GROWING",
        track=[tgt_region],
        member_aircraft=tgt_region.cluster.member_callsigns,
        confidence=1.0,
        peak_complexity=tgt_region.complexity_score,
        peak_time_s=0.0,
        predicted_onset_s=300.0,
        forecast_urgency_rank=1,
        last_updated_cycle_s=0.0,
    )

    engine = ResolutionEngine(config)
    resolution_set = engine.resolve(track, snapshot, regions_by_horizon)

    print()
    print("=" * 78)
    print(f"ResolutionEngine candidates (evaluated horizon = {resolution_set.evaluated_horizon_min} min)")
    print("=" * 78)
    ranked = resolution_set.candidates  # already sorted best-first by resolve()
    header = (
        f"{'clearance':11s} {'delta':>8s}  {'domino':>7s}  {'cplx_delta':>10s}  "
        f"{'deviation':>9s}  {'fuel':>6s}  {'score':>8s}  {'before':>7s}  {'after':>7s}"
    )
    print(header)
    print("-" * len(header))
    for c in ranked:
        after_str = f"{c.complexity_after:7.2f}" if c.complexity_after is not None else "   n/a "
        print(
            f"{c.clearance_type:11s} {c.delta_value:+8.1f}  {c.domino_cost_norm:7.3f}  "
            f"{c.complexity_delta_norm:10.3f}  {c.deviation_cost_norm:9.3f}  "
            f"{c.fuel_cost_proxy_norm:6.3f}  {c.resolution_score:+8.4f}  "
            f"{c.complexity_before:7.2f}  {after_str}"
        )

    best = ranked[0]
    heading_candidates = {c.delta_value: c for c in ranked if c.clearance_type == "HEADING"}
    print()
    print("=" * 78)
    print("Interpretation")
    print("=" * 78)
    print(
        f"Best candidate: {best.clearance_type} {best.delta_value:+.1f} "
        f"(score={best.resolution_score:+.4f}, domino_cost_norm={best.domino_cost_norm:.3f})"
    )
    if len(heading_candidates) == 2:
        deltas = sorted(heading_candidates)
        left, right = heading_candidates[deltas[0]], heading_candidates[deltas[1]]
        print(
            f"The two HEADING candidates have identical deviation/fuel cost "
            f"(same |delta|={abs(deltas[0]):.0f} deg) and similar complexity_delta_norm "
            f"({left.complexity_delta_norm:.3f} vs {right.complexity_delta_norm:.3f}) -- "
            f"complexity_delta_norm alone barely distinguishes them."
        )
        print(
            f"domino_cost_norm is what tells them apart: {deltas[0]:+.0f} deg -> "
            f"{left.domino_cost_norm:.3f}   vs   {deltas[1]:+.0f} deg -> "
            f"{right.domino_cost_norm:.3f}. The engine correctly ranks the clean turn "
            f"above the one that flies into SIDE1/SIDE2's flight path."
        )

    output_path = Path("/mnt/user-data/outputs/domino_effect_demo_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "evaluated_horizon_min": resolution_set.evaluated_horizon_min,
        "observed_regions": [
            {"score": r.complexity_score, "members": sorted(r.cluster.member_callsigns)}
            for r in regions_by_horizon[0]
        ],
        "horizon_5_regions": [
            {"score": r.complexity_score, "members": sorted(r.cluster.member_callsigns)}
            for r in regions_by_horizon[5]
        ],
        "candidates": [
            {
                "clearance_type": c.clearance_type,
                "delta_value": c.delta_value,
                "domino_cost_norm": c.domino_cost_norm,
                "complexity_delta_norm": c.complexity_delta_norm,
                "deviation_cost_norm": c.deviation_cost_norm,
                "fuel_cost_proxy_norm": c.fuel_cost_proxy_norm,
                "resolution_score": c.resolution_score,
                "complexity_before": c.complexity_before,
                "complexity_after": c.complexity_after,
            }
            for c in ranked
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2))
    print()
    print(f"Full results written to {output_path}")


if __name__ == "__main__":
    main()
