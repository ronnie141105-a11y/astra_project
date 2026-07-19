"""
Data-collection / validation script for the `arrival_sequencing` preset
(see `astra/dashboard/scenario_presets_operational.py`).

Demonstrates ASTRA's medium-term flow-management value for a pair of
in-trail aircraft on the same airway, same level, ~5 NM apart, both
converging on the sector-boundary fix AC within about a minute of each
other some 35-40 minutes from now -- a transfer-coordination workload
problem, not a separation conflict (see the preset's own docstring for
the full operational reasoning).

This script does three things, MockConnector only (no BlueSky, no
.scn files):

1.  Runs the real pipeline (`Pipeline.run_cycle`, `MockConnector` via
    `StateReader.for_mock`) for one cycle on the preset traffic and
    records the observed hotspot and how `ComplexityEngine`'s five
    components combine for it (confirms this preset's own documented
    ~40-pt plateau empirically, the same way `scenario_presets.py`
    documents having validated its other presets).
2.  Hand-builds a `FourDArhac` track anchored on that observed region
    (exactly the technique `scenarios/domino_effect_demo.py` already
    uses for a track that wouldn't otherwise clear
    `ResolutionEngine`'s normal eligibility bar) with a predicted onset
    pinned to this scenario's real ETA-to-AC, and calls
    `ResolutionEngine.resolve()` directly to get real, ranked
    HEADING/SPEED candidates for the trailing aircraft.
3.  Actually simulates the scenario twice with `MockConnector` end to
    end -- once with no intervention, once applying a speed reduction
    to the trailing aircraft for a short window -- and measures the
    resulting time gap between the two aircraft passing AC, before vs
    after.

A note on (3)'s choice of a SPEED adjustment over the HEADING vector
described narratively in the project brief: `MockConnector`'s
`_propagate_positions()` recomputes a route-following aircraft's
heading toward its next waypoint on every tick whenever
`route_waypoints` is non-empty (see `mock_connector.py`), so an `HDG`
stack command has no lasting effect on a route-following aircraft in
this simulator -- the next tick immediately re-points it back at the
route. `SPD`, in contrast, is honoured unconditionally regardless of
whether the aircraft is following a route. A real short-vector-then-
direct manoeuvre would need to temporarily clear and later restore
`route_waypoints`, which is possible (reaching into
`MockConnector`'s internals, shown below as `_vector_off_and_back`,
clearly marked) but not part of this connector's public/documented
command surface; the SPD-based adjustment achieves the same
operational goal (delay the trailing aircraft, open the gap at AC)
through a command this simulator actually supports end to end, and
"heading OR speed adjustment" is explicitly an acceptable resolution
per this scenario's own design brief.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.dashboard import scenario_geo as geo
from astra.dashboard.scenario_presets import get_preset
from astra.interface.state_reader import StateReader
from astra.pipeline import Pipeline
from astra.resolution.engine import ResolutionEngine
from astra.tracking.models import FourDArhac
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm

PRESET_KEY = "arrival_sequencing"
LEAD_CALLSIGN = "HVN123"
TRAIL_CALLSIGN = "VJC456"
SIM_STEP_S = 15.0
MAX_SIM_S = 3600.0 * 1.2  # generous cap; the flight takes ~45 min

AC_LAT, AC_LON = geo.waypoint_latlon("W1", "AC")


def _spawn(reader: StateReader) -> None:
    for ac in get_preset(PRESET_KEY)["aircraft"]:
        reader.create_aircraft(
            ac["callsign"], ac["aircraft_type"], ac["lat"], ac["lon"],
            ac["heading_deg"], ac["altitude_ft"], ac["speed_kt"],
            route_waypoints=ac.get("route_waypoints"),
        )


def _run_pipeline_snapshot(config: ASTRAConfig):
    """One pipeline cycle on the freshly-spawned preset, for the static analysis."""
    reader = StateReader.for_mock(config, sim_step_s=1.0)
    reader.connect()
    _spawn(reader)
    reader.send_command("OP")
    pipeline = Pipeline(config, route_provider=reader.get_route)
    snapshot = reader.poll()
    return pipeline.run_cycle(snapshot)


def _resolve_with_hand_built_track(config: ASTRAConfig, cycle_result, onset_s: float):
    """Same technique as scenarios/domino_effect_demo.py: hand-build an
    eligible track anchored on the real observed region, then call
    ResolutionEngine.resolve() directly. See this module's docstring
    for why this preset structurally doesn't clear ForecastEngine's own
    eligibility bar on its own (identical heading/altitude -> two of
    five complexity components are zero -> composite score plateaus
    below forecast_onset_threshold)."""
    region = cycle_result.regions_by_horizon[0][0]
    track = FourDArhac(
        arhac_id="ARRIVAL-SEQ-DEMO",
        status="GROWING",
        track=[region],
        member_aircraft=region.cluster.member_callsigns,
        confidence=1.0,
        peak_complexity=region.complexity_score,
        peak_time_s=0.0,
        predicted_onset_s=onset_s,
        forecast_urgency_rank=1,
        last_updated_cycle_s=0.0,
    )
    engine = ResolutionEngine(config)
    return engine.resolve(track, cycle_result.snapshot, cycle_result.regions_by_horizon)


def _simulate_to_ac(config: ASTRAConfig, apply_speed_reduction: bool):
    """Full MockConnector simulation from spawn until both aircraft have
    passed AC. Returns per-aircraft (closest-approach time_s, closest-
    approach distance_nm) plus the full distance-to-AC time series.

    If `apply_speed_reduction`, VJC456 (trailing) is slowed by 20 kt
    (SPD command, see module docstring) between t=300s and t=900s (a
    10-minute vector-equivalent window applied well before AC, ~25-30
    min out) then restored to its original speed.
    """
    reader = StateReader.for_mock(config, sim_step_s=SIM_STEP_S)
    reader.connect()
    _spawn(reader)
    reader.send_command("OP")

    original_trail_speed = next(
        a["speed_kt"] for a in get_preset(PRESET_KEY)["aircraft"] if a["callsign"] == TRAIL_CALLSIGN
    )
    reduced_speed = original_trail_speed - 20.0
    reduction_applied = False
    reduction_lifted = False

    closest = {LEAD_CALLSIGN: (None, float("inf")), TRAIL_CALLSIGN: (None, float("inf"))}
    series = {LEAD_CALLSIGN: [], TRAIL_CALLSIGN: []}

    t = 0.0
    while t < MAX_SIM_S:
        snapshot = reader.poll()
        if snapshot is None:
            break
        t = snapshot.timestamp_s

        if apply_speed_reduction and not reduction_applied and t >= 300.0:
            reader.send_command(f"SPD {TRAIL_CALLSIGN} {reduced_speed}")
            reduction_applied = True
        if apply_speed_reduction and reduction_applied and not reduction_lifted and t >= 900.0:
            reader.send_command(f"SPD {TRAIL_CALLSIGN} {original_trail_speed}")
            reduction_lifted = True

        both_done = True
        for callsign in (LEAD_CALLSIGN, TRAIL_CALLSIGN):
            ac = snapshot.aircraft.get(callsign)
            if ac is None:
                continue
            dist_nm = haversine_distance_nm(ac.lat, ac.lon, AC_LAT, AC_LON)
            series[callsign].append((t, dist_nm))
            if dist_nm < closest[callsign][1]:
                closest[callsign] = (t, dist_nm)
            # "Done" once past closest approach and pulling away again.
            if len(series[callsign]) < 3 or series[callsign][-1][1] <= series[callsign][-2][1]:
                both_done = False
        if both_done:
            break

    return closest, series


def main() -> None:
    config = ASTRAConfig()

    print("=" * 78)
    print(f"Preset: {PRESET_KEY}")
    print("=" * 78)
    for ac in get_preset(PRESET_KEY)["aircraft"]:
        route_len = len(ac.get("route_waypoints") or [])
        print(
            f"  {ac['callsign']:8s} {ac['aircraft_type']:5s} "
            f"lat={ac['lat']:.4f} lon={ac['lon']:.4f} hdg={ac['heading_deg']:.1f} "
            f"alt={ac['altitude_ft']:.0f}ft gs={ac['speed_kt']}kt "
            f"route_waypoints_ahead={route_len}"
        )

    # ---- 1. Static pipeline analysis on the spawned traffic ----
    cycle_result = _run_pipeline_snapshot(config)
    print()
    print("=" * 78)
    print("t=0 (observed) ComplexityEngine assessment")
    print("=" * 78)
    if not cycle_result.regions_by_horizon.get(0):
        print("  No cluster detected at horizon 0 -- unexpected, check preset spacing.")
        return
    region = cycle_result.regions_by_horizon[0][0]
    print(f"  members={sorted(region.cluster.member_callsigns)}")
    print(f"  complexity_score={region.complexity_score:.2f}  (forecast_onset_threshold={config.forecast_onset_threshold})")
    print(f"  horizontal_extent_nm={region.cluster.horizontal_extent_nm:.2f}")

    print()
    print("  score across predicted horizons (dead-reckoning/route-aware prediction):")
    for h in sorted(cycle_result.regions_by_horizon):
        regions_h = cycle_result.regions_by_horizon[h]
        score = regions_h[0].complexity_score if regions_h else None
        print(f"    horizon={h:>3d} min  score={score}")

    print()
    print(f"  TrackerEngine tracks this cycle: {len(cycle_result.tracks)}")
    for tr in cycle_result.tracks:
        print(f"    status={tr.status}  predicted_onset_s={tr.predicted_onset_s}  urgency_rank={tr.forecast_urgency_rank}")

    # ---- 2. Full baseline simulation: measure the AC transfer gap with no intervention ----
    print()
    print("=" * 78)
    print("Baseline simulation (no intervention) -- time each aircraft passes AC")
    print("=" * 78)
    baseline_closest, baseline_series = _simulate_to_ac(config, apply_speed_reduction=False)
    for cs, (t_close, d_close) in baseline_closest.items():
        eta_min = t_close / 60.0 if t_close is not None else None
        print(f"  {cs}: closest approach to AC at t={t_close}s (~{eta_min:.1f} min), {d_close:.2f} NM")
    lead_t = baseline_closest[LEAD_CALLSIGN][0]
    trail_t = baseline_closest[TRAIL_CALLSIGN][0]
    baseline_gap_s = None
    if lead_t is not None and trail_t is not None:
        baseline_gap_s = trail_t - lead_t
        print(f"  Baseline transfer gap at AC: {baseline_gap_s:.0f} s ({baseline_gap_s / 60.0:.2f} min)")

    # ---- 3. Hand-built-track resolution proposal (see module docstring) ----
    onset_s = lead_t if lead_t is not None else 1800.0
    resolution_set = _resolve_with_hand_built_track(config, cycle_result, onset_s)
    print()
    print("=" * 78)
    print(f"ResolutionEngine candidates (evaluated horizon = {resolution_set.evaluated_horizon_min} min)")
    print("=" * 78)
    ranked = resolution_set.candidates
    for c in ranked[:8]:
        after_str = f"{c.complexity_after:6.2f}" if c.complexity_after is not None else "  n/a "
        print(
            f"  {c.clearance_type:8s} {c.delta_value:+7.1f}  score={c.resolution_score:+8.4f}  "
            f"before={c.complexity_before:6.2f}  after={after_str}"
        )

    # ---- 4. Full simulation WITH the speed-based sequencing adjustment ----
    print()
    print("=" * 78)
    print("Intervention simulation: VJC456 (trailing) slowed 20 kt for a 10-min window")
    print("=" * 78)
    after_closest, after_series = _simulate_to_ac(config, apply_speed_reduction=True)
    for cs, (t_close, d_close) in after_closest.items():
        eta_min = t_close / 60.0 if t_close is not None else None
        print(f"  {cs}: closest approach to AC at t={t_close}s (~{eta_min:.1f} min), {d_close:.2f} NM")
    lead_t2 = after_closest[LEAD_CALLSIGN][0]
    trail_t2 = after_closest[TRAIL_CALLSIGN][0]
    after_gap_s = None
    if lead_t2 is not None and trail_t2 is not None:
        after_gap_s = trail_t2 - lead_t2
        print(f"  Post-adjustment transfer gap at AC: {after_gap_s:.0f} s ({after_gap_s / 60.0:.2f} min)")

    if baseline_gap_s is not None and after_gap_s is not None:
        print()
        print(f"  Gap increased by {after_gap_s - baseline_gap_s:.0f} s "
              f"({baseline_gap_s / 60.0:.2f} min -> {after_gap_s / 60.0:.2f} min) "
              f"from a single 20 kt / 10 min speed adjustment issued ~5 min after spawn, "
              f"well before AC.")

    # ---- write results ----
    output_path = Path("/mnt/user-data/outputs/arrival_sequencing_demo_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "preset": PRESET_KEY,
        "observed_region": {
            "members": sorted(region.cluster.member_callsigns),
            "complexity_score": region.complexity_score,
            "forecast_onset_threshold": config.forecast_onset_threshold,
        },
        "score_by_horizon_min": {
            h: (cycle_result.regions_by_horizon[h][0].complexity_score if cycle_result.regions_by_horizon[h] else None)
            for h in sorted(cycle_result.regions_by_horizon)
        },
        "resolution_candidates": [
            {
                "clearance_type": c.clearance_type,
                "delta_value": c.delta_value,
                "resolution_score": c.resolution_score,
                "complexity_before": c.complexity_before,
                "complexity_after": c.complexity_after,
            }
            for c in ranked
        ],
        "baseline_simulation": {
            "closest_approach_s": {cs: v[0] for cs, v in baseline_closest.items()},
            "closest_approach_nm": {cs: v[1] for cs, v in baseline_closest.items()},
            "transfer_gap_s": baseline_gap_s,
        },
        "intervention_simulation": {
            "adjustment": "SPD VJC456 -20kt for t in [300s, 900s]",
            "closest_approach_s": {cs: v[0] for cs, v in after_closest.items()},
            "closest_approach_nm": {cs: v[1] for cs, v in after_closest.items()},
            "transfer_gap_s": after_gap_s,
        },
    }
    output_path.write_text(json.dumps(payload, indent=2))
    print()
    print(f"Full results written to {output_path}")


if __name__ == "__main__":
    main()
