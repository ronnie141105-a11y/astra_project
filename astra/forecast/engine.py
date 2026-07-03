"""
4DARHAC forecast engine (Milestone 6).

``ForecastEngine`` estimates onset/peak/dissipation times and a composite
confidence for each open, confirmed-or-later ``FourDArhac`` track, using
this cycle's predicted-horizon ``ComplexityRegion``s already computed by
``ClusterEngine``/``ComplexityEngine`` but not previously consumed beyond
horizon 0. Stateless: unlike ``TrackerEngine``, it does not own tracks --
it is called once per track, per poll cycle, after
``TrackerEngine.update()`` has already run. See docs/milestone_6_forecast.md.
"""

import math
from typing import Dict, List

from astra.complexity.models import ComplexityRegion
from astra.forecast.horizon_series import build_series
from astra.forecast.projection import linear_crossing_time, predicted_peak
from astra.tracking.models import FourDArhac
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: Statuses eligible for forecasting. CANDIDATE tracks are excluded
#: (forecasting a possibly-noise track risks amplifying single-cycle
#: DBSCAN artifacts); CLOSED tracks are no longer being observed, so
#: there is nothing left to associate predicted horizons against.
_FORECASTABLE_STATUSES = frozenset({"CONFIRMED", "GROWING", "PEAK", "DISSIPATING"})


class ForecastEngine:
    """Estimates onset/peak/dissipation times and confidence for tracks.

    Stateless after construction; safe to share one instance across the
    whole ASTRA process.

    Example::

        tracks = tracker.update(regions_by_horizon)
        forecast_engine.forecast_many(tracks, regions_by_horizon)
        for t in tracks:
            print(t.predicted_onset_s, t.confidence)
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise from shared config (thresholds, decay constant)."""
        self._config = config
        _LOG.debug(
            "ForecastEngine initialised. onset=%.1f dissipation=%.1f "
            "min_matched_horizons=%d decay_s=%.0f",
            config.forecast_onset_threshold,
            config.forecast_dissipation_threshold,
            config.forecast_min_matched_horizons,
            config.forecast_confidence_decay_s,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def forecast(
        self, track: FourDArhac, regions_by_horizon: Dict[int, List[ComplexityRegion]]
    ) -> FourDArhac:
        """Populate one track's forecast fields in place, this poll cycle.

        Args:
            track: An open track from ``TrackerEngine`` (any status).
                Mutated in place if forecastable; returned unchanged
                otherwise.
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
                keyed by ``horizon_min``, exactly as passed to
                ``TrackerEngine.update()``.

        Returns:
            The same ``track``, for convenient chaining.
        """
        if track.status not in _FORECASTABLE_STATUSES or not track.track:
            return track

        track.forecast_urgency_rank = None
        detection_ramp = track.confidence
        series, matched_count, total_horizons = build_series(
            track, regions_by_horizon, self._config.tracking_jaccard_threshold
        )
        horizon_coverage = matched_count / total_horizons if total_horizons else 0.0

        if matched_count < self._config.forecast_min_matched_horizons:
            track.predicted_onset_s = None
            track.predicted_dissipation_s = None
            track.predicted_peak_time_s = None
            track.confidence = detection_ramp * horizon_coverage
            return track

        anchor_time_s, current_score = series[0]

        track.predicted_onset_s = None
        if current_score < self._config.forecast_onset_threshold:
            track.predicted_onset_s = linear_crossing_time(
                series, self._config.forecast_onset_threshold, rising=True
            )

        track.predicted_dissipation_s = None
        if current_score >= self._config.forecast_dissipation_threshold:
            track.predicted_dissipation_s = linear_crossing_time(
                series, self._config.forecast_dissipation_threshold, rising=False
            )

        future_points = series[1:]
        peak = predicted_peak(future_points, track.peak_complexity)
        track.predicted_peak_time_s = None
        if peak is not None:
            track.predicted_peak_time_s, track.peak_complexity = peak
            track.peak_time_s = track.predicted_peak_time_s

        lead_time_s = max(0.0, series[-1][0] - anchor_time_s)
        decay = 1.0 - math.exp(-lead_time_s / self._config.forecast_confidence_decay_s)
        track.confidence = detection_ramp * horizon_coverage * (1.0 - decay)

        _LOG.debug(
            "Forecast %s: onset=%s dissipation=%s peak_time=%s confidence=%.2f "
            "(matched=%d/%d)",
            track.arhac_id,
            track.predicted_onset_s,
            track.predicted_dissipation_s,
            track.predicted_peak_time_s,
            track.confidence,
            matched_count,
            total_horizons,
        )
        return track

    def forecast_many(
        self,
        tracks: List[FourDArhac],
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
    ) -> List[FourDArhac]:
        """Forecast every track in a list, then rank by forecast urgency.

        Args:
            tracks: Tracks to forecast (any status; non-forecastable ones
                pass through unchanged -- see ``forecast()``).
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
                keyed by ``horizon_min``.

        Returns:
            The same ``tracks`` list, each entry mutated in place.
        """
        for track in tracks:
            self.forecast(track, regions_by_horizon)
        self._assign_urgency_rank(tracks)
        return tracks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_urgency_rank(tracks: List[FourDArhac]) -> None:
        """Rank tracks by soonest ``predicted_onset_s`` (1 = most urgent).

        Tracks with no predicted onset this cycle (already active, no
        crossing found, insufficient matched horizons, or not
        forecastable at all) keep ``forecast_urgency_rank = None`` --
        deliberately separate from ``priority``, see
        docs/milestone_6_forecast.md OQ-4.
        """
        due = [t for t in tracks if t.predicted_onset_s is not None]
        due.sort(key=lambda t: t.predicted_onset_s)
        for rank, track in enumerate(due, start=1):
            track.forecast_urgency_rank = rank
