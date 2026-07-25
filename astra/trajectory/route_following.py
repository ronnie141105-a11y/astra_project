"""
Shared route-following (polyline) kinematics.

Both ``MockConnector`` (which *generates* simulated ground-truth traffic)
and ``RouteAwareTrajectoryEngine`` (which *predicts* future traffic
independently, from information available now) need to answer the exact
same geometric question: "if an aircraft at (lat, lon) heading toward a
known ordered list of waypoints flies `distance_nm` at its current
heading-to-next-waypoint, where does it end up, and which waypoint is it
now heading toward?"

This is deliberately factored out into one pure function so that:

1. There is exactly one implementation of "how a route is flown" in the
   codebase -- MockConnector's own stepping and the predictor's horizon
   computation cannot silently disagree on turn geometry.
2. Using the same *route/intent* on both sides is not circular reasoning:
   both are independently evaluating a well-defined function of
   (current state, known route, speed). The predictor never reads the
   simulator's already-computed *future* positions -- only the *current*
   route/flight-plan, which is legitimately known now (exactly analogous
   to a real ATC system holding a filed/cleared route for a live flight).
   See ``astra/trajectory/route_engine.py``'s module docstring for the
   evaluation methodology this supports.

No BlueSky or ASTRA-pipeline imports here -- pure geometry only, so it is
trivially unit-testable in isolation (see ``tests/test_trajectory.py``).
"""

from typing import List, NamedTuple, Optional, Tuple

from astra.utils.geodesy import bearing_deg, haversine_distance_nm, move_position

#: Safety cap on waypoint legs consumed in one call, so a very large
#: `distance_nm` (e.g. a 60-minute prediction horizon with many short
#: legs) can never loop unboundedly. Matches MockConnector's own cap.
MAX_LEGS_PER_CALL = 50


class RouteAdvanceResult(NamedTuple):
    """Result of advancing one aircraft along its route by some distance."""

    lat: float
    lon: float
    heading_deg: float
    #: Remaining waypoints still ahead, in order. Empty once the route is
    #: flown in full (the aircraft then continues straight on its last
    #: heading -- it does not stop or loop).
    remaining_waypoints: List[Tuple[float, float]]
    #: True once every waypoint has been reached (``remaining_waypoints``
    #: is empty). Distinguishes "flew the route out" from "was never on
    #: a route" for callers that care (``remaining_waypoints == []``
    #: alone is ambiguous between those two cases).
    route_completed: bool


def advance_along_route(
    lat: float,
    lon: float,
    heading_deg: float,
    route_waypoints: Optional[List[Tuple[float, float]]],
    distance_nm: float,
    cap_at_route_end: bool = False,
) -> RouteAdvanceResult:
    """Advance one aircraft `distance_nm` along its remaining route.

    Heads straight for ``route_waypoints[0]``, consuming one or more legs
    if ``distance_nm`` overshoots the current leg (so a single call can
    correctly cover a long prediction horizon that spans several
    waypoints, not just one simulation tick). Once the final waypoint is
    passed, behaviour depends on ``cap_at_route_end``:

    - ``False`` (default, used by ``MockConnector`` -- actual simulated
      flight): any leftover distance is flown straight on the last
      heading flown, so the aircraft continues past the end of its
      filed route rather than stopping dead, matching real-world
      behaviour when a cleared route runs out before the prediction
      horizon does.
    - ``True`` (used by ``RouteAwareTrajectoryEngine`` -- *predicted*
      trajectories): leftover distance is discarded once the final
      waypoint is reached, so the predicted position never overshoots
      the flight plan's destination/transfer point. A predicted
      position "at" or "beyond" the route end is reported as sitting
      exactly on that final waypoint instead of projecting further
      along a heading the aircraft has no filed intent to keep flying.

    If ``route_waypoints`` is ``None`` or empty, this degrades to plain
    constant-heading dead reckoning at ``heading_deg`` -- callers do not
    need to branch on "has a route" before calling this function.

    Args:
        lat, lon: Current position (decimal degrees).
        heading_deg: Current heading -- used verbatim only when there is
            no route (or no distance left to travel); overwritten by the
            bearing to each waypoint in turn while a route is active.
        route_waypoints: Ordered ``[(lat, lon), ...]`` of remaining
            waypoints, first-to-fly-to first. Not mutated.
        distance_nm: Distance to travel along the route (>= 0).
        cap_at_route_end: If ``True``, stop exactly at the final
            waypoint instead of continuing straight past it once the
            route is fully flown. Defaults to ``False`` (legacy/actual-
            simulation behaviour, unchanged for existing callers).

    Returns:
        A ``RouteAdvanceResult`` with the new position, heading, and
        remaining route.
    """
    if not route_waypoints:
        new_lat, new_lon = move_position(lat, lon, heading_deg, distance_nm)
        return RouteAdvanceResult(new_lat, new_lon, heading_deg, [], False)

    remaining_distance = distance_nm
    remaining_waypoints = list(route_waypoints)
    current_lat, current_lon, current_heading = lat, lon, heading_deg
    legs = 0

    while remaining_waypoints and remaining_distance > 0 and legs < MAX_LEGS_PER_CALL:
        legs += 1
        target_lat, target_lon = remaining_waypoints[0]
        leg_distance_nm = haversine_distance_nm(current_lat, current_lon, target_lat, target_lon)
        current_heading = bearing_deg(current_lat, current_lon, target_lat, target_lon)
        if leg_distance_nm <= remaining_distance:
            current_lat, current_lon = target_lat, target_lon
            remaining_distance -= leg_distance_nm
            remaining_waypoints = remaining_waypoints[1:]
        else:
            current_lat, current_lon = move_position(
                current_lat, current_lon, current_heading, remaining_distance
            )
            remaining_distance = 0.0

    route_completed = not remaining_waypoints
    if remaining_distance > 0 and not (cap_at_route_end and route_completed):
        # Route flown out with distance still to travel -- continue
        # straight on the last heading flown (skipped when capping at
        # the route end: the final waypoint position computed above is
        # the answer, full stop).
        current_lat, current_lon = move_position(
            current_lat, current_lon, current_heading, remaining_distance
        )

    return RouteAdvanceResult(
        current_lat, current_lon, current_heading, remaining_waypoints, route_completed
    )


