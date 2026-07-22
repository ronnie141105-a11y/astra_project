"""
Geo-network helpers for building operationally-realistic scenario presets.

`scenario_presets.py`'s original presets place aircraft at hand-picked
lat/lon points chosen only to satisfy the pipeline's structural
constraints (see that module's docstring). This module instead lets a
preset builder work in terms of the *real* published network already
shipped for the map layer (`astra/dashboard/geo/airways.json` and
`geo/sectors.json`):

* `airway_leg(designator)` -- the ordered `(name, lat, lon)` list for one
  airway, read once and cached.
* `sub_route(designator, start_name, end_name)` -- the contiguous slice
  of that airway between two named waypoints, in either direction.
* `route_after(designator, from_name)` / `route_before` -- convenience
  slices to/from one named fix, for spawning traffic that is inbound to
  or outbound from a merge point.
* `advance_from_route_start(coords, distance_nm)` -- walk `distance_nm`
  along a polyline from its first point, returning an interpolated
  start position plus the still-ahead waypoints. This is what lets
  aircraft spawn *between* waypoints (mid-leg), not stacked exactly on
  top of them, while still being created as route-following via
  `MockConnector`'s existing `route_waypoints` mechanism.
* `sectors_by_number(*numbers)` -- named-sector polygons (any vertical
  layer) for the requested HCM ACC sector numbers, for tagging/filtering
  generated traffic by sector membership with a real point-in-polygon
  test (not the circular `SectorDefinition` approximation
  `astra.complexity.sector` uses for scoring).
* `sector_containing(lat, lon)` -- which of the loaded sector polygons
  (if any) contains a point, for spreading generated traffic so it
  actually lands inside the sectors it's labelled with.

Pure data/geometry, no Flask/pipeline imports, so preset modules and
offline demo scripts can both use it without pulling in the dashboard.
"""

import json
import math
import os
from typing import Dict, List, NamedTuple, Optional, Tuple

from astra.utils.geodesy import bearing_deg, haversine_distance_nm, move_position

_GEO_DIR = os.path.join(os.path.dirname(__file__), "geo")
_AIRWAYS_PATH = os.path.join(_GEO_DIR, "airways.json")
_SECTORS_PATH = os.path.join(_GEO_DIR, "sectors.json")

LatLon = Tuple[float, float]


class Sector(NamedTuple):
    """One sector polygon (one vertical layer of one named sector)."""

    id: str
    name: str
    number: str  # e.g. "1", "7" -- parsed out of `name`/`id` for filtering
    vertical_layer: str
    polygon: List[LatLon]  # closed or open ring, (lat, lon) pairs


# ----------------------------------------------------------------------
# Airways
# ----------------------------------------------------------------------

_airways_cache: Optional[Dict[str, List[Tuple[str, float, float]]]] = None


