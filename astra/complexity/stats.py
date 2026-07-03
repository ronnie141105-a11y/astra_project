"""
Circular/linear statistics helpers for complexity assessment (Milestone 4).

See docs/milestone_4_complexity.md for the circular-statistics rationale.
"""

import math
from typing import Sequence

#: Cap for circular_std_dev_deg -- the raw formula diverges as headings
#: approach a uniform spread; capped at the max meaningful angular spread.
_CIRCULAR_STD_DEV_CAP_DEG = 180.0

#: Floor on mean resultant length to keep log() finite for near-uniform data.
_MIN_RESULTANT_LENGTH = 1.0e-9


def circular_std_dev_deg(headings_deg: Sequence[float]) -> float:
    """Circular standard deviation of compass headings, in degrees.

    Handles wrap-around correctly (350 deg and 10 deg are "close").
    Equivalent to the ``HDGSTDDEV`` / ``sigma_hdg`` metric in the
    reference ASTRA documents.

    Args:
        headings_deg: Compass headings in degrees, order-independent.

    Returns:
        Spread in ``[0, _CIRCULAR_STD_DEV_CAP_DEG]``; 0 for an empty input.
    """
    n = len(headings_deg)
    if n == 0:
        return 0.0

    sum_cos = sum(math.cos(math.radians(h)) for h in headings_deg)
    sum_sin = sum(math.sin(math.radians(h)) for h in headings_deg)
    mean_resultant_length = math.hypot(sum_cos, sum_sin) / n
    mean_resultant_length = max(mean_resultant_length, _MIN_RESULTANT_LENGTH)
    mean_resultant_length = min(mean_resultant_length, 1.0)

    spread_deg = math.degrees(math.sqrt(-2.0 * math.log(mean_resultant_length)))
    return min(spread_deg, _CIRCULAR_STD_DEV_CAP_DEG)


def population_std_dev(values: Sequence[float]) -> float:
    """Population standard deviation (used here for altitude spread).

    Args:
        values: Numeric values, order-independent.

    Returns:
        Standard deviation; 0 for 0 or 1 values.
    """
    n = len(values)
    if n <= 1:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance)