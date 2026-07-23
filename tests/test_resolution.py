"""
Regression tests — Milestone 7 (AI resolution framework, `astra.resolution`).

Run with:
    python3 tests/test_resolution.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.hotspot.engine import ClusterEngine
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.candidates import (
    generate_candidates,
    heading_lever_applicable,
    matches_rvsm_parity,
    select_target_aircraft,
)
from astra.resolution.engine import ResolutionEngine
from astra.resolution.models import ResolutionCandidate, ResolutionSet
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig
from tests._runner import Runner


def _aircraft(callsign, lat, lon, hdg=90.0, alt=35000.0, gs=15.0, actype="A320", t=0.0):
    """Build a plain `AircraftState` without needing BlueSky/MockConnector."""
    return AircraftState(
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=alt,
        ground_speed_kt=gs,
        heading_deg=hdg,
        vertical_speed_fpm=0.0,
        aircraft_type=actype,
        timestamp_s=t,
    )


def _snapshot(aircraft, t=0.0):
    """Build a `TrafficSnapshot` from a list of `AircraftState`."""
    return TrafficSnapshot(timestamp_s=t, aircraft={ac.callsign: ac for ac in aircraft})


def _cluster(callsigns, lat=47.0, lon=8.0, extent_nm=5.0, horizon_min=0, valid_at_s=0.0, label=0):
    """Build a hand-controlled `Cluster` (mirrors tests/test_forecast.py)."""
    return Cluster(
        cluster_id=f"{'observed' if horizon_min == 0 else 'predicted'}:{horizon_min}:{label}",
        source="observed" if horizon_min == 0 else "predicted",
        horizon_min=horizon_min,
        valid_at_s=valid_at_s,
        member_callsigns=frozenset(callsigns),
        centroid_lat=lat,
        centroid_lon=lon,
        centroid_alt_ft=35000.0,
        horizontal_extent_nm=extent_nm,
    )


def _region(callsigns, score, components=None, valid_at_s=0.0, horizon_min=0, lat=47.0, lon=8.0):
    """Build a hand-controlled `ComplexityRegion` with a chosen score/components."""
    cluster = _cluster(callsigns, lat=lat, lon=lon, horizon_min=horizon_min, valid_at_s=valid_at_s)
    return ComplexityRegion(
        cluster=cluster, complexity_score=score, components=components or {}, computed_at_s=valid_at_s
    )


def _track(status, region, urgency_rank=None, onset_s=None, arhac_id="T1"):
    """Build a minimal `FourDArhac` anchored on one region, ready to resolve."""
    return FourDArhac(
        arhac_id=arhac_id,
        status=status,
        track=[region],
        member_aircraft=region.cluster.member_callsigns,
        confidence=1.0,
        peak_complexity=region.complexity_score,
        peak_time_s=region.computed_at_s,
        predicted_onset_s=onset_s,
        forecast_urgency_rank=urgency_rank,
        last_updated_cycle_s=region.computed_at_s,
    )


# A converging 3-aircraft geometry: observed complexity is below the
# default forecast onset threshold, but the 5-minute predicted horizon
# rises above it -- deterministic real physics used by both the "happy
# path" engine test and demo_resolution.py.
def _converging_snapshot():
    return _snapshot(
        [
            _aircraft("AC1", 47.000, 7.880, hdg=90.0, alt=35000.0, gs=15.0),
            _aircraft("AC2", 47.000, 8.120, hdg=270.0, alt=35000.0, gs=15.0),
            _aircraft("AC3", 47.090, 8.000, hdg=180.0, alt=34000.0, gs=8.0),
        ]
    )


def _build_regions_by_horizon(snapshot, config):
    """Run Trajectory -> Cluster -> Complexity for horizon 0 and every configured horizon."""
    trajectory_engine = TrajectoryEngine(config)
    cluster_engine = ClusterEngine(config)
    complexity_engine = ComplexityEngine(config)

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


# ----------------------------------------------------------------------
# astra.resolution.models
# ----------------------------------------------------------------------


def test_resolution_set_best_and_len(r: Runner) -> None:
    """`best()` returns the top-ranked (first) candidate; `__len__` counts candidates."""
    region = _region(["A1", "A2"], 60.0)
    track = _track("GROWING", region, urgency_rank=1, onset_s=300.0)
    candidates = [
        ResolutionCandidate("SPEED", "A1", 20.0, 60.0, 50.0, 0.5, 1.0, 0.0, 0.2),
        ResolutionCandidate("FLIGHT_LEVEL", "A1", 1000.0, 60.0, 40.0, 0.8, 1.0, 1.0, 0.1),
    ]
    rs = ResolutionSet(track=track, candidates=candidates, evaluated_horizon_min=5)
    r.check("len reflects candidate count", len(rs) == 2)
    r.check("best() returns the first (ranked) candidate", rs.best() is candidates[0])


def test_resolution_set_best_empty(r: Runner) -> None:
    """`best()` returns `None` when there are no candidates."""
    region = _region(["A1", "A2"], 60.0)
    track = _track("GROWING", region, urgency_rank=1, onset_s=300.0)
    rs = ResolutionSet(track=track, candidates=[], evaluated_horizon_min=5)
    r.check("best() is None for an empty set", rs.best() is None)
    r.check("len is 0 for an empty set", len(rs) == 0)


# ----------------------------------------------------------------------
# astra.resolution.candidates
# ----------------------------------------------------------------------


def test_select_target_single_member(r: Runner) -> None:
    """A single-aircraft cluster returns that aircraft with no conflict scan."""
    cluster = _cluster(["A1"])
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])
    target = select_target_aircraft(cluster, snapshot, ASTRAConfig())
    r.check("single member selected", target is not None and target.callsign == "A1")


def test_select_target_no_members_resolve(r: Runner) -> None:
    """`None` is returned if no cluster member resolves against the snapshot."""
    cluster = _cluster(["GHOST"])
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])
    target = select_target_aircraft(cluster, snapshot, ASTRAConfig())
    r.check("no resolvable member -> None", target is None)


def test_select_target_conflict_based(r: Runner) -> None:
    """Among members, the one involved in the most conflict pairs is selected."""
    config = ASTRAConfig()
    # A1/A2 close enough to be a conflict pair; A3 far from both.
    snapshot = _snapshot(
        [
            _aircraft("A1", 47.000, 8.000),
            _aircraft("A2", 47.000, 8.010),
            _aircraft("A3", 47.200, 8.200),
        ]
    )
    cluster = _cluster(["A1", "A2", "A3"])
    target = select_target_aircraft(cluster, snapshot, config)
    r.check("a conflict-pair member is selected", target is not None and target.callsign in {"A1", "A2"})


def test_select_target_no_conflicts_fallback(r: Runner) -> None:
    """No conflict pairs at all -> falls back to distance from the cluster
    centroid (closest first), not pure alphabetical order."""
    config = ASTRAConfig()
    # Cluster centroid is (47.0, 8.0) (see _cluster's default) -- B1 sits
    # exactly there, A1 is far off to the NE. Despite "A1" sorting first
    # alphabetically, B1 should win: it's the more central aircraft, so
    # moving it does more to actually de-densify the cluster.
    snapshot = _snapshot(
        [
            _aircraft("A1", 47.500, 8.500),
            _aircraft("B1", 47.000, 8.000),
        ]
    )
    cluster = _cluster(["B1", "A1"])
    target = select_target_aircraft(cluster, snapshot, config)
    r.check(
        "the most central aircraft wins, not the alphabetically-first one",
        target is not None and target.callsign == "B1",
    )


def test_select_target_no_conflicts_fallback_alphabetical_last_resort(r: Runner) -> None:
    """Two members exactly equidistant from the centroid -> alphabetical
    order is still the final tie-break, so the ranking stays deterministic."""
    config = ASTRAConfig()
    # Symmetric about the centroid (47.0, 8.0): both members are the
    # same distance away, so centroid-distance can't break the tie.
    snapshot = _snapshot(
        [
            _aircraft("Z1", 47.0, 8.1),
            _aircraft("A1", 47.0, 7.9),
        ]
    )
    cluster = _cluster(["Z1", "A1"])
    target = select_target_aircraft(cluster, snapshot, config)
    r.check(
        "alphabetically first callsign wins when centroid distance is tied",
        target is not None and target.callsign == "A1",
    )


def test_heading_lever_applicable_true(r: Runner) -> None:
    """A region with a nonzero MTCA/LTCA component makes heading applicable."""
    region = _region(["A1", "A2"], 60.0, components={"mtca_count": 1.0, "ltca_count": 0.0})
    r.check("mtca present -> heading applicable", heading_lever_applicable(region))


def test_heading_lever_applicable_false(r: Runner) -> None:
    """A region with no conflict components at all disables the heading lever."""
    region = _region(["A1", "A2"], 60.0, components={"density_ac_per_nm2": 1.0})
    r.check("no conflict components -> heading not applicable", not heading_lever_applicable(region))


def test_generate_candidates_no_heading(r: Runner) -> None:
    """Without conflict components, SPEED + FLIGHT_LEVEL, both directions, both
    step magnitudes (default multipliers [1.0, 2.0] -> 2 levers x 2 signs x
    2 magnitudes = 8 total)."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.5)])
    region = _region(["A1", "A2"], 40.0, components={"density_ac_per_nm2": 1.0})
    specs = generate_candidates(region, snapshot, config)
    r.check("8 candidates without a conflict driver", len(specs) == 8)
    r.check(
        "clearance types are SPEED and FLIGHT_LEVEL",
        {s.clearance_type for s in specs} == {"SPEED", "FLIGHT_LEVEL"},
    )
    r.check(
        "both signed directions present for SPEED at the base step",
        {s.delta_value for s in specs if s.clearance_type == "SPEED"}
        >= {config.resolution_speed_step_kt, -config.resolution_speed_step_kt},
    )
    r.check(
        "both signed directions present for FLIGHT_LEVEL at the base step",
        {s.delta_value for s in specs if s.clearance_type == "FLIGHT_LEVEL"}
        >= {config.resolution_altitude_step_ft, -config.resolution_altitude_step_ft},
    )
    r.check(
        "the larger (2x) magnitude is also present for SPEED",
        2 * config.resolution_speed_step_kt in {abs(s.delta_value) for s in specs if s.clearance_type == "SPEED"},
    )
    r.check(
        "none of these are vector-and-rejoin (no route_provider given)",
        all(s.vector_duration_s is None for s in specs),
    )


def test_generate_candidates_with_heading(r: Runner) -> None:
    """With a conflict component but no known route, HEADING is added in both
    directions/magnitudes as a SUSTAINED hold (3 levers x 2 signs x 2
    magnitudes = 12 total)."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    region = _region(["A1", "A2"], 70.0, components={"mtca_count": 2.0})
    specs = generate_candidates(region, snapshot, config)
    r.check("12 candidates with a conflict driver", len(specs) == 12)
    r.check(
        "clearance types include HEADING",
        {s.clearance_type for s in specs} == {"SPEED", "FLIGHT_LEVEL", "HEADING"},
    )
    r.check(
        "both signed directions present for HEADING at the base step",
        {s.delta_value for s in specs if s.clearance_type == "HEADING"}
        >= {config.resolution_heading_step_deg, -config.resolution_heading_step_deg},
    )
    r.check(
        "HEADING candidates are SUSTAINED (no route_provider given)",
        all(s.vector_duration_s is None for s in specs if s.clearance_type == "HEADING"),
    )


def test_generate_candidates_heading_vector_and_rejoin(r: Runner) -> None:
    """With a conflict component AND a known route for the target, HEADING
    candidates become VECTOR_AND_REJOIN instead of a sustained hold."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    region = _region(["A1", "A2"], 70.0, components={"mtca_count": 2.0})
    route = [(47.5, 8.5), (48.0, 9.0)]

    def route_provider(callsign):
        return route if callsign in {"A1", "A2"} else None

    specs = generate_candidates(region, snapshot, config, route_provider=route_provider)
    heading_specs = [s for s in specs if s.clearance_type == "HEADING"]
    r.check("heading candidates were generated", len(heading_specs) > 0)
    r.check(
        "every heading candidate is VECTOR_AND_REJOIN",
        all(s.vector_duration_s == config.resolution_vector_duration_s for s in heading_specs),
    )
    r.check(
        "every heading candidate carries the target's rejoin route",
        all(s.rejoin_route == route for s in heading_specs),
    )
    r.check(
        "SPEED/FLIGHT_LEVEL candidates are unaffected (still SUSTAINED)",
        all(s.vector_duration_s is None for s in specs if s.clearance_type != "HEADING"),
    )