def _load_airways() -> Dict[str, List[Tuple[str, float, float]]]:
    """`{designator: [(waypoint_name, lat, lon), ...]}`, cached after first read."""
    global _airways_cache
    if _airways_cache is None:
        with open(_AIRWAYS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cache = {}
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            designator = props.get("designator")
            names = props.get("waypoints", [])
            coords = feature.get("geometry", {}).get("coordinates", [])
            if not designator or len(names) != len(coords):
                continue
            cache[designator] = [
                (name, lat, lon) for name, (lon, lat) in zip(names, coords)
            ]
        _airways_cache = cache
    return _airways_cache


def airway_leg(designator: str) -> List[Tuple[str, float, float]]:
    """Full ordered `(name, lat, lon)` list for one airway.

    Raises:
        KeyError: `designator` is not in `geo/airways.json`.
    """
    airways = _load_airways()
    if designator not in airways:
        raise KeyError(f"Unknown airway designator '{designator}'.")
    return airways[designator]


def waypoint_latlon(designator: str, name: str) -> LatLon:
    """Look up one named waypoint's position on a given airway."""
    for wp_name, lat, lon in airway_leg(designator):
        if wp_name == name:
            return lat, lon
    raise KeyError(f"Waypoint '{name}' not found on airway '{designator}'.")


def sub_route(
    designator: str, start_name: str, end_name: str
) -> List[Tuple[str, float, float]]:
    """The contiguous slice of `designator` from `start_name` to `end_name`.

    Works in either direction along the published airway (if `end_name`
    comes before `start_name` in the underlying list, the slice is
    reversed) so callers don't need to know the airway's filed
    direction to fly it "backwards" (e.g. an inbound aircraft flying
    TSH -> AC -> BMT the opposite way to the airway's own listing).

    Returns:
        `[(name, lat, lon), ...]` inclusive of both endpoints.

    Raises:
        KeyError: either waypoint name is not on this airway.
    """
    leg = airway_leg(designator)
    names = [n for n, _, _ in leg]
    if start_name not in names:
        raise KeyError(f"'{start_name}' not found on airway '{designator}'.")
    if end_name not in names:
        raise KeyError(f"'{end_name}' not found on airway '{designator}'.")
    i, j = names.index(start_name), names.index(end_name)
    if i <= j:
        return leg[i : j + 1]
    return list(reversed(leg[j : i + 1]))


def route_after(designator: str, from_name: str, end_name: Optional[str] = None) -> List[Tuple[str, float, float]]:
    """`sub_route` from `from_name` to the airway's last waypoint (or `end_name`)."""
    leg = airway_leg(designator)
    return sub_route(designator, from_name, end_name or leg[-1][0])


def route_before(designator: str, to_name: str, start_name: Optional[str] = None) -> List[Tuple[str, float, float]]:
    """`sub_route` from the airway's first waypoint (or `start_name`) to `to_name`."""
    leg = airway_leg(designator)
    return sub_route(designator, start_name or leg[0][0], to_name)


class RouteStart(NamedTuple):
    """Result of walking partway along a polyline from its first point."""

    lat: float
    lon: float
    heading_deg: float
    #: Waypoints still ahead of the interpolated start position, in
    #: order -- directly usable as a `PresetAircraft["route_waypoints"]`
    #: / `MockConnector.create_aircraft(route_waypoints=...)` value.
    remaining_waypoints: List[LatLon]


def advance_from_route_start(
    coords: List[LatLon], distance_nm: float
) -> RouteStart:
    """Interpolate `distance_nm` along `coords` from its first point.

    Lets a generated aircraft spawn mid-leg (e.g. "18 NM past BMT along
    W1") instead of stacked exactly on a named fix, while still being
    created as a normal route-following aircraft: feed the returned
    `remaining_waypoints` straight into `route_waypoints`.

    Args:
        coords: `[(lat, lon), ...]`, at least 2 points, first-to-last in
            the direction of flight.
        distance_nm: Distance to walk from `coords[0]`, >= 0. If this
            exceeds the polyline's total length, the result sits at the
            final point with an empty `remaining_waypoints` and the
            heading of the last leg.

    Returns:
        A `RouteStart` with the interpolated position, the heading of
        the leg it's on, and every waypoint still ahead of it.
    """
    if len(coords) < 2:
        raise ValueError("advance_from_route_start needs at least 2 points.")

    remaining = distance_nm
    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        leg_nm = haversine_distance_nm(lat1, lon1, lat2, lon2)
        heading = bearing_deg(lat1, lon1, lat2, lon2)
        if remaining < leg_nm - 1e-9:
            # Stops strictly inside this leg: coords[i+1] and everything
            # after it is still ahead.
            lat, lon = move_position(lat1, lon1, heading, max(remaining, 0.0))
            return RouteStart(lat, lon, heading, list(coords[i + 1 :]))
        # Reaches (or passes) coords[i+1] exactly -- that waypoint is now
        # consumed; keep walking the remaining distance into the next leg.
        remaining -= leg_nm

    # distance_nm >= total polyline length: sit at the final point, no
    # waypoints left ahead.
    last_heading = bearing_deg(coords[-2][0], coords[-2][1], coords[-1][0], coords[-1][1])
    return RouteStart(coords[-1][0], coords[-1][1], last_heading, [])


# ----------------------------------------------------------------------
# Sectors
# ----------------------------------------------------------------------

_sectors_cache: Optional[List[Sector]] = None


def _parse_sector_number(name: str, sector_id: str) -> str:
    """Extract the sector number from e.g. 'Sector 7 Ho Chi Minh ACC' -> '7'."""
    for token in name.replace("-", " ").split():
        if token.isdigit():
            return token
    # Fall back to the id, e.g. "sector_7" / "sector_1a" -> "7" / "1".
    digits = "".join(ch for ch in sector_id if ch.isdigit())
    return digits or sector_id


def _load_sectors() -> List[Sector]:
    global _sectors_cache
    if _sectors_cache is None:
        with open(_SECTORS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        sectors = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            if geom.get("type") != "Polygon":
                continue
            rings = geom.get("coordinates", [])
            if not rings:
                continue
            outer = rings[0]
            polygon = [(lat, lon) for lon, lat in outer]
            sector_id = props.get("id", "?")
            name = props.get("name", sector_id)
            sectors.append(
                Sector(
                    id=sector_id,
                    name=name,
                    number=_parse_sector_number(name, sector_id),
                    vertical_layer=props.get("vertical_layer", ""),
                    polygon=polygon,
                )
            )
        _sectors_cache = sectors
    return _sectors_cache


def sectors_by_number(*numbers: str) -> List[Sector]:
    """All polygon slabs (every vertical layer) belonging to the given sector numbers.

    Args:
        *numbers: Sector numbers as strings, e.g. `sectors_by_number("1", "2", "5", "6", "7")`.
    """
    wanted = {str(n) for n in numbers}
    return [s for s in _load_sectors() if s.number in wanted]


def _point_in_polygon(lat: float, lon: float, polygon: List[LatLon]) -> bool:
    """Standard ray-casting point-in-polygon test, (lat, lon) pairs."""
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    x, y = lon, lat
    x1, y1 = polygon[0][1], polygon[0][0]
    for i in range(1, n + 1):
        x2, y2 = polygon[i % n][1], polygon[i % n][0]
        if ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-15) + x1
        ):
            inside = not inside
        x1, y1 = x2, y2
    return inside


