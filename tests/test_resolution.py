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
    """No conflict pairs at all -> deterministic alphabetical fallback."""
    config = ASTRAConfig()
    snapshot = _snapshot(
        [
            _aircraft("B1", 47.000, 8.000),
            _aircraft("A1", 47.500, 8.500),
        ]
    )
    cluster = _cluster(["B1", "A1"])
    target = select_target_aircraft(cluster, snapshot, config)
    r.check("alphabetically first callsign wins", target is not None and target.callsign == "A1")


def test_heading_lever_applicable_true(r: Runner) -> None:
    """A region with a nonzero MTCA/LTCA component makes heading applicable."""
    region = _region(["A1", "A2"], 60.0, components={"mtca_count": 1.0, "ltca_count": 0.0})
    r.check("mtca present -> heading applicable", heading_lever_applicable(region))


def test_heading_lever_applicable_false(r: Runner) -> None:
    """A region with no conflict components at all disables the heading lever."""
    region = _region(["A1", "A2"], 60.0, components={"density_ac_per_nm2": 1.0})
    r.check("no conflict components -> heading not applicable", not heading_lever_applicable(region))


def test_generate_candidates_no_heading(r: Runner) -> None:
    """Without conflict components, exactly SPEED + FLIGHT_LEVEL are generated."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.5)])
    region = _region(["A1", "A2"], 40.0, components={"density_ac_per_nm2": 1.0})
    specs = generate_candidates(region, snapshot, config)
    r.check("2 candidates without a conflict driver", len(specs) == 2)
    r.check(
        "clearance types are SPEED and FLIGHT_LEVEL",
        {s.clearance_type for s in specs} == {"SPEED", "FLIGHT_LEVEL"},
    )


def test_generate_candidates_with_heading(r: Runner) -> None:
    """With a conflict component, HEADING is added as a third candidate."""
    config = ASTRAConfig()
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.0, 8.01)])
    region = _region(["A1", "A2"], 70.0, components={"mtca_count": 2.0})
    specs = generate_candidates(region, snapshot, config)
    r.check("3 candidates with a conflict driver", len(specs) == 3)
    r.check(
        "clearance types include HEADING",
        {s.clearance_type for s in specs} == {"SPEED", "FLIGHT_LEVEL", "HEADING"},
    )


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
    specs = {s.clearance_type: s for s in generate_candidates(region, snapshot, config)}
    target_callsign = specs["SPEED"].target_callsign
    base = ac1 if target_callsign == "A1" else ac2
    r.check(
        "all specs target the same (highest-conflict) aircraft",
        {s.target_callsign for s in specs.values()} == {target_callsign},
    )

    speed_after = specs["SPEED"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "speed candidate adds the configured step",
        speed_after.ground_speed_kt,
        base.ground_speed_kt + config.resolution_speed_step_kt,
    )
    fl_after = specs["FLIGHT_LEVEL"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "flight-level candidate adds the configured step",
        fl_after.altitude_ft,
        base.altitude_ft + config.resolution_altitude_step_ft,
    )
    hdg_after = specs["HEADING"].hypothetical_snapshot.get(target_callsign)
    r.check_close(
        "heading candidate wraps modulo 360",
        hdg_after.heading_deg,
        (base.heading_deg + config.resolution_heading_step_deg) % 360.0,
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
        "deviation_cost_norm is a step ratio in [0, 1]",
        all(0.0 <= c.deviation_cost_norm <= 1.0 for c in rs.candidates),
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


def main() -> None:
    r = Runner("Milestone 7 — AI resolution framework (astra.resolution)")
    test_resolution_set_best_and_len(r)
    test_resolution_set_best_empty(r)
    test_select_target_single_member(r)
    test_select_target_no_members_resolve(r)
    test_select_target_conflict_based(r)
    test_select_target_no_conflicts_fallback(r)
    test_heading_lever_applicable_true(r)
    test_heading_lever_applicable_false(r)
    test_generate_candidates_no_heading(r)
    test_generate_candidates_with_heading(r)
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
    r.summary()


if __name__ == "__main__":
    main()