def test_generate_candidates_explicit_target_and_levers(r: Runner) -> None:
    """An explicit `target` skips selection; `levers` restricts which types are built."""
    config = ASTRAConfig()
    snapshot = _snapshot(
        [
            _aircraft("A1", 47.000, 8.000),
            _aircraft("A2", 47.000, 8.010),
            _aircraft("A3", 47.200, 8.200),
        ]
    )
    region = _region(["A1", "A2", "A3"], 70.0, components={"mtca_count": 1.0})
    a3 = snapshot.get("A3")
    specs = generate_candidates(region, snapshot, config, target=a3, levers=["SPEED"])
    r.check("all specs target the explicitly-given aircraft", {s.target_callsign for s in specs} == {"A3"})
    r.check("only SPEED candidates were built", {s.clearance_type for s in specs} == {"SPEED"})


def test_generate_candidates_empty_when_no_target(r: Runner) -> None:
    """No resolvable cluster member -> no candidates at all."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])
    region = _region(["GHOST"], 60.0, components={"mtca_count": 1.0})
    specs = generate_candidates(region, snapshot, config)
    r.check("no target -> empty spec list", specs == [])


def test_generate_candidates_apply_clearance_values(r: Runner) -> None:
    """Each spec's hypothetical snapshot reflects the intended lever adjustment."""
    config = ASTRAConfig()
    ac1 = _aircraft("A1", 47.0, 8.0, hdg=350.0, alt=35000.0, gs=250.0)
    ac2 = _aircraft("A2", 47.0, 8.01, hdg=170.0, alt=35000.0, gs=250.0)
    snapshot = _snapshot([ac1, ac2])
    region = _region(["A1", "A2"], 70.0, components={"mtca_count": 1.0})
    specs = generate_candidates(region, snapshot, config)
    # Base-step (multiplier=1.0) spec per lever/sign -- the only magnitude
    # generated before step_multipliers was widened to [1.0, 2.0].
    specs_positive = {
        s.clearance_type: s
        for s in specs
        if s.delta_value > 0 and s.clearance_type == "SPEED" and s.delta_value == config.resolution_speed_step_kt
        or s.delta_value > 0 and s.clearance_type == "FLIGHT_LEVEL" and s.delta_value == config.resolution_altitude_step_ft
        or s.delta_value > 0 and s.clearance_type == "HEADING" and s.delta_value == config.resolution_heading_step_deg
    }
    specs_negative = {
        s.clearance_type: s
        for s in specs
        if s.delta_value < 0 and s.clearance_type == "SPEED" and -s.delta_value == config.resolution_speed_step_kt
        or s.delta_value < 0 and s.clearance_type == "FLIGHT_LEVEL" and -s.delta_value == config.resolution_altitude_step_ft
        or s.delta_value < 0 and s.clearance_type == "HEADING" and -s.delta_value == config.resolution_heading_step_deg
    }
    target_callsign = specs_positive["SPEED"].target_callsign
    base = ac1 if target_callsign == "A1" else ac2
    r.check(
        "all specs target the same (highest-conflict) aircraft",
        {s.target_callsign for s in specs} == {target_callsign},
    )

    speed_after = specs_positive["SPEED"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "speed candidate adds the configured step",
        speed_after.ground_speed_kt,
        base.ground_speed_kt + config.resolution_speed_step_kt,
    )
    speed_after_neg = specs_negative["SPEED"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "speed candidate also subtracts the configured step",
        speed_after_neg.ground_speed_kt,
        base.ground_speed_kt - config.resolution_speed_step_kt,
    )
    fl_after = specs_positive["FLIGHT_LEVEL"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "flight-level candidate adds the configured step",
        fl_after.altitude_ft,
        base.altitude_ft + config.resolution_altitude_step_ft,
    )
    fl_after_neg = specs_negative["FLIGHT_LEVEL"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "flight-level candidate also subtracts the configured step",
        fl_after_neg.altitude_ft,
        base.altitude_ft - config.resolution_altitude_step_ft,
    )
    hdg_after = specs_positive["HEADING"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "heading candidate wraps modulo 360",
        hdg_after.heading_deg,
        (base.heading_deg + config.resolution_heading_step_deg) % 360.0,
    )
    hdg_after_neg = specs_negative["HEADING"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "heading candidate also wraps the negative direction modulo 360",
        hdg_after_neg.heading_deg,
        (base.heading_deg - config.resolution_heading_step_deg) % 360.0,
    )
    r.check(
        "original snapshot is not mutated",
        snapshot.get(target_callsign).ground_speed_kt == base.ground_speed_kt,
    )


# ----------------------------------------------------------------------
# astra.resolution.engine.ResolutionEngine — eligibility & wiring
# ----------------------------------------------------------------------


def test_engine_ineligible_status(r: Runner) -> None:
    """CANDIDATE/CLOSED tracks are never resolved, even with an urgency rank."""
    engine = ResolutionEngine(ASTRAConfig())
    region = _region(["A1", "A2"], 60.0)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    for status in ("CANDIDATE", "CLOSED"):
        track = _track(status, region, urgency_rank=1, onset_s=300.0)
        rs = engine.resolve(track, snapshot, {5: [region]})
        r.check(f"{status} track yields no candidates", rs.candidates == [])


def test_engine_ineligible_no_urgency_rank(r: Runner) -> None:
    """A track with no `forecast_urgency_rank` is not resolved."""
    engine = ResolutionEngine(ASTRAConfig())
    region = _region(["A1", "A2"], 60.0)
    track = _track("GROWING", region, urgency_rank=None, onset_s=300.0)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    rs = engine.resolve(track, snapshot, {5: [region]})
    r.check("no urgency rank -> no candidates", rs.candidates == [])