def sector_containing(lat: float, lon: float, candidates: Optional[List[Sector]] = None) -> Optional[Sector]:
    """First loaded sector polygon (optionally restricted to `candidates`) containing the point.

    Vertical layering is ignored (any slab of a sector counts as "in
    that sector" for placement/labelling purposes) -- good enough for
    spreading generated traffic across named sectors; exact
    altitude-aware membership is `SectorComplexityEngine`'s job at
    scoring time, not this module's.
    """
    for sector in candidates if candidates is not None else _load_sectors():
        if _point_in_polygon(lat, lon, sector.polygon):
            return sector
    return None


def hcm_sector_label(lat: float, lon: float) -> Optional[str]:
    """Canonical "HCM-S<number>" label for a point, or `None` if no sector data is loaded.

    `geo/sectors.json` has multiple polygon *slabs* per sector number --
    one per vertical layer (e.g. `sector_2a`/`sector_2b` for Sector 2's
    GND-FL460 and GND-FL305 layers) -- which is exactly right for
    `sector_containing()`'s "which slab, if any, contains this point"
    use, but wrong for a *display label*: naively surfacing the winning
    slab's raw `id` (or a label built from it) leaks that internal
    vertical-layer letter (e.g. "HCM-S2A", "HCM-S2B") into the HMI,
    where the sector is a single number regardless of which of its
    layers matched. This collapses that back to one label per number --
    ignoring vertical layering entirely, same as `sector_containing()`
    already does for its own purpose -- and formats it the same way
    the whole HMI expects: `HCM-S<number>`, no trailing letter.

    Falls back to the nearest sector's centroid (still one candidate
    per number, not per slab) when the point sits outside every loaded
    polygon, so an aircraft/track just outside a sector boundary still
    gets a sensible label instead of `None`.
    """
    sectors = _load_sectors()
    if not sectors:
        return None
    hit = sector_containing(lat, lon, sectors)
    if hit is not None:
        return f"HCM-S{hit.number}"
    # One representative slab per number for the nearest-centroid fallback --
    # multiple slabs of the same number would otherwise just be redundant
    # (near-identical) candidates in the distance comparison below.
    by_number: Dict[str, Sector] = {}
    for sector in sectors:
        by_number.setdefault(sector.number, sector)
    best_number = None
    best_dist = float("inf")
    for number, sector in by_number.items():
        clat = sum(p[0] for p in sector.polygon) / len(sector.polygon)
        clon = sum(p[1] for p in sector.polygon) / len(sector.polygon)
        dist = haversine_distance_nm(lat, lon, clat, clon)
        if dist < best_dist:
            best_dist = dist
            best_number = number
    return f"HCM-S{best_number}" if best_number is not None else None


