"""
Custom pairwise distance metric for cluster detection (Milestone 3).

Aircraft are DBSCAN neighbours only if within BOTH a horizontal (NM,
great-circle) and vertical (ft) threshold. See docs/milestone_3_hotspot.md
for why this needs a precomputed matrix rather than a blended scalar.
"""

from typing import Sequence

import numpy as np

from astra.interface.traffic_state import AircraftState
from astra.utils.geodesy import haversine_distance_nm

#: Assigned to any pair failing the vertical gate. Must be finite (DBSCAN
#: rejects inf) and larger than any realistic eps (Earth's circumference
#: is ~21 600 NM).
_NON_NEIGHBOR_DISTANCE = 1.0e9


def build_distance_matrix(
    aircraft: Sequence[AircraftState],
    vertical_separation_ft: float,
) -> np.ndarray:
    """Build a precomputed pairwise (horizontal NM, vertical-gated) distance matrix.

    Args:
        aircraft: Aircraft states in a fixed order; matrix row/col ``i``
            corresponds to ``aircraft[i]``.
        vertical_separation_ft: Max altitude difference (ft) for two
            aircraft to be neighbourhood candidates at all.

    Returns:
        A symmetric ``(N, N)`` array of NM distances (zero diagonal),
        suitable for ``sklearn.cluster.DBSCAN(metric="precomputed")``.
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