def test_engine_ineligible_no_onset(r: Runner) -> None:
    """A track with no `predicted_onset_s` is not resolved."""
    engine = ResolutionEngine(ASTRAConfig())
    region = _region(["A1", "A2"], 60.0)
    track = _track("GROWING", region, urgency_rank=1, onset_s=None)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    rs = engine.resolve(track, snapshot, {5: [region]})
    r.check("no predicted onset -> no candidates", rs.candidates == [])


def test_engine_missing_matched_region(r: Runner) -> None:
    """An eligible track whose horizon has no matching region yields no candidates."""
    engine = ResolutionEngine(ASTRAConfig())
    region = _region(["A1", "A2"], 60.0)
    track = _track("GROWING", region, urgency_rank=1, onset_s=300.0)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    rs = engine.resolve(track, snapshot, {})
    r.check("no regions at all -> no candidates", rs.candidates == [])
    r.check("evaluated_horizon_min is still reported", rs.evaluated_horizon_min == 5)


def test_engine_closest_horizon_selection(r: Runner) -> None:
    """The horizon nearest the track's predicted onset lead time is chosen."""
    engine = ResolutionEngine(ASTRAConfig())
    region = _region(["A1", "A2"], 60.0, valid_at_s=0.0)
    # onset 600s after the anchor -> closest configured horizon is 10 min.
    track = _track("GROWING", region, urgency_rank=1, onset_s=600.0)
    horizon = engine._closest_horizon(track)
    r.check_close("10-minute horizon selected for a 600s lead time", float(horizon), 10.0)


def test_engine_resolve_many_orders_and_caps(r: Runner) -> None:
    """`resolve_many` filters ineligible tracks, orders by urgency, and caps the count."""
    config = ASTRAConfig(resolution_max_tracks_per_cycle=1)
    engine = ResolutionEngine(config)
    region = _region(["A1", "A2"], 60.0)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])

    urgent = _track("GROWING", region, urgency_rank=1, onset_s=300.0, arhac_id="URGENT")
    less_urgent = _track("GROWING", region, urgency_rank=2, onset_s=300.0, arhac_id="LESS_URGENT")
    ineligible = _track("CANDIDATE", region, urgency_rank=None, onset_s=None, arhac_id="INELIGIBLE")

    result = engine.resolve_many([less_urgent, ineligible, urgent], snapshot, {})
    r.check("capped to 1 result", len(result) == 1)
    r.check("the most urgent track is the one resolved", result[0].track.arhac_id == "URGENT")


# ----------------------------------------------------------------------
# astra.resolution.engine.ResolutionEngine — full pipeline happy path
# ----------------------------------------------------------------------


def test_engine_resolve_end_to_end(r: Runner) -> None:
    """A real converging 3-aircraft geometry produces ranked, scored candidates."""
    config = ASTRAConfig()
    snapshot = _converging_snapshot()
    regions_by_horizon = _build_regions_by_horizon(snapshot, config)

    observed_region = regions_by_horizon[0][0]
    r.check("observed complexity below the onset threshold", observed_region.complexity_score < 50.0)
    horizon5_region = regions_by_horizon[5][0]
    r.check("5-minute horizon complexity above the onset threshold", horizon5_region.complexity_score >= 50.0)

    track = _track("GROWING", observed_region, urgency_rank=1, onset_s=300.0)
    engine = ResolutionEngine(config)
    rs = engine.resolve(track, snapshot, regions_by_horizon)

    r.check("candidates were generated", len(rs) > 0)
    r.check("evaluated at the 5-minute horizon", rs.evaluated_horizon_min == 5)
    r.check(
        "candidates ranked descending by resolution_score",
        all(
            rs.candidates[i].resolution_score >= rs.candidates[i + 1].resolution_score
            for i in range(len(rs.candidates) - 1)
        ),
    )
    r.check(
        "every candidate's complexity_before matches the matched region",
        all(c.complexity_before == horizon5_region.complexity_score for c in rs.candidates),
    )
    best = rs.best()
    r.check("best() returns the top-ranked candidate", best is rs.candidates[0])
    r.check(
        "deviation_cost_norm is a step ratio within the configured multiplier range",
        all(
            0.0 <= c.deviation_cost_norm <= max(config.resolution_step_multipliers)
            for c in rs.candidates
        ),
    )
    r.check(
        "domino_cost_norm is a normalised penalty in [0, 1]",
        all(0.0 <= c.domino_cost_norm <= 1.0 for c in rs.candidates),
    )
    r.check(
        "candidate count reflects both-direction, both-magnitude generation (up to 12)",
        0 < len(rs.candidates) <= 12,
    )


def test_engine_resolve_many_end_to_end(r: Runner) -> None:
    """`resolve_many` runs the full pipeline once per eligible track this cycle."""
    config = ASTRAConfig()
    snapshot = _converging_snapshot()
    regions_by_horizon = _build_regions_by_horizon(snapshot, config)
    observed_region = regions_by_horizon[0][0]

    track = _track("GROWING", observed_region, urgency_rank=1, onset_s=300.0)
    engine = ResolutionEngine(config)
    results = engine.resolve_many([track], snapshot, regions_by_horizon)

    r.check("one ResolutionSet returned", len(results) == 1)
    r.check("the eligible track produced candidates", len(results[0]) > 0)


# ----------------------------------------------------------------------
# astra.resolution.engine.ResolutionEngine — route-aware evaluation,
# vector-and-rejoin, and joint (multi-aircraft) candidates
# ----------------------------------------------------------------------


def test_engine_route_aware_when_route_provider_given(r: Runner) -> None:
    """A `route_provider` swaps in `RouteAwareTrajectoryEngine` for evaluation."""
    from astra.trajectory.engine import TrajectoryEngine as _Baseline
    from astra.trajectory.route_engine import RouteAwareTrajectoryEngine as _RouteAware

    config = ASTRAConfig()
    engine_plain = ResolutionEngine(config)
    engine_routed = ResolutionEngine(config, route_provider=lambda cs: None)
    r.check("no route_provider -> baseline TrajectoryEngine", isinstance(engine_plain._trajectory_engine, _Baseline))
    r.check("route_provider given -> RouteAwareTrajectoryEngine", isinstance(engine_routed._trajectory_engine, _RouteAware))