def polyline_length_nm(coords: List[LatLon]) -> float:
    """Total great-circle length of an ordered `(lat, lon)` polyline."""
    total = 0.0
    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        total += haversine_distance_nm(lat1, lon1, lat2, lon2)
    return total


def extend_route_backward(coords: List[LatLon], extension_nm: float) -> List[LatLon]:
    """Prepend a synthetic point `extension_nm` behind `coords[0]`.

    The synthetic point continues the reciprocal of the first leg's
    bearing, so the returned polyline's initial heading (and every
    waypoint after `coords[0]`) is unchanged -- it only gives
    `advance_from_route_start` room to place a second, trailing aircraft
    *before* the first named fix while both aircraft share exactly the
    same track. Typical use: two in-trail aircraft on the same airway,
    N NM apart, both created via `advance_from_route_start` on this
    extended list at two different distances.
    """
    if len(coords) < 2:
        raise ValueError("extend_route_backward needs at least 2 points.")
    lat1, lon1 = coords[0]
    lat2, lon2 = coords[1]
    forward_heading = bearing_deg(lat1, lon1, lat2, lon2)
    back_heading = (forward_heading + 180.0) % 360.0
    behind_lat, behind_lon = move_position(lat1, lon1, back_heading, extension_nm)
    return [(behind_lat, behind_lon)] + list(coords)


def bounding_circle(polygon: List[LatLon]) -> Tuple[float, float, float]:
    """Centroid + enclosing radius (NM) approximating a polygon as a circle.

    `astra.complexity.sector.SectorComplexityEngine` models sectors as
    circles (`SectorDefinition`), not arbitrary polygons -- this turns
    one of our real sector polygons into a `(center_lat, center_lon,
    radius_nm)` triple usable there, so sector-overload scenarios can
    get a real per-sector complexity trend instead of only a
    point-in-polygon aircraft count.

    Returns:
        `(center_lat, center_lon, radius_nm)`. The radius is the
        distance from the simple vertex-average centroid to the
        furthest vertex, not a true minimum enclosing circle -- a
        slight overestimate is fine here since it only needs to contain
        the traffic we deliberately place inside the real polygon.
    """
    if not polygon:
        raise ValueError("bounding_circle needs a non-empty polygon.")
    center_lat = sum(lat for lat, _ in polygon) / len(polygon)
    center_lon = sum(lon for _, lon in polygon) / len(polygon)
    radius_nm = max(
        haversine_distance_nm(center_lat, center_lon, lat, lon) for lat, lon in polygon
    )
    return center_lat, center_lon, radius_nm


def circle_approx_for_sector_number(number: str) -> Tuple[float, float, float]:
    """`bounding_circle` for one representative lateral shape of sector `number`.

    A sector number can have multiple polygon slabs in `geo/sectors.json`
    (e.g. "sector_1a"/"sector_1b"), but they are not always the same
    lateral area at different altitude bands -- some (e.g. "sector_2b")
    are annotated as a separate "additional area". Merging every slab's
    vertices would produce a circle far larger than any real slab (it
    would try to cover disjoint areas at once), so this picks a single
    representative slab instead: the one whose vertical layer starts
    from the ground ("GND-..."), or the first slab found if none does.
    """
    slabs = sectors_by_number(number)
    if not slabs:
        raise KeyError(f"No sector polygons found for sector number '{number}'.")
    representative = next(
        (s for s in slabs if s.vertical_layer.upper().startswith("GND")), slabs[0]
    )
    return bounding_circle(representative.polygon)


def offset_track_point(lat: float, lon: float, along_heading_deg: float, lateral_nm: float) -> LatLon:
    """Move `lateral_nm` perpendicular (right positive) to `along_heading_deg`.

    Used to nudge a generated aircraft off the exact centreline (e.g.
    departures flown on a reciprocal-ish track offset from the arrival
    flow it crosses) without changing which airway it is "on" for
    labelling purposes.
    """
    perp_heading = (along_heading_deg + 90.0) % 360.0
    return move_position(lat, lon, perp_heading, lateral_nm)
