"""
Pure threshold-crossing / peak-detection math for 4DARHAC forecasting
(Milestone 6).

No domain-object dependencies -- operates on plain ``(time_s, score)``
point series. Mirrors ``astra.complexity.stats``'s pattern of small,
dependency-free numeric helpers feeding a stateless engine
(``astra.forecast.engine.ForecastEngine``). See
docs/milestone_6_forecast.md for the design rationale.
"""

from typing import List, Optional, Tuple


def linear_crossing_time(
    points: List[Tuple[float, float]], threshold: float, rising: bool
) -> Optional[float]:
    """First time a piecewise-linear score series crosses ``threshold``.

    Args:
        points: ``(time_s, score)`` pairs, already sorted ascending by
            ``time_s``.
        threshold: The score value to find a crossing of.
        rising: ``True`` to find an upward crossing (score goes from
            below to at/above threshold); ``False`` for a downward
            crossing.

    Returns:
        The linearly-interpolated ``time_s`` of the first qualifying
        crossing between two consecutive points, or ``None`` if no such
        crossing exists in the given series.
    """
    for (t0, s0), (t1, s1) in zip(points, points[1:]):
        if s1 == s0:
            continue
        if rising and s0 < threshold <= s1:
            fraction = (threshold - s0) / (s1 - s0)
            return t0 + fraction * (t1 - t0)
        if not rising and s0 > threshold >= s1:
            fraction = (s0 - threshold) / (s0 - s1)
            return t0 + fraction * (t1 - t0)
    return None


def predicted_peak(
    points: List[Tuple[float, float]], current_peak: float
) -> Optional[Tuple[float, float]]:
    """The highest-scoring future point, if it exceeds ``current_peak``.

    Args:
        points: ``(time_s, score)`` pairs for *future* horizons only
            (the caller should already have excluded the observed "now"
            anchor point).
        current_peak: The highest score already known
            (``FourDArhac.peak_complexity``).

    Returns:
        ``(time_s, score)`` of the highest-scoring future point if it
        exceeds ``current_peak``, else ``None``.
    """
    if not points:
        return None
    best_time, best_score = max(points, key=lambda point: point[1])
    if best_score > current_peak:
        return best_time, best_score
    return None
