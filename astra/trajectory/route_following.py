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
) -> RouteAdvanceResult:
    """Advance one aircraft `distance_nm` along its remaining route.

    Heads straight for ``route_waypoints[0]``, consuming one or more legs
    if ``distance_nm`` overshoots the current leg (so a single call can
    correctly cover a long prediction horizon that spans several
    waypoints, not just one simulation tick). Once the final waypoint is
    passed, any leftover distance is flown straight on the last heading
    flown -- the aircraft continues past the end of its filed route
    rather than stopping dead, matching real-world behaviour when a
    cleared route runs out before the prediction horizon does.

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
    if remaining_distance > 0:
        # Route flown out with distance still to travel -- continue
        # straight on the last heading flown.
        current_lat, current_lon = move_position(
            current_lat, current_lon, current_heading, remaining_distance
        )

    return RouteAdvanceResult(
        current_lat, current_lon, current_heading, remaining_waypoints, route_completed
    )
