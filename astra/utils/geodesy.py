"""
Geodesy utilities for the ASTRA prototype.

Why this module is here (Phase 1, not Phase 3)
------------------------------------------------
Three separate later phases all need geographic arithmetic:

* Phase 2 (trajectory prediction): dead-reckoning needs `move_position`.
* Phase 3 (DBSCAN clustering): the DBSCAN distance metric is haversine
  distance in NM, matching the 15 NM horizontal separation threshold from
  `ASTRAConfig.separation_horizontal_nm`.
* Phase 1 (mock connector): `MockConnector.poll()` propagates synthetic
  aircraft positions by dead-reckoning — so it needs `move_position` *right
  now*, before any later phase exists.
* Phase 4 (complexity assessment): pairwise closest-point-of-approach (CPA)
  computation for MTCA/LTCA conflict counting needs a local, Euclidean
  (East/North) coordinate frame — see `local_tangent_plane_nm` below.

Defining these functions here, in `utils`, keeps them a zero-dependency
foundation that any later package can import without creating a circular
dependency between `astra.hotspot` and `astra.trajectory`, for example.

All functions work in decimal degrees (WGS-84) and nautical miles, matching
the unit conventions of the whole ASTRA codebase (see `units.py`).

Design decision — pure Python, no numpy
-----------------------------------------
These functions are called per-aircraft per-poll-cycle. For the scale of
a thesis prototype (tens to low-hundreds of aircraft, 1 Hz polling), the
overhead of calling `math` is negligible. Keeping this module free of
numpy means it can be imported in *any* environment that has Python's
standard library, which matters for unit tests that deliberately avoid
installing simulation dependencies.
"""

import math
from typing import Tuple

# Earth mean radius in nautical miles.
# Value: 6371008.8 m / 1852 m/NM ≈ 3440.065 NM.
_EARTH_RADIUS_NM: float = 3440.065


def haversine_distance_nm(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """Great-circle distance between two WGS-84 points, in nautical miles.

    Uses the haversine formula, which is numerically well-conditioned for
    the short distances (< 100 NM) typical of individual en-route hotspots,
    and is the same metric referenced in the SESAR ASTRA documents for the
    15 NM / 1000 ft DBSCAN neighbourhood definition.

    Args:
        lat1_deg: Latitude of point 1, decimal degrees.
        lon1_deg: Longitude of point 1, decimal degrees.
        lat2_deg: Latitude of point 2, decimal degrees.
        lon2_deg: Longitude of point 2, decimal degrees.

    Returns:
        Great-circle distance in nautical miles (always non-negative).
    """
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    dlat = math.radians(lat2_deg - lat1_deg)
    dlon = math.radians(lon2_deg - lon1_deg)

    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return _EARTH_RADIUS_NM * c


def bearing_deg(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """Initial true bearing from point 1 to point 2, in degrees [0, 360).

    Returns the forward azimuth at point 1 of the great-circle path to
    point 2. "Initial" means the bearing at the departure point; for the
    short inter-aircraft distances in this prototype the bearing change
    along the arc is negligible.

    Args:
        lat1_deg: Latitude of origin, decimal degrees.
        lon1_deg: Longitude of origin, decimal degrees.
        lat2_deg: Latitude of destination, decimal degrees.
        lon2_deg: Longitude of destination, decimal degrees.

    Returns:
        True bearing in degrees, in the range [0, 360).
    """
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    dlon = math.radians(lon2_deg - lon1_deg)

    x = math.sin(dlon) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    bearing = math.degrees(math.atan2(x, y))
    return bearing % 360.0


def move_position(
    lat_deg: float,
    lon_deg: float,
    heading_deg: float,
    distance_nm: float,
) -> Tuple[float, float]:
    """Dead-reckoning: new position after travelling a given distance.

    Computes the destination point reached by starting at (`lat_deg`,
    `lon_deg`), travelling along the initial great-circle bearing
    `heading_deg` (true north = 0°) for `distance_nm` nautical miles.

    This is the direct (Vincenty/spherical) formula. For the distances
    used in ASTRA trajectory prediction (sub-100 NM per prediction step)
    the spherical approximation introduces at most ~0.3 % error, which is
    well within the accuracy of the kinematic trajectory model itself.

    Args:
        lat_deg: Starting latitude, decimal degrees.
        lon_deg: Starting longitude, decimal degrees.
        heading_deg: True heading of travel, degrees [0, 360).
        distance_nm: Distance to travel, nautical miles.

    Returns:
        A (latitude, longitude) tuple in decimal degrees.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    brng = math.radians(heading_deg)
    ang_dist = distance_nm / _EARTH_RADIUS_NM  # angular distance in radians

    lat2 = math.asin(
        math.sin(lat) * math.cos(ang_dist)
        + math.cos(lat) * math.sin(ang_dist) * math.cos(brng)
    )
    lon2 = lon + math.atan2(
        math.sin(brng) * math.sin(ang_dist) * math.cos(lat),
        math.cos(ang_dist) - math.sin(lat) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def local_tangent_plane_nm(
    lat0_deg: float,
    lon0_deg: float,
    lat_deg: float,
    lon_deg: float,
) -> Tuple[float, float]:
    """Project a point onto a local East/North tangent plane, in NM.

    Used by Phase 4 (complexity assessment) to compute pairwise
    closest-point-of-approach (CPA) between aircraft using plain vector
    arithmetic. Great-circle geometry has no simple closed-form CPA
    solution for two moving points; projecting onto a local flat-Earth
    tangent plane anchored at ``(lat0_deg, lon0_deg)`` (conventionally the
    cluster centroid) does, and the approximation error is negligible at
    the scale this system operates on -- clusters span at most a few tens
    of NM (`ASTRAConfig.separation_horizontal_nm` = 15 NM base neighbourhood,
    with chained DBSCAN groups larger but still regional, not global).

    This is an equirectangular projection: longitude is scaled by
    ``cos(lat0_deg)`` to account for meridian convergence, matching the
    same small-angle approximation already used implicitly by
    `haversine_distance_nm` at these distances.

    Args:
        lat0_deg: Latitude of the projection origin, decimal degrees
            (typically a cluster centroid).
        lon0_deg: Longitude of the projection origin, decimal degrees.
        lat_deg: Latitude of the point to project, decimal degrees.
        lon_deg: Longitude of the point to project, decimal degrees.

    Returns:
        An ``(x_nm, y_nm)`` tuple: ``x_nm`` is the East displacement from
        the origin, ``y_nm`` is the North displacement, both in nautical
        miles.
    """
    lat0 = math.radians(lat0_deg)
    x_nm = math.radians(lon_deg - lon0_deg) * math.cos(lat0) * _EARTH_RADIUS_NM
    y_nm = math.radians(lat_deg - lat0_deg) * _EARTH_RADIUS_NM
    return x_nm, y_nm