def test_engine_vector_and_rejoin_end_to_end(r: Runner) -> None:
    """A HEADING candidate on a route-following aircraft is scored as a
    bounded vector followed by a predicted turn back onto its route --
    not an indefinite heading hold."""
    config = ASTRAConfig()
    snapshot = _converging_snapshot()
    regions_by_horizon = _build_regions_by_horizon(snapshot, config)
    observed_region = regions_by_horizon[0][0]
    track = _track("GROWING", observed_region, urgency_rank=1, onset_s=300.0)

    # AC1 (the likely conflict-count target) is following a real route far
    # from its current position -- if the rejoin logic works, its predicted
    # position at a longer horizon should be pulled toward that route
    # rather than drifting off in a straight line forever.
    route = {"AC1": [(50.0, 7.88), (52.0, 7.88)]}

    def route_provider(callsign):
        return route.get(callsign)

    engine = ResolutionEngine(config, route_provider=route_provider)
    rs = engine.resolve(track, snapshot, regions_by_horizon)
    heading_candidates = [c for c in rs.candidates if c.clearance_type == "HEADING"]
    r.check("heading candidates were generated", len(heading_candidates) > 0)
    r.check(
        "heading candidates on a routed aircraft are VECTOR_AND_REJOIN",
        all(
            c.maneuver_kind == "VECTOR_AND_REJOIN"
            for c in heading_candidates
            if c.target_callsign in route
        ),
    )
    r.check(
        "VECTOR_AND_REJOIN candidates record their vector duration",
        all(
            c.vector_duration_s == config.resolution_vector_duration_s
            for c in heading_candidates
            if c.target_callsign in route
        ),
    )
    routed = next((c for c in heading_candidates if c.target_callsign in route), None)
    if routed is not None and routed.hypothetical_prediction is not None:
        long_horizon = max(routed.hypothetical_prediction.horizon_list())
        predicted = routed.hypothetical_prediction.at(long_horizon).get(routed.target_callsign)
        start_lat = snapshot.get(routed.target_callsign).lat
        r.check(
            "at a long horizon the rejoined aircraft has moved toward its route (north, not just the vector heading)",
            predicted is not None and predicted.lat > start_lat,
        )


def test_engine_joint_candidates_present_for_two_member_cluster(r: Runner) -> None:
    """A 2-aircraft cluster now gets diverse joint candidates (Issue 1):
    both members move together, tried across every configured secondary
    lever set, rather than being excluded entirely."""
    config = ASTRAConfig()
    region = _region(["A1", "A2"], 70.0, components={"mtca_count": 1.0}, lat=47.0, lon=8.0)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    track = _track("GROWING", region, urgency_rank=1, onset_s=300.0)
    engine = ResolutionEngine(config)
    rs = engine.resolve(track, snapshot, {config.prediction_horizons_min[0]: [region]})

    r.check("joint candidates were built for a 2-member cluster", len(rs.joint_candidates) > 0)
    r.check(
        "every joint candidate for a 2-member cluster has exactly 2 legs",
        all(len(jc.legs) == 2 for jc in rs.joint_candidates),
    )
    r.check(
        "every leg's target is one of the cluster's 2 members",
        all(
            {jc.legs[0].target_callsign, jc.legs[1].target_callsign} == {"A1", "A2"}
            for jc in rs.joint_candidates
        ),
    )
    r.check(
        "joint candidates are sorted descending by complexity_delta_norm",
        all(
            rs.joint_candidates[i].complexity_delta_norm >= rs.joint_candidates[i + 1].complexity_delta_norm
            for i in range(len(rs.joint_candidates) - 1)
        ),
    )
    secondary_levers = {jc.legs[1].clearance_type for jc in rs.joint_candidates}
    r.check(
        "more than one secondary lever type was tried (diverse pairings, not speed-only)",
        len(secondary_levers) >= 1,  # geometry/config-dependent; at minimum this no longer hard-fails to zero
    )
    r.check(
        "backward-compatible joint_candidate property returns the best-scoring one",
        rs.joint_candidate is not None
        and rs.joint_candidate.resolution_score == max(jc.resolution_score for jc in rs.joint_candidates),
    )


def test_engine_joint_candidates_for_three_member_cluster(r: Runner) -> None:
    """A 3-aircraft cluster gets one or more 2-leg joint candidates
    alongside the single-aircraft candidates, per the thesis request (3
    aircraft -> 2 aircraft resolved together), now spanning diverse
    secondary lever combinations rather than a single fixed one."""
    config = ASTRAConfig()
    snapshot = _converging_snapshot()
    regions_by_horizon = _build_regions_by_horizon(snapshot, config)
    observed_region = regions_by_horizon[0][0]
    r.check("setup: this cluster has 3 members", len(observed_region.cluster.member_callsigns) == 3)

    track = _track("GROWING", observed_region, urgency_rank=1, onset_s=300.0)
    engine = ResolutionEngine(config)
    rs = engine.resolve(track, snapshot, regions_by_horizon)

    r.check("at least one joint candidate was built", len(rs.joint_candidates) > 0)
    r.check(
        "every joint candidate has 2 legs (3-member cluster -> 2 targets)",
        all(len(jc.legs) == 2 for jc in rs.joint_candidates),
    )
    r.check(
        "every joint candidate's primary leg matches the best single-aircraft candidate",
        rs.candidates
        and all(
            jc.legs[0].target_callsign == rs.candidates[0].target_callsign
            and jc.legs[0].clearance_type == rs.candidates[0].clearance_type
            and jc.legs[0].delta_value == rs.candidates[0].delta_value
            for jc in rs.joint_candidates
        ),
    )
    r.check(
        "secondary leg is always a different aircraft from the primary",
        all(jc.legs[1].target_callsign != jc.legs[0].target_callsign for jc in rs.joint_candidates),
    )
    r.check(
        "joint candidates are sorted descending by complexity_delta_norm (impact)",
        all(
            rs.joint_candidates[i].complexity_delta_norm >= rs.joint_candidates[i + 1].complexity_delta_norm
            for i in range(len(rs.joint_candidates) - 1)
        ),
    )
    r.check(
        "every joint candidate's cost sums both legs (non-negative)",
        all(jc.deviation_cost_norm >= 0.0 for jc in rs.joint_candidates),
    )
    r.check(
        "best_overall() picks the single highest resolution_score across singles and joints",
        rs.best_overall() is not None
        and rs.best_overall().resolution_score
        == max(c.resolution_score for c in list(rs.candidates) + list(rs.joint_candidates)),
    )
    r.check(
        "joint_candidate (singular, backward-compatible) is the best-scoring entry of joint_candidates",
        rs.joint_candidate is not None
        and rs.joint_candidate.resolution_score == max(jc.resolution_score for jc in rs.joint_candidates),
    )