def remaining_route_distance_nm(
    lat: float, lon: float, route_waypoints: Optional[List[Tuple[float, float]]]
) -> float:
    """Total great-circle distance (NM) from `(lat, lon)` to the end of
    `route_waypoints`, following the polyline through every remaining
    waypoint in order (not a straight line to the last one).

    Used by scenario-generation code that needs to know "how far along
    the route am I" independent of `advance_along_route`'s own per-tick
    stepping -- e.g. a Landing-profile aircraft deciding when to begin
    its descent (40 NM from the final waypoint), which needs the actual
    remaining track distance, not a straight-line shortcut across any
    turns still ahead.

    Args:
        lat, lon: Current position (decimal degrees).
        route_waypoints: Ordered `[(lat, lon), ...]` of remaining
            waypoints, first-to-fly-to first. Not mutated.

    Returns:
        0.0 if `route_waypoints` is `None`/empty (no route, or the
        route has already been fully flown).
    """
    if not route_waypoints:
        return 0.0
    total_nm = 0.0
    prev_lat, prev_lon = lat, lon
    for wp_lat, wp_lon in route_waypoints:
        total_nm += haversine_distance_nm(prev_lat, prev_lon, wp_lat, wp_lon)
        prev_lat, prev_lon = wp_lat, wp_lon
    return total_nm


def top_of_descent_distance_nm(
    altitude_ft: float, groundspeed_kt: float, vertical_rate_fpm: float
) -> float:
    """Distance (NM) before reaching 0 ft that a constant-rate descent
    from `altitude_ft` must begin -- i.e. how far out "top of descent"
    (TOD) is, flying at `groundspeed_kt` and descending at
    `vertical_rate_fpm` (a positive rate, e.g. 2000 -- not a signed
    vertical speed).

    Pure kinematics: `time_to_descend_min = altitude_ft / vertical_rate_fpm`,
    then `distance_nm = groundspeed_kt * (time_to_descend_min / 60)`.
    E.g. descending from FL300 (30,000 ft) at 2,000 ft/min takes 15
    minutes; at 480 kt groundspeed that is 120 NM of TOD distance.

    Shared by `MockConnector` (which *applies* this profile to a
    "LANDING" aircraft's actual simulated descent) and
    `RouteAwareTrajectoryEngine` (which *predicts* the same profile
    forward for hotspot/complexity scoring during timeline scrubbing) --
    one implementation of "when should this aircraft start descending"
    so the two can never silently disagree, the same reasoning
    `advance_along_route`'s module docstring gives for route-following
    geometry.

    Args:
        altitude_ft: Current altitude above the descent's target
            (treated as 0 ft -- i.e. this is "altitude still to lose").
            Negative values are clamped to 0.
        groundspeed_kt: Current ground speed, knots.
        vertical_rate_fpm: Descent rate, feet per minute (positive).

    Returns:
        0.0 if `groundspeed_kt` or `vertical_rate_fpm` is not positive
        (nothing meaningful to compute -- callers should treat this as
        "no TOD distance, not yet applicable" rather than "TOD is right
        here").
    """
    if groundspeed_kt <= 0 or vertical_rate_fpm <= 0:
        return 0.0
    descent_time_min = max(0.0, altitude_ft) / vertical_rate_fpm
    return groundspeed_kt * (descent_time_min / 60.0)
