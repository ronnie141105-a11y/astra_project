"""
Regression tests — Milestone 5 (4DARHAC detection / tracking, `astra.tracking`).

Run with:
    python3 tests/test_tracking.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.models import ComplexityRegion
from astra.hotspot.models import Cluster
from astra.tracking.association import (
    best_track_match,
    centroid_extent_overlap,
    jaccard_similarity,
)
from astra.tracking.engine import TrackerEngine
from astra.tracking.models import FourDArhac
from astra.utils.config import ASTRAConfig
from tests._runner import Runner


def _cluster(
    callsigns,
    lat=47.0,
    lon=8.0,
    alt_ft=35000.0,
    extent_nm=5.0,
    horizon_min=0,
    valid_at_s=0.0,
    label=0,
):
    """Build a hand-controlled `Cluster` for direct engine/association tests."""
    return Cluster(
        cluster_id=f"observed:{horizon_min}:{label}",
        source="observed" if horizon_min == 0 else "predicted",
        horizon_min=horizon_min,
        valid_at_s=valid_at_s,
        member_callsigns=frozenset(callsigns),
        centroid_lat=lat,
        centroid_lon=lon,
        centroid_alt_ft=alt_ft,
        horizontal_extent_nm=extent_nm,
    )


def _region(callsigns, score, valid_at_s, lat=47.0, lon=8.0, extent_nm=5.0, label=0):
    """Build a hand-controlled `ComplexityRegion` with a chosen score/time."""
    cluster = _cluster(
        callsigns, lat=lat, lon=lon, extent_nm=extent_nm, valid_at_s=valid_at_s, label=label
    )
    return ComplexityRegion(
        cluster=cluster,
        complexity_score=score,
        components={},
        computed_at_s=valid_at_s,
    )


def _track_with_last(callsigns, lat=47.0, lon=8.0, extent_nm=5.0):
    """Build a minimal open `FourDArhac` whose most recent entry is given."""
    region = _region(callsigns, score=50.0, valid_at_s=0.0, lat=lat, lon=lon, extent_nm=extent_nm)
    return FourDArhac(arhac_id="T1", status="CANDIDATE", track=[region])


# ----------------------------------------------------------------------
# astra.tracking.association
# ----------------------------------------------------------------------


def test_jaccard_similarity(r: Runner) -> None:
    """`jaccard_similarity` matches hand-computed intersection/union ratios."""
    r.check_close(
        "identical sets -> 1.0",
        jaccard_similarity(frozenset({"A1", "A2"}), frozenset({"A1", "A2"})),
        1.0,
    )
    r.check_close(
        "disjoint sets -> 0.0",
        jaccard_similarity(frozenset({"A1"}), frozenset({"A2"})),
        0.0,
    )
    r.check_close(
        "both empty -> 0.0",
        jaccard_similarity(frozenset(), frozenset()),
        0.0,
    )
    r.check_close(
        "partial overlap {A1,A2} vs {A2,A3} -> 1/3",
        jaccard_similarity(frozenset({"A1", "A2"}), frozenset({"A2", "A3"})),
        1.0 / 3.0,
    )


def test_centroid_extent_overlap(r: Runner) -> None:
    """`centroid_extent_overlap` gates on summed extents, not raw proximity."""
    near_a = _cluster(["A1"], lat=47.0, lon=8.0, extent_nm=5.0)
    near_b = _cluster(["B1"], lat=47.05, lon=8.0, extent_nm=5.0)  # ~3 NM away
    r.check("overlapping circles -> True", centroid_extent_overlap(near_a, near_b))

    far_a = _cluster(["A1"], lat=47.0, lon=8.0, extent_nm=1.0)
    far_b = _cluster(["B1"], lat=10.0, lon=10.0, extent_nm=1.0)
    r.check("distant circles -> False", not centroid_extent_overlap(far_a, far_b))


def test_best_track_match_primary_jaccard(r: Runner) -> None:
    """A cluster sharing most callsigns with a track wins on Jaccard."""
    track = _track_with_last(["A1", "A2", "A3"])
    new_cluster = _cluster(["A1", "A2", "A3", "A4"])  # jaccard = 3/4 = 0.75
    match = best_track_match(new_cluster, [track], jaccard_threshold=0.5)
    r.check("high-overlap cluster matches the track", match is not None and match.arhac_id == "T1")


def test_best_track_match_below_threshold_no_fallback(r: Runner) -> None:
    """Below the Jaccard threshold and spatially distant -> no match."""
    track = _track_with_last(["A1", "A2"], lat=47.0, lon=8.0, extent_nm=1.0)
    new_cluster = _cluster(["B1", "B2"], lat=10.0, lon=10.0, extent_nm=1.0)
    match = best_track_match(new_cluster, [track], jaccard_threshold=0.5)
    r.check("no member overlap and no spatial overlap -> None", match is None)


def test_best_track_match_centroid_fallback(r: Runner) -> None:
    """Membership has fully drifted, but the spatial area still coincides."""
    track = _track_with_last(["A1", "A2"], lat=47.0, lon=8.0, extent_nm=5.0)
    new_cluster = _cluster(["B1", "B2"], lat=47.02, lon=8.0, extent_nm=5.0)  # 0 jaccard, overlaps
    match = best_track_match(new_cluster, [track], jaccard_threshold=0.5)
    r.check("zero member overlap but spatial overlap -> fallback match", match is not None)


def test_best_track_match_no_candidates(r: Runner) -> None:
    """No open tracks -> no match, no error."""
    new_cluster = _cluster(["A1", "A2"])
    r.check("empty candidate list -> None", best_track_match(new_cluster, [], 0.5) is None)


# ----------------------------------------------------------------------
# astra.tracking.engine.TrackerEngine
# ----------------------------------------------------------------------


def test_tracker_creates_candidate_track(r: Runner) -> None:
    """A first-ever detection opens exactly one CANDIDATE track."""
    config = ASTRAConfig(tracking_confirm_cycles=2)
    tracker = TrackerEngine(config)
    region = _region(["A1", "A2"], score=40.0, valid_at_s=0.0)

    tracks = tracker.update({0: [region]})

    r.check("exactly one track opened", len(tracks) == 1)
    r.check("new track status is CANDIDATE", tracks[0].status == "CANDIDATE")
    r.check(
        "member_aircraft matches the cluster",
        tracks[0].member_aircraft == frozenset({"A1", "A2"}),
    )
    r.check_close("peak_complexity seeded from first region", tracks[0].peak_complexity, 40.0)
    r.check_close("confidence ramps proportionally (1/2)", tracks[0].confidence, 0.5)


def test_tracker_promotes_to_confirmed(r: Runner) -> None:
    """A track seen for `tracking_confirm_cycles` consecutive cycles is confirmed."""
    config = ASTRAConfig(tracking_confirm_cycles=2)
    tracker = TrackerEngine(config)

    tracker.update({0: [_region(["A1", "A2"], score=40.0, valid_at_s=0.0)]})
    tracks = tracker.update({0: [_region(["A1", "A2"], score=41.0, valid_at_s=60.0)]})

    r.check("still exactly one track (same identity)", len(tracks) == 1)
    r.check("promoted to CONFIRMED on the 2nd consecutive detection", tracks[0].status == "CONFIRMED")
    r.check_close("confidence reaches 1.0 at confirm_cycles", tracks[0].confidence, 1.0)


def test_tracker_arhac_id_stable_across_cycles(r: Runner) -> None:
    """The same physical area keeps the same arhac_id across poll cycles."""
    config = ASTRAConfig(tracking_confirm_cycles=1)
    tracker = TrackerEngine(config)

    first = tracker.update({0: [_region(["A1", "A2"], score=30.0, valid_at_s=0.0)]})
    arhac_id = first[0].arhac_id
    second = tracker.update({0: [_region(["A1", "A2", "A3"], score=35.0, valid_at_s=60.0)]})

    r.check("arhac_id unchanged across poll cycles", second[0].arhac_id == arhac_id)
    r.check(
        "member_aircraft is the union across cycles",
        second[0].member_aircraft == frozenset({"A1", "A2", "A3"}),
    )
    r.check("track history has 2 entries", len(second[0].track) == 2)


def test_tracker_full_lifecycle(r: Runner) -> None:
    """A scripted multi-cycle scenario walks CANDIDATE -> ... -> CLOSED in order."""
    config = ASTRAConfig(
        tracking_confirm_cycles=2,
        tracking_stale_cycles=2,
        tracking_trend_tolerance=1.0,
    )
    tracker = TrackerEngine(config)
    members = ["A1", "A2"]

    # Cycle 1: first detection -> CANDIDATE.
    t = tracker.update({0: [_region(members, score=30.0, valid_at_s=0.0)]})
    r.check("cycle 1: CANDIDATE", t[0].status == "CANDIDATE")

    # Cycle 2: second consecutive detection -> CONFIRMED.
    t = tracker.update({0: [_region(members, score=35.0, valid_at_s=60.0)]})
    r.check("cycle 2: CONFIRMED", t[0].status == "CONFIRMED")

    # Cycle 3: rising score -> GROWING.
    t = tracker.update({0: [_region(members, score=50.0, valid_at_s=120.0)]})
    r.check("cycle 3: GROWING", t[0].status == "GROWING")

    # Cycle 4: still rising -> stays GROWING.
    t = tracker.update({0: [_region(members, score=70.0, valid_at_s=180.0)]})
    r.check("cycle 4: still GROWING", t[0].status == "GROWING")

    # Cycle 5: score falls -> PEAK (transition cycle out of GROWING).
    t = tracker.update({0: [_region(members, score=60.0, valid_at_s=240.0)]})
    r.check("cycle 5: PEAK", t[0].status == "PEAK")
    r.check_close("peak_complexity recorded the highest score", t[0].peak_complexity, 70.0)

    # Cycle 6: continues falling -> DISSIPATING.
    t = tracker.update({0: [_region(members, score=45.0, valid_at_s=300.0)]})
    r.check("cycle 6: DISSIPATING", t[0].status == "DISSIPATING")
    arhac_id = t[0].arhac_id

    # Cycles 7-8: not re-observed. tracking_stale_cycles=2 -> closes on cycle 8.
    t = tracker.update({0: []})
    r.check("cycle 7: not yet closed (1 missed cycle)", len(tracker.open_tracks()) == 1)
    r.check(
        "cycle 7: still-open track is reported, not yet CLOSED",
        len(t) == 1 and t[0].status != "CLOSED",
    )

    t = tracker.update({0: []})
    r.check("cycle 8: exactly one track freshly closed", len(t) == 1 and t[0].arhac_id == arhac_id)
    r.check("cycle 8: closed track has CLOSED status", t[0].status == "CLOSED")
    r.check("cycle 8: no longer in the open set", len(tracker.open_tracks()) == 0)


def test_tracker_two_independent_tracks(r: Runner) -> None:
    """Two spatially and compositionally distinct clusters open two tracks."""
    config = ASTRAConfig(tracking_confirm_cycles=1)
    tracker = TrackerEngine(config)
    region_a = _region(["A1", "A2"], score=30.0, valid_at_s=0.0, lat=47.0, lon=8.0, label=0)
    region_b = _region(["B1", "B2"], score=60.0, valid_at_s=0.0, lat=10.0, lon=10.0, label=1)

    tracks = tracker.update({0: [region_a, region_b]})

    r.check("two independent tracks opened", len(tracks) == 2)
    r.check(
        "sorted by descending peak_complexity",
        tracks[0].peak_complexity >= tracks[1].peak_complexity,
    )
    r.check("higher-complexity track has priority 1", tracks[0].priority == 1)
    r.check("lower-complexity track has priority 2", tracks[1].priority == 2)


def test_tracker_ignores_non_zero_horizons_for_identity(r: Runner) -> None:
    """Only horizon 0 drives Milestone 5 identity; other horizons are inert."""
    config = ASTRAConfig(tracking_confirm_cycles=1)
    tracker = TrackerEngine(config)
    observed = _region(["A1", "A2"], score=30.0, valid_at_s=0.0)
    predicted = _region(["A1", "A2"], score=99.0, valid_at_s=300.0, label=1)

    tracks = tracker.update({0: [observed], 5: [predicted]})

    r.check("exactly one track from the observed region only", len(tracks) == 1)
    r.check(
        "predicted-horizon region did not affect peak_complexity",
        tracks[0].peak_complexity == 30.0,
    )


def test_config_tracking_validation(r: Runner) -> None:
    """`ASTRAConfig` rejects out-of-range tracking thresholds."""
    r.check_raises(
        "tracking_jaccard_threshold <= 0 raises ValueError",
        lambda: ASTRAConfig(tracking_jaccard_threshold=0.0),
        ValueError,
    )
    r.check_raises(
        "tracking_jaccard_threshold > 1 raises ValueError",
        lambda: ASTRAConfig(tracking_jaccard_threshold=1.5),
        ValueError,
    )
    r.check_raises(
        "tracking_stale_cycles < 1 raises ValueError",
        lambda: ASTRAConfig(tracking_stale_cycles=0),
        ValueError,
    )
    r.check_raises(
        "tracking_confirm_cycles < 1 raises ValueError",
        lambda: ASTRAConfig(tracking_confirm_cycles=0),
        ValueError,
    )
    r.check_raises(
        "tracking_trend_tolerance < 0 raises ValueError",
        lambda: ASTRAConfig(tracking_trend_tolerance=-1.0),
        ValueError,
    )
    r.check_raises(
        "tracking_provisional_min_complexity out of [0, 100] raises ValueError",
        lambda: ASTRAConfig(tracking_provisional_min_complexity=150.0),
        ValueError,
    )
    r.check_raises(
        "tracking_provisional_confidence_multiplier <= 0 raises ValueError",
        lambda: ASTRAConfig(tracking_provisional_confidence_multiplier=0.0),
        ValueError,
    )
    r.check_raises(
        "tracking_provisional_confidence_multiplier > 1 raises ValueError",
        lambda: ASTRAConfig(tracking_provisional_confidence_multiplier=1.5),
        ValueError,
    )


# ----------------------------------------------------------------------
# PROVISIONAL tracks — predicted-only hotspots with no current proximity
# ----------------------------------------------------------------------


def test_provisional_track_opens_from_future_horizon_only(r: Runner) -> None:
    """A cluster with no horizon-0 counterpart at all opens a PROVISIONAL track."""
    config = ASTRAConfig(tracking_provisional_min_complexity=25.0)
    tracker = TrackerEngine(config)
    predicted = _region(["A1", "A2"], score=40.0, valid_at_s=1800.0, label=0)

    tracks = tracker.update({0: [], 30: [predicted]})

    r.check("exactly one track opened", len(tracks) == 1)
    r.check("status is PROVISIONAL", tracks[0].status == "PROVISIONAL")
    r.check("track.track is empty (no real observation yet)", tracks[0].track == [])
    r.check(
        "provisional_track has the predicted entry",
        len(tracks[0].provisional_track) == 1
        and tracks[0].provisional_track[0].complexity_score == 40.0,
    )
    r.check(
        "first_detected_cycle_s backs out the real 'now' time (1800 - 30*60 = 0)",
        tracks[0].first_detected_cycle_s == 0.0,
    )
    r.check(
        "confidence is scaled down by tracking_provisional_confidence_multiplier",
        0.0 < tracks[0].confidence < config.tracking_confirm_cycles and tracks[0].confidence < 1.0,
    )
    r.check("member_aircraft is populated", tracks[0].member_aircraft == frozenset({"A1", "A2"}))


def test_provisional_track_ignores_below_threshold(r: Runner) -> None:
    """A predicted cluster below tracking_provisional_min_complexity opens nothing."""
    config = ASTRAConfig(tracking_provisional_min_complexity=50.0)
    tracker = TrackerEngine(config)
    faint = _region(["A1", "A2"], score=20.0, valid_at_s=1800.0, label=0)

    tracks = tracker.update({0: [], 30: [faint]})

    r.check("no track opened for a below-threshold prediction", tracks == [])


def test_provisional_track_extends_across_cycles(r: Runner) -> None:
    """A provisional track gets one new provisional_track entry per cycle it keeps matching."""
    config = ASTRAConfig(tracking_provisional_min_complexity=25.0, poll_interval_s=300.0)
    tracker = TrackerEngine(config)

    # Cycle 1 (t=0): predicted at horizon 30 (i.e. for t=1800s).
    tracker.update({0: [], 30: [_region(["A1", "A2"], score=40.0, valid_at_s=1800.0, label=0)]})
    # Cycle 2 (t=300s): same phenomenon, now only 25 min out.
    tracks = tracker.update(
        {0: [], 25: [_region(["A1", "A2"], score=45.0, valid_at_s=1800.0, label=0)]}
    )

    r.check("still exactly one track (not duplicated)", len(tracks) == 1)
    r.check("still PROVISIONAL", tracks[0].status == "PROVISIONAL")
    r.check("provisional_track now has 2 entries", len(tracks[0].provisional_track) == 2)
    r.check(
        "peak_complexity raised to the higher predicted score",
        tracks[0].peak_complexity == 45.0,
    )


def test_provisional_track_does_not_duplicate_within_one_cycle(r: Runner) -> None:
    """The same evolving cluster visible at two horizons in one cycle opens only one track."""
    config = ASTRAConfig(tracking_provisional_min_complexity=25.0)
    tracker = TrackerEngine(config)
    at_20 = _region(["A1", "A2"], score=35.0, valid_at_s=1200.0, label=0)
    at_30 = _region(["A1", "A2"], score=42.0, valid_at_s=1800.0, label=0)

    tracks = tracker.update({0: [], 20: [at_20], 30: [at_30]})

    r.check("exactly one provisional track, not two", len(tracks) == 1)
    r.check("status is PROVISIONAL", tracks[0].status == "PROVISIONAL")
    r.check(
        "anchored on the smaller (soonest) horizon's entry, not both",
        len(tracks[0].provisional_track) == 1
        and tracks[0].provisional_track[0].complexity_score == 35.0,
    )


def test_provisional_track_promotes_on_real_observation(r: Runner) -> None:
    """Once the predicted cluster actually appears at horizon 0, the SAME track promotes."""
    config = ASTRAConfig(tracking_provisional_min_complexity=25.0, tracking_confirm_cycles=2)
    tracker = TrackerEngine(config)

    provisional_tracks = tracker.update(
        {0: [], 30: [_region(["A1", "A2"], score=40.0, valid_at_s=1800.0, label=0)]}
    )
    arhac_id = provisional_tracks[0].arhac_id
    first_detected = provisional_tracks[0].first_detected_cycle_s

    real = _region(["A1", "A2"], score=55.0, valid_at_s=1800.0, label=0)
    promoted_tracks = tracker.update({0: [real]})

    r.check("still exactly one track", len(promoted_tracks) == 1)
    r.check("same arhac_id preserved across promotion", promoted_tracks[0].arhac_id == arhac_id)
    r.check(
        "status left PROVISIONAL (has a real observation now)",
        promoted_tracks[0].status != "PROVISIONAL",
    )
    r.check(
        "status is CANDIDATE, not CONFIRMED (provisional history doesn't count toward confirm_cycles)",
        promoted_tracks[0].status == "CANDIDATE",
    )
    r.check("track.track now has exactly 1 real entry", len(promoted_tracks[0].track) == 1)
    r.check(
        "provisional_track is preserved as a historical record",
        len(promoted_tracks[0].provisional_track) == 1,
    )
    r.check(
        "first_detected_cycle_s preserved from the original provisional detection",
        promoted_tracks[0].first_detected_cycle_s == first_detected,
    )
    r.check("peak_complexity reflects the real observation", promoted_tracks[0].peak_complexity == 55.0)

    # One more real cycle should now reach CONFIRMED via the normal path.
    real2 = _region(["A1", "A2"], score=58.0, valid_at_s=2100.0, label=0)
    final_tracks = tracker.update({0: [real2]})
    r.check("reaches CONFIRMED after tracking_confirm_cycles real detections", final_tracks[0].status == "CONFIRMED")


def test_provisional_track_goes_stale_and_closes(r: Runner) -> None:
    """A provisional track that stops matching anywhere (real or predicted) eventually closes."""
    config = ASTRAConfig(
        tracking_provisional_min_complexity=25.0, tracking_stale_cycles=2
    )
    tracker = TrackerEngine(config)

    tracker.update({0: [], 30: [_region(["A1", "A2"], score=40.0, valid_at_s=1800.0, label=0)]})
    tracker.update({0: [], 30: []})  # missed cycle 1
    tracks = tracker.update({0: [], 30: []})  # missed cycle 2 -> stale_cycles reached

    r.check("track closed after tracking_stale_cycles missed cycles", len(tracks) == 1)
    r.check("closed status", tracks[0].status == "CLOSED")


def test_provisional_track_never_resolvable(r: Runner) -> None:
    """A PROVISIONAL track never clears ResolutionEngine's eligibility bar."""
    from astra.resolution.engine import ResolutionEngine

    config = ASTRAConfig(tracking_provisional_min_complexity=25.0)
    tracker = TrackerEngine(config)
    provisional_tracks = tracker.update(
        {0: [], 30: [_region(["A1", "A2"], score=40.0, valid_at_s=1800.0, label=0)]}
    )
    track = provisional_tracks[0]
    track.forecast_urgency_rank = 1  # even if somehow ranked urgent
    track.predicted_onset_s = 600.0

    engine = ResolutionEngine(config)
    snapshot = _snapshot_stub(["A1", "A2"])
    rs = engine.resolve(track, snapshot, {30: [track.provisional_track[0]]})

    r.check("no candidates generated for a PROVISIONAL track", rs.candidates == [])
    r.check("no joint candidate either", rs.joint_candidate is None)


def _snapshot_stub(callsigns):
    """Minimal `TrafficSnapshot` stub for the resolution-eligibility test above."""
    from astra.interface.traffic_state import AircraftState, TrafficSnapshot

    aircraft = {
        cs: AircraftState(
            callsign=cs, lat=47.0, lon=8.0 + i * 0.01, altitude_ft=35000.0,
            ground_speed_kt=250.0, heading_deg=90.0, vertical_speed_fpm=0.0,
            aircraft_type="A320", timestamp_s=0.0,
        )
        for i, cs in enumerate(callsigns)
    }
    return TrafficSnapshot(timestamp_s=0.0, aircraft=aircraft)


def main() -> None:
    r = Runner("Milestone 5 — 4DARHAC detection / tracking (astra.tracking)")
    test_jaccard_similarity(r)
    test_centroid_extent_overlap(r)
    test_best_track_match_primary_jaccard(r)
    test_best_track_match_below_threshold_no_fallback(r)
    test_best_track_match_centroid_fallback(r)
    test_best_track_match_no_candidates(r)
    test_tracker_creates_candidate_track(r)
    test_tracker_promotes_to_confirmed(r)
    test_tracker_arhac_id_stable_across_cycles(r)
    test_tracker_full_lifecycle(r)
    test_tracker_two_independent_tracks(r)
    test_tracker_ignores_non_zero_horizons_for_identity(r)
    test_config_tracking_validation(r)
    test_provisional_track_opens_from_future_horizon_only(r)
    test_provisional_track_ignores_below_threshold(r)
    test_provisional_track_extends_across_cycles(r)
    test_provisional_track_does_not_duplicate_within_one_cycle(r)
    test_provisional_track_promotes_on_real_observation(r)
    test_provisional_track_goes_stale_and_closes(r)
    test_provisional_track_never_resolvable(r)
    r.summary()


if __name__ == "__main__":
    main()