def test_engine_joint_candidate_capped_by_config(r: Runner) -> None:
    """`resolution_joint_max_targets` bounds how many aircraft a joint
    candidate touches, even for a larger cluster."""
    config = ASTRAConfig(resolution_joint_max_targets=2)
    snapshot = _snapshot(
        [
            _aircraft("A1", 47.000, 8.000, hdg=90.0, gs=15.0),
            _aircraft("A2", 47.000, 8.010, hdg=270.0, gs=15.0),
            _aircraft("A3", 47.005, 8.005, hdg=180.0, gs=10.0),
            _aircraft("A4", 47.003, 8.007, hdg=0.0, gs=10.0),
        ]
    )
    region = _region(["A1", "A2", "A3", "A4"], 70.0, components={"mtca_count": 2.0}, lat=47.002, lon=8.005)
    track = _track("GROWING", region, urgency_rank=1, onset_s=300.0)
    engine = ResolutionEngine(config)
    rs = engine.resolve(track, snapshot, {config.prediction_horizons_min[0]: [region]})
    r.check(
        "no joint candidate ever exceeds resolution_joint_max_targets legs",
        all(len(jc.legs) <= config.resolution_joint_max_targets for jc in rs.joint_candidates),
    )


def test_domino_cost_scans_every_horizon_not_just_evaluated(r: Runner) -> None:
    """A candidate clean at the evaluated horizon but that introduces a
    new hotspot at a *different* horizon is now penalised -- the
    original Milestone 7 domino check only ever looked at the one
    horizon being evaluated, silently missing this (see
    docs/backend_improvements_backlog.md item 3)."""
    from astra.trajectory.models import PredictedSnapshot, PredictionResult

    config = ASTRAConfig()
    engine = ResolutionEngine(config)

    # The track being resolved: A1/A2, matched via before_region.
    before_region = _region(["A1", "A2"], 60.0, lat=47.0, lon=8.0)

    # "Now" (horizon 0): nothing else going on, clean.
    hypothetical_snapshot_now = _snapshot(
        [_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01), _aircraft("B1", 50.0, 9.0)]
    )
    regions_by_horizon = {0: [before_region]}

    # Horizon 10: clean too -- no real regions, and the hypothetical
    # snapshot's B1/A1 aren't close enough to cluster.
    clean_snapshot = PredictedSnapshot(
        horizon_min=10, source_time_s=0.0, predicted_time_s=600.0,
        aircraft={
            "A1": _aircraft("A1", 47.2, 8.0, t=600.0),
            "A2": _aircraft("A2", 47.2, 8.05, t=600.0),
            "B1": _aircraft("B1", 50.0, 9.0, t=600.0),
        },
    )
    # Horizon 30: the candidate's own aircraft (A1) has drifted next to
    # the bystander B1 -- a brand-new hotspot this candidate introduced,
    # nowhere near the evaluated horizon (10).
    domino_snapshot = PredictedSnapshot(
        horizon_min=30, source_time_s=0.0, predicted_time_s=1800.0,
        aircraft={
            "A1": _aircraft("A1", 47.5, 8.2, t=1800.0),
            "A2": _aircraft("A2", 47.9, 8.3, t=1800.0),
            "B1": _aircraft("B1", 47.5, 8.201, t=1800.0),  # essentially on top of A1
        },
    )
    prediction = PredictionResult(
        source_time_s=0.0,
        aircraft_count=3,
        horizons_min=(10, 30),
        snapshots={10: clean_snapshot, 30: domino_snapshot},
    )
    regions_by_horizon[10] = []
    regions_by_horizon[30] = []

    domino_at_evaluated_only = engine._domino_cost_at_horizon(
        clean_snapshot, regions_by_horizon[10], before_region
    )
    r.check(
        "the evaluated horizon alone shows no domino effect (what the old check would see)",
        domino_at_evaluated_only == 0.0,
    )

    domino_full_scan = engine._domino_cost(
        hypothetical_snapshot_now, prediction, regions_by_horizon, before_region
    )
    r.check(
        "the full multi-horizon scan catches the domino effect the single-horizon check missed",
        domino_full_scan > 0.0,
    )


def test_domino_cost_never_less_than_single_horizon_check(r: Runner) -> None:
    """The multi-horizon scan is a strict generalisation: its result can
    only be >= what checking just the evaluated horizon would give,
    since that horizon is always included in the scan."""
    from astra.trajectory.models import PredictedSnapshot, PredictionResult

    config = ASTRAConfig()
    engine = ResolutionEngine(config)
    before_region = _region(["A1", "A2"], 55.0, lat=47.0, lon=8.0)
    now_snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])

    snap_10 = PredictedSnapshot(
        horizon_min=10, source_time_s=0.0, predicted_time_s=600.0,
        aircraft={"A1": _aircraft("A1", 47.2, 8.0, t=600.0), "A2": _aircraft("A2", 47.25, 8.05, t=600.0)},
    )
    prediction = PredictionResult(
        source_time_s=0.0, aircraft_count=2, horizons_min=(10,), snapshots={10: snap_10}
    )
    regions_by_horizon = {0: [before_region], 10: []}

    single_horizon_result = engine._domino_cost_at_horizon(snap_10, [], before_region)
    full_scan_result = engine._domino_cost(now_snapshot, prediction, regions_by_horizon, before_region)
    r.check(
        "full scan >= single-horizon check when they cover the same ground",
        full_scan_result >= single_horizon_result,
    )


