"""
Per-track predicted-horizon series construction (Milestone 6).

Pure function, no engine state -- turns one track's observed anchor plus
this cycle's matched predicted horizons into the discrete ``(time_s,
complexity_score)`` series that ``astra.forecast.projection`` interpolates
over. Reuses ``astra.tracking.association.best_cluster_match`` rather than
reimplementing matching -- see docs/milestone_6_forecast.md.
"""

from typing import Dict, List, Tuple

from astra.complexity.models import ComplexityRegion
from astra.tracking.association import best_cluster_match
from astra.tracking.models import FourDArhac

#: (time_s, complexity_score) point.
SeriesPoint = Tuple[float, float]


def build_series(
    track: FourDArhac,
    regions_by_horizon: Dict[int, List[ComplexityRegion]],
    jaccard_threshold: float,
) -> Tuple[List[SeriesPoint], int, int]:
    """Build this cycle's forecast series for one track.

    Args:
        track: The track to forecast. Must have at least one entry in
            ``track.track`` (true for every ``CONFIRMED`` or later track).
        regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
            keyed by ``horizon_min`` (0 = observed, already folded into
            ``track`` by ``TrackerEngine``; only non-zero horizons are
            considered here).
        jaccard_threshold: Minimum Jaccard similarity for a primary match
            (``ASTRAConfig.tracking_jaccard_threshold``, reused as-is --
            the same signal that already links this track cycle to
            cycle applies equally to linking it to its own predicted
            horizons).

    Returns:
        ``(series, matched_count, total_horizons)``:
            ``series`` -- ascending-time ``(time_s, complexity_score)``
                points: the track's most recent observed entry, followed
                by every predicted horizon whose cluster matched this
                track this cycle.
            ``matched_count`` -- number of predicted horizons that
                matched.
            ``total_horizons`` -- number of non-zero horizon keys present
                in ``regions_by_horizon`` this cycle (the denominator for
                horizon coverage).
    """
    last_region = track.track[-1]
    series: List[SeriesPoint] = [(last_region.computed_at_s, last_region.complexity_score)]

    predicted_horizons = {
        horizon_min: regions
        for horizon_min, regions in regions_by_horizon.items()
        if horizon_min != 0
    }

    matched_count = 0
    for horizon_min in sorted(predicted_horizons):
        regions = predicted_horizons[horizon_min]
        if not regions:
            continue
        region_by_cluster = {region.cluster: region for region in regions}
        match = best_cluster_match(
            last_region.cluster, list(region_by_cluster.keys()), jaccard_threshold
        )
        if match is None:
            continue
        matched_region = region_by_cluster[match]
        series.append((matched_region.computed_at_s, matched_region.complexity_score))
        matched_count += 1

    series.sort(key=lambda point: point[0])
    return series, matched_count, len(predicted_horizons)
