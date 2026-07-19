"""
Two-phase "vector, then rejoin route" kinematics for resolution
candidate evaluation (extends Milestone 7).

A plain ``HEADING`` candidate (see ``astra.resolution.candidates``)
treats the new heading as held indefinitely -- fine for an aircraft
with no filed route, but unrealistic for one that does: in practice a
controller vectors an aircraft briefly off its route and then turns it
back (direct to its next waypoint, or a fresh clearance), not off its
route forever. ``TrajectoryEngine`` (constant-velocity dead reckoning)
and ``RouteAwareTrajectoryEngine`` (route-following) each only model
one of those two phases, not a transition between them.

``predict_vector_and_rejoin`` fills that gap: for a given duration, fly
dead-reckoning at the new heading (Phase 1 -- the vector); for whatever
horizon remains after that, fly the aircraft's *original* known route
from wherever the vector left it (Phase 2 -- the rejoin), reusing
``astra.trajectory.route_following.advance_along_route`` exactly as
``RouteAwareTrajectoryEngine`` does. This is the same "turn toward the
next known waypoint" geometry a real direct-to clearance would produce
-- ``MockConnector`` has no explicit ``DCT`` stack command (see
``astra.resolution.candidates`` module docstring), but this is the
correct predicted *outcome* of one, and matches exactly what
``MockConnector`` itself would do if its route_waypoints were restored
after a temporary vector (see ``scenarios/arrival_sequencing_demo.py``
for the real-connector-side counterpart of this same idea).

If the whole requested duration falls inside the vector phase (a short
prediction horizon evaluated before the vector would have finished),
this degrades to plain dead reckoning at the vectored heading -- there
is nothing to rejoin yet. If the aircraft has no known route at all,
callers should not be using this function in the first place (see
``heading candidate`` generation in ``astra.resolution.candidates``,
which only offers a vector-and-rejoin candidate when a route is known);
this module does not silently fall back, to keep that precondition
explicit and testable.
"""

from typing import List, Tuple

from astra.interface.traffic_state import AircraftState
from astra.trajectory.engine import predict_constant_velocity
from astra.trajectory.route_following import advance_along_route
from astra.utils.geodesy import move_position


def predict_vector_and_rejoin(
    ac: AircraftState,
    route: List[Tuple[float, float]],
    vector_heading_deg: float,
    vector_duration_s: float,
    dt_s: float,
    predicted_time_s: float,
) -> AircraftState:
    """Predict one aircraft's state after a bounded-duration vector, then rejoin.

    Args:
        ac: Current observed state (before the vector begins). Ground
            speed and vertical speed are held constant through both
            phases, matching every other trajectory predictor in this
            project.
        route: The aircraft's real, currently-known remaining
            waypoints (``[(lat, lon), ...]``, first-to-fly-to first) --
            what it would resume once the vector ends. Must be
            non-empty; see module docstring.
        vector_heading_deg: The heading flown during Phase 1 (typically
            ``ac.heading_deg + resolution_heading_step_deg``, i.e. the
            same delta a plain HEADING candidate would apply).
        vector_duration_s: Length of Phase 1, in seconds. If this is
            >= ``dt_s``, the whole prediction falls inside the vector
            and Phase 2 never runs.
        dt_s: Total prediction horizon from now, in seconds (matches
            ``TrajectoryEngine``'s own ``dt_s`` convention).
        predicted_time_s: Absolute simulation timestamp of the
            predicted state (``ac.timestamp_s + dt_s``, computed by the
            caller once and passed through for consistency with the
            rest of the pipeline).

    Returns:
        A new ``AircraftState`` at ``predicted_time_s`` -- lat/lon/
        heading from the two-phase kinematic above; altitude and
        vertical speed reuse ``predict_constant_velocity``'s linear
        extrapolation unchanged, exactly as
        ``RouteAwareTrajectoryEngine._predict_along_route`` does for a
        normal route-following prediction.
    """
    phase1_s = max(0.0, min(dt_s, vector_duration_s))
    phase1_distance_nm = ac.ground_speed_kt * (phase1_s / 3600.0)
    lat1, lon1 = move_position(ac.lat, ac.lon, vector_heading_deg, phase1_distance_nm)

    remaining_s = dt_s - phase1_s
    if remaining_s <= 0.0:
        # Horizon falls entirely inside the vector: nothing to rejoin yet.
        final_lat, final_lon, final_heading = lat1, lon1, vector_heading_deg
    else:
        phase2_distance_nm = ac.ground_speed_kt * (remaining_s / 3600.0)
        rejoin = advance_along_route(lat1, lon1, vector_heading_deg, route, phase2_distance_nm)
        final_lat, final_lon, final_heading = rejoin.lat, rejoin.lon, rejoin.heading_deg

    # Altitude/vertical-speed extrapolation is identical for every
    # trajectory predictor in this project -- reuse it rather than
    # duplicating the linear vertical model here.
    dead_reckoned = predict_constant_velocity(ac, dt_s, predicted_time_s)

    return AircraftState(
        callsign=ac.callsign,
        lat=final_lat,
        lon=final_lon,
        altitude_ft=dead_reckoned.altitude_ft,
        ground_speed_kt=ac.ground_speed_kt,
        heading_deg=final_heading,
        vertical_speed_fpm=ac.vertical_speed_fpm,
        aircraft_type=ac.aircraft_type,
        timestamp_s=predicted_time_s,
    )