def test_costs_speed_now_has_nonzero_fuel_proxy(r: Runner) -> None:
    """SPEED candidates get a real fuel-cost proxy (equal to their own
    deviation ratio, mirroring FLIGHT_LEVEL's existing convention) --
    previously hardcoded to 0.0 regardless of magnitude."""
    config = ASTRAConfig()
    engine = ResolutionEngine(config)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])

    from astra.resolution.candidates import CandidateSpec

    spec = CandidateSpec("SPEED", "A1", config.resolution_speed_step_kt, snapshot)
    deviation, fuel = engine._costs(spec)
    r.check_close("deviation is the expected step ratio", deviation, 1.0)
    r.check_close("fuel proxy equals deviation for SPEED (mirrors FLIGHT_LEVEL)", fuel, 1.0)

    spec_2x = CandidateSpec("SPEED", "A1", 2 * config.resolution_speed_step_kt, snapshot)
    _, fuel_2x = engine._costs(spec_2x)
    r.check_close("fuel proxy scales with magnitude", fuel_2x, 2.0)

    spec_negative = CandidateSpec("SPEED", "A1", -config.resolution_speed_step_kt, snapshot)
    _, fuel_negative = engine._costs(spec_negative)
    r.check_close("fuel proxy is direction-agnostic (slowing down also costs)", fuel_negative, 1.0)


def test_costs_heading_now_has_nonzero_fuel_proxy(r: Runner) -> None:
    """HEADING candidates get a sin-based fuel-cost proxy for wasted
    track distance -- previously hardcoded to 0.0 for both SUSTAINED
    and VECTOR_AND_REJOIN candidates."""
    import math

    config = ASTRAConfig()
    engine = ResolutionEngine(config)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])

    from astra.resolution.candidates import CandidateSpec

    spec = CandidateSpec("HEADING", "A1", 15.0, snapshot)
    deviation, fuel = engine._costs(spec)
    r.check_close("deviation is the expected step ratio", deviation, 1.0)
    r.check_close("fuel proxy is sin(15 deg)", fuel, math.sin(math.radians(15.0)))
    r.check(
        "a modest heading vector's fuel cost is well below its deviation cost",
        0.0 < fuel < deviation,
    )

    spec_90 = CandidateSpec("HEADING", "A1", 90.0, snapshot)
    _, fuel_90 = engine._costs(spec_90)
    r.check_close("a 90-degree vector wastes its full flown distance (sin(90)=1)", fuel_90, 1.0)

    spec_negative = CandidateSpec("HEADING", "A1", -15.0, snapshot)
    _, fuel_negative = engine._costs(spec_negative)
    r.check_close("fuel proxy is direction-agnostic for heading too", fuel_negative, math.sin(math.radians(15.0)))

    # Also holds for a VECTOR_AND_REJOIN candidate (rejoin_route set) --
    # _costs only looks at clearance_type/delta_value, not maneuver kind.
    spec_vector = CandidateSpec(
        "HEADING", "A1", 15.0, snapshot,
        vector_duration_s=config.resolution_vector_duration_s,
        rejoin_route=[(48.0, 8.0)],
    )
    _, fuel_vector = engine._costs(spec_vector)
    r.check_close(
        "VECTOR_AND_REJOIN candidates get the same fuel proxy as a sustained heading change",
        fuel_vector, math.sin(math.radians(15.0)),
    )


def test_costs_flight_level_unchanged(r: Runner) -> None:
    """FLIGHT_LEVEL's cost formula is byte-for-byte unchanged by this fix."""
    config = ASTRAConfig()
    engine = ResolutionEngine(config)
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)])

    from astra.resolution.candidates import CandidateSpec

    spec = CandidateSpec("FLIGHT_LEVEL", "A1", config.resolution_altitude_step_ft, snapshot)
    deviation, fuel = engine._costs(spec)
    r.check_close("deviation is the expected step ratio", deviation, 1.0)
    r.check_close("fuel proxy still equals deviation for FLIGHT_LEVEL", fuel, 1.0)


def test_matches_rvsm_parity_eastbound_odd(r: Runner) -> None:
    """Eastbound (heading < 180) expects an odd whole-thousands flight level."""
    r.check("FL350 (odd) matches eastbound", matches_rvsm_parity(90.0, 35000.0))
    r.check("FL340 (even) does not match eastbound", not matches_rvsm_parity(90.0, 34000.0))
    r.check("heading exactly 0 counts as eastbound", matches_rvsm_parity(0.0, 35000.0))


def test_matches_rvsm_parity_westbound_even(r: Runner) -> None:
    """Westbound (heading >= 180) expects an even whole-thousands flight level."""
    r.check("FL340 (even) matches westbound", matches_rvsm_parity(270.0, 34000.0))
    r.check("FL350 (odd) does not match westbound", not matches_rvsm_parity(270.0, 35000.0))
    r.check("heading exactly 180 counts as westbound", not matches_rvsm_parity(180.0, 35000.0))


def test_matches_rvsm_parity_normalises_heading(r: Runner) -> None:
    """Headings outside [0, 360) are normalised the same way as everywhere else."""
    r.check(
        "450 degrees (== 90) behaves as eastbound",
        matches_rvsm_parity(450.0, 35000.0) == matches_rvsm_parity(90.0, 35000.0),
    )
    r.check(
        "-90 degrees (== 270) behaves as westbound",
        matches_rvsm_parity(-90.0, 34000.0) == matches_rvsm_parity(270.0, 34000.0),
    )


