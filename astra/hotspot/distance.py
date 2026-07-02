"""
Custom pairwise distance metric for cluster detection (Milestone 3).

DBSCAN takes a single scalar distance between two points and one ``eps``
threshold. ASTRA's neighbourhood definition, however, is genuinely
two-dimensional: two aircraft are only "close" if they satisfy BOTH a
horizontal separation threshold (nautical miles, great-circle) AND a
vertical separation threshold (feet) simultaneously -- this is the same
15 NM / 1000 ft definition used elsewhere in the reference ASTRA
documents (see ``ASTRAConfig.separation_horizontal_nm`` /
``separation_vertical_ft``).

Rather than collapsing horizontal and vertical distance into one blended
scalar (which would let a large vertical separation be "compensated" by
horizontal closeness, or vice versa -- physically wrong for airspace
separation), this module builds a precomputed pairwise distance matrix
where:

* the value between two aircraft is their great-circle distance in NM if
  they are within the configured vertical separation, and
* a very large finite value (effectively "far beyond any realistic eps")
  if they are not -- which unconditionally excludes them from being
  DBSCAN neighbours regardless of horizontal proximity.

``sklearn.cluster.DBSCAN`` is then run with ``metric="precomputed"`` and
``eps=ASTRAConfig.separation_horizontal_nm``, so the horizontal threshold
is enforced by DBSCAN itself and the vertical threshold is enforced by
this matrix -- both constraints apply, neither can substitute for the
other.
"""

from typing import Sequence

import numpy as np

from astra.interface.traffic_state import AircraftState
from astra.utils.geodesy import haversine_distance_nm

#: Distance assigned to any aircraft pair that fails the vertical
#: separation gate. ``sklearn.cluster.DBSCAN`` rejects non-finite values
#: in a precomputed distance matrix, so this must be a large *finite*
#: value rather than ``float("inf")``. Earth's circumference is
#: ~21 600 NM, so any value larger than that is guaranteed to exceed any
#: realistic ``separation_horizontal_nm`` (eps), making such pairs
#: unconditionally non-neighbours regardless of horizontal proximity.
_NON_NEIGHBOR_DISTANCE = 1.0e9


def build_distance_matrix(
    aircraft: Sequence[AircraftState],
    vertical_separation_ft: float,
) -> np.ndarray:
    """Build a precomputed pairwise distance matrix for DBSCAN.

    Args:
        aircraft: Aircraft states to compute pairwise distances for, in a
            fixed order. The returned matrix's row/column ``i`` corresponds
            to ``aircraft[i]``.
        vertical_separation_ft: Maximum altitude difference (feet) for two
            aircraft to be considered neighbourhood candidates at all.
            Aircraft further apart vertically than this are assigned
            ``_NON_NEIGHBOR_DISTANCE`` regardless of horizontal distance.

    Returns:
        A symmetric ``(N, N)`` ``numpy.ndarray`` of distances in nautical
        miles (or ``inf`` where the vertical gate is not satisfied), with
        a zero diagonal, suitable for ``sklearn.cluster.DBSCAN(metric="precomputed")``.
    """
    n = len(aircraft)
    matrix = np.zeros((n, n), dtype=float)

    for i in range(n):
        for j in range(i + 1, n):
            vertical_gap_ft = abs(
                aircraft[i].altitude_ft - aircraft[j].altitude_ft
            )
            if vertical_gap_ft > vertical_separation_ft:
                distance_nm = _NON_NEIGHBOR_DISTANCE
            else:
                distance_nm = haversine_distance_nm(
                    aircraft[i].lat,
                    aircraft[i].lon,
                    aircraft[j].lat,
                    aircraft[j].lon,
                )
            matrix[i, j] = distance_nm
            matrix[j, i] = distance_nm

    return matrix
