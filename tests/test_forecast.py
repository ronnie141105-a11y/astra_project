"""
Regression tests — Milestone 6 (4DARHAC forecast, `astra.forecast`).

Run with:
    python3 tests/test_forecast.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.models import ComplexityRegion
from astra.forecast.engine import ForecastEngine
from astra.forecast.horizon_series import build_series
from astra.forecast.projection import linear_crossing_time, predicted_peak
from astra.hotspot.models import Cluster
from astra.tracking.models import FourDArhac
from astra.utils.config import ASTRAConfig
from tests._runner import Runner


def _cluster(callsigns, lat=47.0, lon=8.0, extent_nm=5.0, horizon_min=0, valid_at_s=0.0, label=0):
    """Build a hand-controlled `Cluster` for direct series/engine tests."""
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


def _region(callsigns, score, valid_at_s, horizon_min=0, lat=47.0, lon=8.0, label=0):
    """Build a hand-controlled `ComplexityRegion` with a chosen score/time."""
    cluster = _cluster(
        callsigns, lat=lat, lon=lon, horizon_min=horizon_min, valid_at_s=valid_at_s, label=label
    )
    return ComplexityRegion(cluster=cluster, complexity_score=score, components={}, computed_at_s=valid_at_s)


def _track(status, score, valid_at_s=0.0, confidence=1.0, callsigns=("A1", "A2"), peak_complexity=None):
    """Build a minimal `FourDArhac` with one observed entry, ready to forecast."""
    region = _region(list(callsigns), score, valid_at_s)
    return FourDArhac(
        arhac_id="T1",
        status=status,
        track=[region],
        member_aircraft=frozenset(callsigns),
        confidence=confidence,
        peak_complexity=peak_complexity if peak_complexity is not None else score,
        peak_time_s=valid_at_s,
    )


# ----------------------------------------------------------------------
# astra.forecast.projection
# ----------------------------------------------------------------------


def test_linear_crossing_time_rising(r: Runner) -> None:
    """A rising crossing interpolates linearly between the bracketing points."""
    points = [(0.0, 40.0), (300.0, 45.0), (600.0, 55.0)]
    t = linear_crossing_time(points, threshold=50.0, rising=True)
    r.check_close("rising crossing interpolated at t=450", t, 450.0)


def test_linear_crossing_time_falling(r: Runner) -> None:
    """A falling crossing interpolates linearly between the bracketing points."""
    points = [(0.0, 35.0), (300.0, 32.0), (600.0, 25.0)]
    t = linear_crossing_time(points, threshold=30.0, rising=False)
    r.check_close("falling crossing interpolated at t=385.714", t, 300.0 + (2.0 / 7.0) * 300.0)


def test_linear_crossing_time_no_crossing(r: Runner) -> None:
    """A series that never reaches the threshold returns None."""
    points = [(0.0, 10.0), (300.0, 15.0), (600.0, 20.0)]
    r.check("no rising crossing -> None", linear_crossing_time(points, 50.0, rising=True) is None)


def test_predicted_peak_exceeds(r: Runner) -> None:
    """The highest future point is returned when it exceeds the current peak."""
    result = predicted_peak([(300.0, 60.0), (600.0, 85.0)], current_peak=70.0)
    r.check("peak found", result is not None)
    r.check_close("peak time", result[0], 600.0)
    r.check_close("peak score", result[1], 85.0)


def test_predicted_peak_does_not_exceed(r: Runner) -> None:
    """No future point exceeding the current peak -> None."""
    result = predicted_peak([(300.0, 60.0), (600.0, 65.0)], current_peak=70.0)
    r.check("no higher future peak -> None", result is None)


def test_predicted_peak_empty(r: Runner) -> None:
    """No future points at all -> None."""
    r.check("empty points -> None", predicted_peak([], current_peak=70.0) is None)


# ----------------------------------------------------------------------
# astra.forecast.horizon_series
# ----------------------------------------------------------------------


def test_build_series_matches_and_counts(r: Runner) -> None:
    """Matched horizons are appended in time order; unmatched horizons are skipped."""
    track = _track("CONFIRMED", score=40.0, valid_at_s=0.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 45.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 55.0, 600.0, horizon_min=10)],
        15: [_region(["B1", "B2"], 90.0, 900.0, horizon_min=15, lat=10.0, lon=10.0)],
    }
    series, matched_count, total_horizons = build_series(track, regions_by_horizon, jaccard_threshold=0.5)

    r.check_close("anchor + 2 matched horizons", float(len(series)), 3.0)
    r.check("series sorted ascending by time", [p[0] for p in series] == sorted(p[0] for p in series))
    r.check("matched_count excludes the non-overlapping horizon", matched_count == 2)
    r.check("total_horizons counts every non-zero horizon key present", total_horizons == 3)


def test_build_series_ignores_horizon_zero(r: Runner) -> None:
    """Horizon 0 in regions_by_horizon (already folded into track) is not re-matched."""
    track = _track("CONFIRMED", score=40.0, valid_at_s=0.0)
    regions_by_horizon = {0: [_region(["A1", "A2"], 999.0, 0.0)]}
    series, matched_count, total_horizons = build_series(track, regions_by_horizon, jaccard_threshold=0.5)

    r.check("only the anchor point present", len(series) == 1)
    r.check("horizon 0 not counted as a predicted horizon", total_horizons == 0)
    r.check("nothing matched", matched_count == 0)


# ----------------------------------------------------------------------
# astra.forecast.engine.ForecastEngine
# ----------------------------------------------------------------------


def test_forecast_skips_candidate(r: Runner) -> None:
    """CANDIDATE tracks are never forecast."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("CANDIDATE", score=40.0)
    regions_by_horizon = {5: [_region(["A1", "A2"], 90.0, 300.0, horizon_min=5)]}
    result = engine.forecast(track, regions_by_horizon)
    r.check("predicted_onset_s stays None", result.predicted_onset_s is None)
    r.check("forecast_urgency_rank stays None", result.forecast_urgency_rank is None)