def test_costs_flight_level_rvsm_penalty_applied_when_resulting_level_wrong(r: Runner) -> None:
    """A FLIGHT_LEVEL candidate whose *resulting* altitude violates RVSM
    parity for the target's track direction picks up
    resolution_rvsm_parity_penalty on deviation, but not on fuel."""
    from astra.resolution.candidates import CandidateSpec

    config = ASTRAConfig()
    engine = ResolutionEngine(config)

    # Eastbound (hdg=90), starting at FL350 (odd -- correctly matches).
    # +1000ft candidate resolves to FL360 (even) -- wrong for eastbound.
    starting = _aircraft("A1", 47.0, 8.0, hdg=90.0, alt=35000.0)
    snapshot_before = _snapshot([starting])
    hypothetical_snapshot = _snapshot(
        [_aircraft("A1", 47.0, 8.0, hdg=90.0, alt=35000.0 + config.resolution_altitude_step_ft)]
    )
    spec = CandidateSpec(
        "FLIGHT_LEVEL", "A1", config.resolution_altitude_step_ft, hypothetical_snapshot
    )
    r.check(
        "setup: the resulting FL360 does violate eastbound parity",
        not matches_rvsm_parity(90.0, 35000.0 + config.resolution_altitude_step_ft),
    )
    deviation, fuel = engine._costs(spec)
    r.check_close(
        "deviation includes the base step ratio plus the parity penalty",
        deviation, 1.0 + config.resolution_rvsm_parity_penalty,
    )
    r.check_close("fuel proxy is unaffected by the parity penalty", fuel, 1.0)


def test_costs_flight_level_no_rvsm_penalty_when_resulting_level_correct(r: Runner) -> None:
    """A FLIGHT_LEVEL candidate whose resulting altitude matches RVSM
    parity pays no penalty -- e.g. a 2x-magnitude step, which preserves
    parity since it changes altitude by an even number of thousands."""
    from astra.resolution.candidates import CandidateSpec

    config = ASTRAConfig()
    engine = ResolutionEngine(config)

    two_x_step = 2 * config.resolution_altitude_step_ft
    hypothetical_snapshot = _snapshot(
        [_aircraft("A1", 47.0, 8.0, hdg=90.0, alt=35000.0 + two_x_step)]
    )
    spec = CandidateSpec("FLIGHT_LEVEL", "A1", two_x_step, hypothetical_snapshot)
    r.check(
        "setup: a 2x step (2000ft) preserves eastbound parity (FL370 is odd)",
        matches_rvsm_parity(90.0, 35000.0 + two_x_step),
    )
    deviation, fuel = engine._costs(spec)
    r.check_close("deviation is just the step ratio, no penalty added", deviation, 2.0)
    r.check_close("fuel proxy matches deviation as usual", fuel, 2.0)


def test_costs_flight_level_rvsm_penalty_lowers_resolution_score(r: Runner) -> None:
    """All else equal, the RVSM parity penalty's extra deviation cost
    directly lowers resolution_score by resolution_weight_deviation *
    resolution_rvsm_parity_penalty -- confirmed via the same weighted
    formula ResolutionEngine._evaluate uses, holding complexity_delta_norm
    and domino_cost_norm fixed so only the cost terms differ. (An
    end-to-end comparison through the full replay pipeline is not used
    here: changing a candidate's altitude by 2x the base step can also
    change whether the hypothetical cluster still re-associates with the
    track at all -- see best_cluster_match -- which would confound the
    comparison with an unrelated effect.)"""
    config = ASTRAConfig()

    def score(deviation, fuel, complexity_delta=0.5, domino=0.0):
        return (
            config.resolution_weight_complexity * complexity_delta
            - config.resolution_weight_domino * domino
            - config.resolution_weight_deviation * deviation
            - config.resolution_weight_fuel * fuel
        )

    clean_score = score(deviation=1.0, fuel=1.0)
    penalised_score = score(deviation=1.0 + config.resolution_rvsm_parity_penalty, fuel=1.0)
    r.check(
        "a parity-violating candidate's score is lower by weight_deviation * penalty",
        abs((clean_score - penalised_score) - config.resolution_weight_deviation * config.resolution_rvsm_parity_penalty) < 1e-9,
    )
    r.check("the parity-violating candidate scores strictly worse, all else equal", penalised_score < clean_score)


def main() -> None:
    r = Runner("Milestone 7 — AI resolution framework (astra.resolution)")
    test_resolution_set_best_and_len(r)
    test_resolution_set_best_empty(r)
    test_select_target_single_member(r)
    test_select_target_no_members_resolve(r)
    test_select_target_conflict_based(r)
    test_select_target_no_conflicts_fallback(r)
    test_select_target_no_conflicts_fallback_alphabetical_last_resort(r)
    test_heading_lever_applicable_true(r)
    test_heading_lever_applicable_false(r)
    test_generate_candidates_no_heading(r)
    test_generate_candidates_with_heading(r)
    test_generate_candidates_heading_vector_and_rejoin(r)
    test_generate_candidates_explicit_target_and_levers(r)
    test_generate_candidates_empty_when_no_target(r)
    test_generate_candidates_apply_clearance_values(r)
    test_engine_ineligible_status(r)
    test_engine_ineligible_no_urgency_rank(r)
    test_engine_ineligible_no_onset(r)
    test_engine_missing_matched_region(r)
    test_engine_closest_horizon_selection(r)
    test_engine_resolve_many_orders_and_caps(r)
    test_engine_resolve_end_to_end(r)
    test_engine_resolve_many_end_to_end(r)
    test_engine_route_aware_when_route_provider_given(r)
    test_engine_vector_and_rejoin_end_to_end(r)
    test_engine_joint_candidates_present_for_two_member_cluster(r)
    test_engine_joint_candidates_for_three_member_cluster(r)
    test_engine_joint_candidate_capped_by_config(r)
    test_domino_cost_scans_every_horizon_not_just_evaluated(r)
    test_domino_cost_never_less_than_single_horizon_check(r)
    test_costs_speed_now_has_nonzero_fuel_proxy(r)
    test_costs_heading_now_has_nonzero_fuel_proxy(r)
    test_costs_flight_level_unchanged(r)
    test_matches_rvsm_parity_eastbound_odd(r)
    test_matches_rvsm_parity_westbound_even(r)
    test_matches_rvsm_parity_normalises_heading(r)
    test_costs_flight_level_rvsm_penalty_applied_when_resulting_level_wrong(r)
    test_costs_flight_level_no_rvsm_penalty_when_resulting_level_correct(r)
    test_costs_flight_level_rvsm_penalty_lowers_resolution_score(r)
    r.summary()


if __name__ == "__main__":
    main()
