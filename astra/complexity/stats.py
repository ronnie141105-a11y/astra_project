"""
Small, pure statistics helpers for cluster complexity assessment (Milestone 4).

Two functions live here because they have different-enough mathematics
from `astra.utils.geodesy` (position/distance arithmetic) that bundling
them there would blur that module's purpose. Both are pure and
dependency-free (only the standard library `math` module), and both are
directly unit-testable against hand-computable cases -- see
`tests/test_complexity.py`.
"""

import math
from typing import Sequence

#: Ceiling applied to `circular_std_dev_deg`. The raw formula
#: `sqrt(-2 * ln(R))` diverges to infinity as the mean resultant length R
#: approaches zero (headings uniformly spread around the compass), which
#: is not a usable score. Values are physically meaningless as *specific*
#: degrees once every possible heading value is equally interpretable,
#: they only need to say "maximally diverse" -- so anything beyond a
#: near-uniform distribution is capped at 180 degrees, the maximum
#: meaningful angular spread.
_CIRCULAR_STD_DEV_CAP_DEG = 180.0

#: Floor applied to the mean resultant length R before taking its
#: logarithm, to keep the `sqrt(-2 * ln(R))` computation finite for
#: perfectly (or near-perfectly) uniform heading distributions.
_MIN_RESULTANT_LENGTH = 1.0e-9


def circular_std_dev_deg(headings_deg: Sequence[float]) -> float:
    """Circular standard deviation of a set of compass headings.

    Ordinary (linear) standard deviation is unsuitable for headings
    because it does not understand wrap-around -- headings of 350 deg and
    10 deg are 20 deg apart on a compass but ~340 apart arithmetically.
    This uses the standard circular-statistics definition (Mardia &
    Jupp, *Directional Statistics*, 2000): headings are treated as unit
    vectors, averaged, and the length of the resulting mean vector (the
    "mean resultant length" R, in [0, 1]) is converted to an angular
    spread via ``sqrt(-2 * ln(R))``. R close to 1 means the headings are
    tightly clustered (small spread); R close to 0 means they are spread
    around the full compass (large spread, capped -- see
    `_CIRCULAR_STD_DEV_CAP_DEG`).

    This is the metric the literature calls ``sigma_hdg`` /
    ``HDGSTDDEV`` in both reference ASTRA documents, used there as one of
    the complexity indicators distinguishing parallel traffic flows (low
    heading diversity, lower complexity) from converging/crossing flows
    (high heading diversity, higher complexity).

    Args:
        headings_deg: Compass headings in degrees, each in [0, 360). Order
            does not matter. An empty sequence is defined as zero spread.

    Returns:
        The circular standard deviation in degrees, in
        ``[0, _CIRCULAR_STD_DEV_CAP_DEG]``.
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
    """Population standard deviation of a sequence of numbers.

    Ordinary linear standard deviation, used here for altitude spread
    within a cluster (`alt_div`). "Population" (dividing by N rather than
    N-1) is used rather than the sample estimator because a `Cluster`'s
    members are the complete, exact set of aircraft in that group at that
    instant -- not a sample drawn from a larger population.

    Args:
        values: Numeric values (e.g. altitudes in feet). Order does not
            matter. A sequence of 0 or 1 values is defined as zero spread
            (no variation possible).

    Returns:
        The population standard deviation, in the same units as ``values``.
    """
    n = len(values)
    if n <= 1:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance)