def test_forecast_skips_closed(r: Runner) -> None:
    """CLOSED tracks are never forecast."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("CLOSED", score=40.0)
    regions_by_horizon = {5: [_region(["A1", "A2"], 90.0, 300.0, horizon_min=5)]}
    result = engine.forecast(track, regions_by_horizon)
    r.check("predicted_onset_s stays None", result.predicted_onset_s is None)


def test_forecast_insufficient_matched_horizons(r: Runner) -> None:
    """Fewer than forecast_min_matched_horizons matches -> None fields, capped confidence."""
    config = ASTRAConfig(forecast_min_matched_horizons=2)
    engine = ForecastEngine(config)
    track = _track("CONFIRMED", score=40.0, confidence=1.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 90.0, 300.0, horizon_min=5)],
        10: [_region(["B1", "B2"], 90.0, 600.0, horizon_min=10, lat=10.0, lon=10.0)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check("predicted_onset_s is None (insufficient data)", result.predicted_onset_s is None)
    r.check("predicted_dissipation_s is None", result.predicted_dissipation_s is None)
    r.check("predicted_peak_time_s is None", result.predicted_peak_time_s is None)
    r.check_close("confidence capped by coverage (1 matched / 2 total)", result.confidence, 1.0 * 0.5)


def test_forecast_onset_crossing(r: Runner) -> None:
    """A rising series crossing forecast_onset_threshold sets predicted_onset_s."""
    config = ASTRAConfig()
    engine = ForecastEngine(config)
    track = _track("GROWING", score=40.0, valid_at_s=0.0, confidence=1.0, peak_complexity=40.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 45.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 55.0, 600.0, horizon_min=10)],
        15: [],
        30: [],
        60: [],
    }
    result = engine.forecast(track, regions_by_horizon)

    r.check("predicted_onset_s is set", result.predicted_onset_s is not None)
    r.check_close("interpolated onset time", result.predicted_onset_s, 450.0)

    coverage = 2 / 5
    lead_time_s = 600.0
    decay = 1.0 - math.exp(-lead_time_s / config.forecast_confidence_decay_s)
    expected_confidence = 1.0 * coverage * (1.0 - decay)
    r.check_close("confidence matches detection_ramp*coverage*(1-decay)", result.confidence, expected_confidence)


def test_forecast_onset_already_active(r: Runner) -> None:
    """A track already above the onset threshold has no onset left to predict."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("GROWING", score=60.0, confidence=1.0, peak_complexity=60.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 65.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 70.0, 600.0, horizon_min=10)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check("already active -> predicted_onset_s stays None", result.predicted_onset_s is None)


def test_forecast_dissipation_crossing(r: Runner) -> None:
    """A falling series crossing forecast_dissipation_threshold sets predicted_dissipation_s."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("DISSIPATING", score=35.0, confidence=1.0, peak_complexity=70.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 32.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 25.0, 600.0, horizon_min=10)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check("predicted_dissipation_s is set", result.predicted_dissipation_s is not None)
    r.check_close(
        "interpolated dissipation time", result.predicted_dissipation_s, 300.0 + (2.0 / 7.0) * 300.0
    )


def test_forecast_dissipation_already_below(r: Runner) -> None:
    """A track already below the dissipation threshold has already dissipated."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("DISSIPATING", score=20.0, confidence=1.0, peak_complexity=70.0)
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 15.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 10.0, 600.0, horizon_min=10)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check("already dissipated -> predicted_dissipation_s stays None", result.predicted_dissipation_s is None)


def test_forecast_peak_raised(r: Runner) -> None:
    """A future matched horizon exceeding the current peak raises peak_complexity."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("GROWING", score=40.0, confidence=1.0, peak_complexity=70.0)
    track.peak_time_s = 0.0
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 60.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 85.0, 600.0, horizon_min=10)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check_close("peak_complexity raised to the future max", result.peak_complexity, 85.0)
    r.check_close("peak_time_s moved to the predicted time", result.peak_time_s, 600.0)
    r.check_close("predicted_peak_time_s recorded", result.predicted_peak_time_s, 600.0)


def test_forecast_peak_not_raised(r: Runner) -> None:
    """No future point exceeds the current peak -> peak fields untouched."""
    engine = ForecastEngine(ASTRAConfig())
    track = _track("GROWING", score=40.0, confidence=1.0, peak_complexity=90.0)
    track.peak_time_s = 0.0
    regions_by_horizon = {
        5: [_region(["A1", "A2"], 45.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 55.0, 600.0, horizon_min=10)],
    }
    result = engine.forecast(track, regions_by_horizon)
    r.check("predicted_peak_time_s stays None", result.predicted_peak_time_s is None)
    r.check_close("peak_complexity unchanged", result.peak_complexity, 90.0)
    r.check_close("peak_time_s unchanged", result.peak_time_s, 0.0)


def test_forecast_many_assigns_urgency_rank(r: Runner) -> None:
    """forecast_many ranks tracks by soonest predicted_onset_s; no-onset tracks get None."""
    engine = ForecastEngine(ASTRAConfig())

    soon = _track("GROWING", score=40.0, confidence=1.0, peak_complexity=40.0)
    soon.arhac_id = "SOON"
    later = _track("GROWING", score=40.0, confidence=1.0, peak_complexity=40.0)
    later.arhac_id = "LATER"
    no_onset = _track("GROWING", score=60.0, confidence=1.0, peak_complexity=60.0)
    no_onset.arhac_id = "NO_ONSET"

    # Each track has its own distinctly-located cluster/matching regions so
    # they associate independently, mimicking how each track is matched
    # against the same cycle's horizons in the real pipeline.
    soon_regions = {
        5: [_region(["A1", "A2"], 55.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 60.0, 600.0, horizon_min=10)],
    }
    later_regions = {
        5: [_region(["A1", "A2"], 42.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 51.0, 3600.0, horizon_min=10)],
    }
    engine.forecast(soon, soon_regions)
    engine.forecast(later, later_regions)
    engine.forecast(no_onset, {5: [_region(["A1", "A2"], 65.0, 300.0, horizon_min=5)]})

    tracks = [later, no_onset, soon]
    ForecastEngine._assign_urgency_rank(tracks)

    r.check("soonest onset ranked 1", soon.forecast_urgency_rank == 1)
    r.check("later onset ranked 2", later.forecast_urgency_rank == 2)
    r.check("no predicted onset -> rank None", no_onset.forecast_urgency_rank is None)


def test_forecast_many_end_to_end(r: Runner) -> None:
    """forecast_many() forecasts every track and assigns urgency ranks in one call."""
    engine = ForecastEngine(ASTRAConfig())
    fast = _track("GROWING", score=40.0, confidence=1.0, peak_complexity=40.0)
    fast.arhac_id = "FAST"
    fast_regions = {
        5: [_region(["A1", "A2"], 55.0, 300.0, horizon_min=5)],
        10: [_region(["A1", "A2"], 60.0, 600.0, horizon_min=10)],
    }
    candidate = _track("CANDIDATE", score=40.0, confidence=0.5)
    candidate.arhac_id = "CAND"

    # forecast_many shares one regions_by_horizon across all tracks in the
    # real pipeline; here both tracks reuse fast_regions since only `fast`
    # is eligible to be forecast at all.
    result = engine.forecast_many([fast, candidate], fast_regions)

    r.check("eligible track forecast", result[0].predicted_onset_s is not None)
    r.check("eligible track ranked", result[0].forecast_urgency_rank == 1)
    r.check("CANDIDATE track left untouched", result[1].predicted_onset_s is None)
    r.check("CANDIDATE track has no urgency rank", result[1].forecast_urgency_rank is None)


# ----------------------------------------------------------------------
# ASTRAConfig — Phase 6 validation
# ----------------------------------------------------------------------


def test_config_forecast_validation(r: Runner) -> None:
    """`ASTRAConfig` rejects out-of-range forecast thresholds."""
    r.check_raises(
        "forecast_onset_threshold > 100 raises ValueError",
        lambda: ASTRAConfig(forecast_onset_threshold=150.0),
        ValueError,
    )
    r.check_raises(
        "forecast_dissipation_threshold < 0 raises ValueError",
        lambda: ASTRAConfig(forecast_dissipation_threshold=-1.0),
        ValueError,
    )
    r.check_raises(
        "dissipation >= onset raises ValueError (hysteresis)",
        lambda: ASTRAConfig(forecast_onset_threshold=30.0, forecast_dissipation_threshold=30.0),
        ValueError,
    )
    r.check_raises(
        "forecast_min_matched_horizons < 1 raises ValueError",
        lambda: ASTRAConfig(forecast_min_matched_horizons=0),
        ValueError,
    )
    r.check_raises(
        "forecast_confidence_decay_s <= 0 raises ValueError",
        lambda: ASTRAConfig(forecast_confidence_decay_s=0.0),
        ValueError,
    )


def main() -> None:
    r = Runner("Milestone 6 — 4DARHAC forecast (astra.forecast)")
    test_linear_crossing_time_rising(r)
    test_linear_crossing_time_falling(r)
    test_linear_crossing_time_no_crossing(r)
    test_predicted_peak_exceeds(r)
    test_predicted_peak_does_not_exceed(r)
    test_predicted_peak_empty(r)
    test_build_series_matches_and_counts(r)
    test_build_series_ignores_horizon_zero(r)
    test_forecast_skips_candidate(r)
    test_forecast_skips_closed(r)
    test_forecast_insufficient_matched_horizons(r)
    test_forecast_onset_crossing(r)
    test_forecast_onset_already_active(r)
    test_forecast_dissipation_crossing(r)
    test_forecast_dissipation_already_below(r)
    test_forecast_peak_raised(r)
    test_forecast_peak_not_raised(r)
    test_forecast_many_assigns_urgency_rank(r)
    test_forecast_many_end_to_end(r)
    test_config_forecast_validation(r)
    r.summary()


if __name__ == "__main__":
    main()
