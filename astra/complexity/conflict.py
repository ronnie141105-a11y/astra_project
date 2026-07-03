"""
Pairwise MTCA/LTCA conflict detection for complexity assessment (Milestone 4).

Computes closest point of approach (CPA) between aircraft pairs under a
constant-velocity assumption, on a local tangent plane. See
docs/milestone_4_complexity.md for the MTCA/LTCA definitions and CPA
derivation.
"""

import math
from typing import List, NamedTuple, Optional, Tuple

from astra.interface.traffic_state import AircraftState
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import local_tangent_plane_nm

#: Relative speed (kt^2) below which two aircraft are treated as
#: non-converging, guarding the CPA division against near-zero values.
_MIN_RELATIVE_SPEED_SQ_KT2 = 1.0e-6


class ClosestApproach(NamedTuple):
    """Result of a pairwise closest-point-of-approach computation.

    Attributes:
        distance_nm: Predicted horizontal separation at CPA.
        time_to_cpa_min: Minutes until CPA (>= 0; 0 if already diverging).
    """

    distance_nm: float
    time_to_cpa_min: float


def _velocity_components_kt(heading_deg: float, ground_speed_kt: float) -> Tuple[float, float]:
    """Decompose heading/ground-speed into (East, North) kt components."""
    heading_rad = math.radians(heading_deg)
    vx_kt = ground_speed_kt * math.sin(heading_rad)
    vy_kt = ground_speed_kt * math.cos(heading_rad)
    return vx_kt, vy_kt


def closest_point_of_approach(
    reference_lat_deg: float,
    reference_lon_deg: float,
    aircraft_1: AircraftState,
    aircraft_2: AircraftState,
) -> ClosestApproach:
    """Predicted closest point of approach between two aircraft.

    Constant-velocity (current heading/speed) straight-line model,
    consistent with ``TrajectoryEngine``.

    Args:
        reference_lat_deg: Tangent-plane projection origin latitude
            (typically the cluster centroid).
        reference_lon_deg: Tangent-plane projection origin longitude.
        aircraft_1: First aircraft's current state.
        aircraft_2: Second aircraft's current state.

    Returns:
        A `ClosestApproach` with predicted minimum separation and time.
    """
    x1, y1 = local_tangent_plane_nm(
        reference_lat_deg, reference_lon_deg, aircraft_1.lat, aircraft_1.lon
    )
    x2, y2 = local_tangent_plane_nm(
        reference_lat_deg, reference_lon_deg, aircraft_2.lat, aircraft_2.lon
    )
    vx1, vy1 = _velocity_components_kt(aircraft_1.heading_deg, aircraft_1.ground_speed_kt)
    vx2, vy2 = _velocity_components_kt(aircraft_2.heading_deg, aircraft_2.ground_speed_kt)

    # Relative position/velocity of aircraft_1 w.r.t. aircraft_2.
    rx, ry = x1 - x2, y1 - y2
    rvx, rvy = vx1 - vx2, vy1 - vy2
    relative_speed_sq_kt2 = rvx * rvx + rvy * rvy

    if relative_speed_sq_kt2 < _MIN_RELATIVE_SPEED_SQ_KT2:
        # Not converging: separation is constant, so CPA is "now".
        time_to_cpa_hr = 0.0
    else:
        # Minimise |r + rv*t|^2 => t = -(r . rv) / |rv|^2.
        time_to_cpa_hr = -(rx * rvx + ry * rvy) / relative_speed_sq_kt2
        if time_to_cpa_hr < 0.0:
            time_to_cpa_hr = 0.0  # Already diverging: nearest future point is now.

    dx = rx + rvx * time_to_cpa_hr
    dy = ry + rvy * time_to_cpa_hr
    distance_nm = math.hypot(dx, dy)

    return ClosestApproach(
        distance_nm=distance_nm, time_to_cpa_min=time_to_cpa_hr * 60.0
    )


def classify_conflict(
    approach: ClosestApproach, config: ASTRAConfig
) -> Optional[str]:
    """Classify a CPA result as MTCA, LTCA, or no conflict.

    Args:
        approach: Result from `closest_point_of_approach`.
        config: Provides MTCA/LTCA distance/time thresholds.

    Returns:
        ``"MTCA"``, ``"LTCA"`` (mutually exclusive), or ``None``.
    """
    if (
        approach.distance_nm < config.mtca_distance_nm
        and approach.time_to_cpa_min < config.mtca_time_min
    ):
        return "MTCA"
    if (
        approach.distance_nm < config.ltca_distance_nm
        and approach.time_to_cpa_min < config.ltca_time_min
    ):
        return "LTCA"
    return None


def count_conflicts(
    members: List[AircraftState],
    centroid_lat_deg: float,
    centroid_lon_deg: float,
    config: ASTRAConfig,
) -> Tuple[int, int]:
    """Count MTCA and LTCA conflict pairs within a group of aircraft.

    Args:
        members: Aircraft states to check pairwise (a cluster's members).
        centroid_lat_deg: Tangent-plane projection origin latitude.
        centroid_lon_deg: Tangent-plane projection origin longitude.
        config: Shared configuration (MTCA/LTCA thresholds).

    Returns:
        An ``(mtca_count, ltca_count)`` tuple.
    """
    mtca_count = 0
    ltca_count = 0
    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            approach = closest_point_of_approach(
                centroid_lat_deg, centroid_lon_deg, members[i], members[j]
            )
            classification = classify_conflict(approach, config)
            if classification == "MTCA":
                mtca_count += 1
            elif classification == "LTCA":
                ltca_count += 1
    return mtca_count, ltca_count