"""
Pairwise conflict detection for cluster complexity assessment (Milestone 4).

Both reference ASTRA documents define two conflict-alert categories over
a pair of aircraft's predicted closest point of approach (CPA):

* MTCA (Medium-Term Conflict Alert): CPA distance < ``mtca_distance_nm``
  AND time-to-CPA < ``mtca_time_min``.
* LTCA (Long-Term Conflict Alert): CPA distance < ``ltca_distance_nm``
  AND time-to-CPA < ``ltca_time_min``, EXCLUDING pairs already counted as
  MTCA (see `framework_for_predict_and_resolve_hotspot.md` Sec 2.4.1 /
  `Tài_liệu_kỹ_thuật_ASTRA.md` Sec 3.4.1 -- "NOLTCA ... excludes the
  MTCAs").

CPA computation
----------------
Two aircraft flying at constant velocity have a closed-form closest
point of approach: project both onto a local East/North tangent plane
(`astra.utils.geodesy.local_tangent_plane_nm`) anchored at their
midpoint (or any nearby fixed reference -- only relative geometry
matters), express both as `position + velocity * t`, and minimise the
squared separation distance over `t`. This reduces to a single dot
product; see `closest_point_of_approach` for the derivation in code and
`tests/test_complexity.py` for hand-verified cases (head-on, parallel,
diverging, perpendicular crossing).

This module deliberately works with *straight-line* constant-velocity
kinematics only, consistent with `astra.trajectory.engine.TrajectoryEngine`
(Phase 2) -- it estimates conflicts from each aircraft's instantaneous
heading/speed/vertical-speed at the snapshot being assessed, not from a
full re-prediction. This is an intentional simplification: see "Known
limitations" in `Developer_Handover.md`.
"""

import math
from typing import List, NamedTuple, Optional, Tuple

from astra.interface.traffic_state import AircraftState
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import local_tangent_plane_nm

#: Relative speed (squared, kt^2) below which two aircraft are treated as
#: non-converging (their separation is not decreasing over time). Guards
#: the CPA division against near-zero relative velocity; the same
#: threshold order of magnitude BlueSky/CPA literature commonly uses for
#: "effectively stationary relative to each other".
_MIN_RELATIVE_SPEED_SQ_KT2 = 1.0e-6


class ClosestApproach(NamedTuple):
    """Result of a pairwise closest-point-of-approach computation.

    Attributes:
        distance_nm: Predicted horizontal separation (NM) at the closest
            point of approach, assuming both aircraft hold their current
            heading and ground speed.
        time_to_cpa_min: Minutes from now until that closest point of
            approach. Always >= 0 -- if the aircraft are already
            diverging (moving apart), the closest point of approach is
            "now", so ``distance_nm`` is the current separation and
            ``time_to_cpa_min`` is 0.
    """

    distance_nm: float
    time_to_cpa_min: float


def _velocity_components_kt(heading_deg: float, ground_speed_kt: float) -> Tuple[float, float]:
    """Decompose heading/ground-speed into (East, North) velocity components.

    Args:
        heading_deg: True heading, degrees [0, 360), 0 = north, clockwise.
        ground_speed_kt: Ground speed, knots.

    Returns:
        An ``(vx_kt, vy_kt)`` tuple: East and North velocity components in
        knots (equivalently NM/hour, since 1 kt = 1 NM/hr).
    """
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

    Both aircraft are modelled as moving in a straight line at their
    current heading and ground speed (constant-velocity assumption,
    consistent with `TrajectoryEngine`). The computation is done on a
    local flat-Earth tangent plane, which introduces negligible error at
    cluster scale (see `local_tangent_plane_nm`'s docstring).

    Args:
        reference_lat_deg: Latitude of the tangent-plane projection
            origin, decimal degrees. Any point near both aircraft works;
            callers pass the cluster centroid.
        reference_lon_deg: Longitude of the tangent-plane projection
            origin, decimal degrees.
        aircraft_1: First aircraft's current state.
        aircraft_2: Second aircraft's current state.

    Returns:
        A `ClosestApproach` with the predicted minimum separation and the
        time (from now) at which it occurs.
    """
    x1, y1 = local_tangent_plane_nm(
        reference_lat_deg, reference_lon_deg, aircraft_1.lat, aircraft_1.lon
    )
    x2, y2 = local_tangent_plane_nm(
        reference_lat_deg, reference_lon_deg, aircraft_2.lat, aircraft_2.lon
    )
    vx1, vy1 = _velocity_components_kt(aircraft_1.heading_deg, aircraft_1.ground_speed_kt)
    vx2, vy2 = _velocity_components_kt(aircraft_2.heading_deg, aircraft_2.ground_speed_kt)

    # Relative position and velocity (aircraft_1 relative to aircraft_2).
    rx, ry = x1 - x2, y1 - y2
    rvx, rvy = vx1 - vx2, vy1 - vy2
    relative_speed_sq_kt2 = rvx * rvx + rvy * rvy

    if relative_speed_sq_kt2 < _MIN_RELATIVE_SPEED_SQ_KT2:
        # Not converging (parallel courses / equal velocity vectors):
        # separation does not decrease, so "closest approach" is now.
        time_to_cpa_hr = 0.0
    else:
        # Minimise |r + rv*t|^2 over t: derivative = 2*(r.rv + t*|rv|^2) = 0
        #   => t = -(r . rv) / |rv|^2
        time_to_cpa_hr = -(rx * rvx + ry * rvy) / relative_speed_sq_kt2
        if time_to_cpa_hr < 0.0:
            # Closest approach was in the past (already diverging): the
            # nearest *future* point is now.
            time_to_cpa_hr = 0.0

    dx = rx + rvx * time_to_cpa_hr
    dy = ry + rvy * time_to_cpa_hr
    distance_nm = math.hypot(dx, dy)

    return ClosestApproach(
        distance_nm=distance_nm, time_to_cpa_min=time_to_cpa_hr * 60.0
    )


def classify_conflict(
    approach: ClosestApproach, config: ASTRAConfig
) -> Optional[str]:
    """Classify a closest-point-of-approach result as MTCA, LTCA, or none.

    Args:
        approach: Result from `closest_point_of_approach`.
        config: Shared configuration, providing the MTCA/LTCA
            distance/time thresholds.

    Returns:
        ``"MTCA"`` if both the MTCA distance and time thresholds are met;
        else ``"LTCA"`` if both the (wider) LTCA thresholds are met; else
        ``None`` (no conflict alert). MTCA is checked first and is
        mutually exclusive with LTCA, matching the reference documents'
        "NOLTCA ... excludes the MTCAs" definition.
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

    Evaluates every unordered pair of ``members`` (there are at most
    ``ASTRAConfig.dbscan_min_samples``-and-up members in a cluster, so
    this is always a small, cheap O(n^2) pass -- clusters are, by
    construction, spatially local groups, not the whole traffic sample).

    Args:
        members: Aircraft states to check pairwise (typically a
            `Cluster`'s member aircraft, resolved from their callsigns).
        centroid_lat_deg: Latitude to use as the tangent-plane projection
            origin for every pair (the cluster centroid).
        centroid_lon_deg: Longitude to use as the tangent-plane
            projection origin.
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
