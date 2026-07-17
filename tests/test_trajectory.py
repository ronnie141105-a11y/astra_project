"""
Trajectory prediction tests -- baseline dead reckoning, shared
route-following geometry, and the new route-aware engine.

This is a new test file (the repository had no `tests/test_trajectory.py`
before); it fills that gap while covering both the pre-existing
`TrajectoryEngine` (previously exercised only indirectly through
downstream modules) and the newly-added route-aware engine, so the two
can be compared with the same helpers a thesis evaluation would use.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._runner import Runner

from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.engine import TrajectoryEngine, predict_constant_velocity
from astra.trajectory.route_engine import RouteAwareTrajectoryEngine
from astra.trajectory.route_following import advance_along_route
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import bearing_deg, haversine_distance_nm, move_position


def _aircraft(callsign, lat, lon, hdg=90.0, alt=35000.0, gs=300.0, vs=0.0, t=0.0):
    return AircraftState(
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=alt,
        ground_speed_kt=gs,
        heading_deg=hdg,
        vertical_speed_fpm=vs,
        aircraft_type="A320",
        timestamp_s=t,
    )


def _snapshot(aircraft, t=0.0):
    return TrafficSnapshot(timestamp_s=t, aircraft={a.callsign: a for a in aircraft})


# ======================================================================
# Baseline TrajectoryEngine (constant-velocity dead reckoning)
# ======================================================================


def test_baseline_straight_line(r: Runner) -> None:
    """Dead reckoning matches move_position() exactly at every horizon."""
    config = ASTRAConfig()
    ac = _aircraft("AC1", 10.0, 106.0, hdg=90.0, gs=300.0)
    engine = TrajectoryEngine(config)
    result = engine.predict(_snapshot([ac]))

    for h in config.prediction_horizons_min:
        predicted = result.at(h).get("AC1")
        expected_lat, expected_lon = move_position(10.0, 106.0, 90.0, 300.0 * h / 60.0)
        r.check_close(f"h={h}min lat matches move_position", predicted.lat, expected_lat, tol=1e-9)
        r.check_close(f"h={h}min lon matches move_position", predicted.lon, expected_lon, tol=1e-9)
        r.check_close(f"h={h}min heading unchanged", predicted.heading_deg, 90.0)
        r.check_close(f"h={h}min altitude unchanged (vs=0)", predicted.altitude_ft, 35000.0)


def test_baseline_vertical_extrapolation(r: Runner) -> None:
    """Altitude changes linearly with vertical_speed_fpm; clamped at 0."""
    config = ASTRAConfig()
    climbing = _aircraft("CLB", 10.0, 106.0, alt=30000.0, vs=1000.0)
    descending = _aircraft("DSC", 10.0, 106.0, alt=2000.0, vs=-2000.0)
    engine = TrajectoryEngine(config)
    result = engine.predict(_snapshot([climbing, descending]))

    r.check_close(
        "climb: +1000 fpm for 10 min = +10000 ft",
        result.at(10).get("CLB").altitude_ft,
        40000.0,
    )
    r.check_close(
        "descent clamps at ground level, never negative",
        result.at(10).get("DSC").altitude_ft,
        0.0,
    )


def test_predict_constant_velocity_matches_engine(r: Runner) -> None:
    """The extracted free function and the engine method agree exactly."""
    ac = _aircraft("AC1", 10.0, 106.0, hdg=45.0, gs=250.0, vs=500.0)
    via_function = predict_constant_velocity(ac, 900.0, 900.0)
    engine = TrajectoryEngine(ASTRAConfig())
    via_method = engine._predict_aircraft(ac, 900.0, 900.0)
    r.check_close("lat matches between function and method", via_function.lat, via_method.lat, tol=1e-12)
    r.check_close("lon matches between function and method", via_function.lon, via_method.lon, tol=1e-12)
    r.check_close(
        "altitude matches between function and method",
        via_function.altitude_ft,
        via_method.altitude_ft,
    )


# ======================================================================
# Shared route-following geometry (astra.trajectory.route_following)
# ======================================================================


def test_advance_along_route_no_route_is_dead_reckoning(r: Runner) -> None:
    """None/empty route degrades to plain constant-heading movement."""
    result_none = advance_along_route(10.0, 106.0, 90.0, None, 50.0)
    result_empty = advance_along_route(10.0, 106.0, 90.0, [], 50.0)
    expected_lat, expected_lon = move_position(10.0, 106.0, 90.0, 50.0)
    r.check_close("None route: lat matches dead reckoning", result_none.lat, expected_lat, tol=1e-9)
    r.check_close("None route: lon matches dead reckoning", result_none.lon, expected_lon, tol=1e-9)
    r.check("None route: heading unchanged", result_none.heading_deg == 90.0)
    r.check("None route: not marked completed", result_none.route_completed is False)
    r.check_close("Empty route: lat matches dead reckoning", result_empty.lat, expected_lat, tol=1e-9)


def test_advance_along_route_single_leg(r: Runner) -> None:
    """Partway along one leg: heads straight for the waypoint, doesn't reach it."""
    start_lat, start_lon = 10.0, 106.0
    wp = (10.0, 106.5)  # due east
    full_leg_nm = haversine_distance_nm(start_lat, start_lon, *wp)
    result = advance_along_route(start_lat, start_lon, 0.0, [wp], full_leg_nm / 2.0)

    expected_heading = bearing_deg(start_lat, start_lon, *wp)
    r.check_close("heading updates to bearing toward the waypoint", result.heading_deg, expected_heading, tol=1e-6)
    r.check("waypoint not yet reached: still in remaining_waypoints", result.remaining_waypoints == [wp])
    r.check("route not completed", result.route_completed is False)
    dist_to_wp = haversine_distance_nm(result.lat, result.lon, *wp)
    r.check_close("halfway: roughly half the leg remains", dist_to_wp, full_leg_nm / 2.0, tol=1e-3)


def test_advance_along_route_reaches_and_turns(r: Runner) -> None:
    """Overshooting one leg consumes it and turns toward the next -- the
    exact scenario dead reckoning gets structurally wrong."""
    start_lat, start_lon = 10.0, 106.0
    wp1 = (10.0, 106.5)  # due east of start
    wp2 = (10.5, 106.5)  # due north of wp1 -- a 90-degree turn
    leg1_nm = haversine_distance_nm(start_lat, start_lon, *wp1)

    # Travel just past wp1, onto the leg toward wp2.
    travel_nm = leg1_nm + 5.0
    result = advance_along_route(start_lat, start_lon, 0.0, [wp1, wp2], travel_nm)

    r.check("wp1 consumed, only wp2 remains", result.remaining_waypoints == [wp2])
    r.check("route not completed (wp2 still ahead)", result.route_completed is False)
    expected_heading_leg2 = bearing_deg(*wp1, *wp2)
    r.check_close(
        "heading after the turn points toward wp2, not the original heading",
        result.heading_deg,
        expected_heading_leg2,
        tol=1e-6,
    )
    r.check(
        "heading actually changed (this is what dead reckoning misses)",
        abs(result.heading_deg - 90.0) > 45.0,
    )


def test_advance_along_route_completes_and_continues_straight(r: Runner) -> None:
    """Past the last waypoint: route clears, flight continues on last heading."""
    start_lat, start_lon = 10.0, 106.0
    wp = (10.0, 106.2)
    leg_nm = haversine_distance_nm(start_lat, start_lon, *wp)
    overshoot_nm = 20.0
    result = advance_along_route(start_lat, start_lon, 0.0, [wp], leg_nm + overshoot_nm)

    r.check("route fully consumed", result.remaining_waypoints == [])
    r.check("route_completed is True", result.route_completed is True)
    expected_lat, expected_lon = move_position(*wp, result.heading_deg, overshoot_nm)
    r.check_close("overshoot flown straight past the last waypoint", result.lat, expected_lat, tol=1e-6)
    r.check_close("overshoot longitude matches", result.lon, expected_lon, tol=1e-6)


def test_advance_along_route_multi_leg_single_call(r: Runner) -> None:
    """One large distance_nm can cross several legs in a single call --
    needed for long prediction horizons, not just one sim tick."""
    start = (10.0, 106.0)
    wp1 = (10.0, 106.1)
    wp2 = (10.1, 106.1)
    wp3 = (10.1, 106.2)
    total_route_nm = (
        haversine_distance_nm(*start, *wp1)
        + haversine_distance_nm(*wp1, *wp2)
        + haversine_distance_nm(*wp2, *wp3)
    )
    result = advance_along_route(*start, 0.0, [wp1, wp2, wp3], total_route_nm)
    r.check("all three legs consumed in one call", result.route_completed is True)
    r.check_close("final position lat == wp3", result.lat, wp3[0], tol=1e-6)
    r.check_close("final position lon == wp3", result.lon, wp3[1], tol=1e-6)


# ======================================================================
# RouteAwareTrajectoryEngine
# ======================================================================


def test_route_aware_matches_baseline_with_no_route(r: Runner) -> None:
    """Aircraft with no known route: identical output to TrajectoryEngine."""
    config = ASTRAConfig()
    ac = _aircraft("NOROUTE", 10.0, 106.0, hdg=90.0, gs=280.0, vs=300.0)
    snapshot = _snapshot([ac])

    baseline = TrajectoryEngine(config).predict(snapshot)
    route_aware = RouteAwareTrajectoryEngine(config, route_provider=lambda cs: None).predict(snapshot)

    for h in config.prediction_horizons_min:
        b = baseline.at(h).get("NOROUTE")
        ra = route_aware.at(h).get("NOROUTE")
        r.check_close(f"h={h}min lat identical to baseline", ra.lat, b.lat, tol=1e-12)
        r.check_close(f"h={h}min lon identical to baseline", ra.lon, b.lon, tol=1e-12)
        r.check_close(f"h={h}min altitude identical to baseline", ra.altitude_ft, b.altitude_ft, tol=1e-9)


def test_route_aware_predicts_the_turn_baseline_misses(r: Runner) -> None:
    """The central claim: an aircraft about to turn is predicted turning
    by the route-aware engine, and predicted flying straight through the
    turn by the baseline -- diverging further apart at later horizons."""
    config = ASTRAConfig()
    start_lat, start_lon = 10.0, 106.0
    wp1 = move_position(start_lat, start_lon, 90.0, 20.0)  # 20 NM east
    wp2 = move_position(*wp1, 0.0, 40.0)  # then 40 NM due north -- sharp turn
    route = [wp1, wp2]
    gs = 300.0  # kt -> 5 NM/min, reaches wp1 at t=4min, well before h=5min horizon

    ac = _aircraft("TURN1", start_lat, start_lon, hdg=90.0, gs=gs)
    snapshot = _snapshot([ac])

    baseline = TrajectoryEngine(config).predict(snapshot)
    route_aware = RouteAwareTrajectoryEngine(config, route_provider=lambda cs: route).predict(snapshot)

    b15 = baseline.at(15).get("TURN1")
    ra15 = route_aware.at(15).get("TURN1")

    # Baseline (dead reckoning) never turns -- it should be very close to
    # a pure 15-minute straight-line projection at the original heading.
    expected_straight_lat, expected_straight_lon = move_position(start_lat, start_lon, 90.0, gs * 15.0 / 60.0)
    r.check_close(
        "baseline at h=15min matches pure straight-line dead reckoning",
        b15.lat,
        expected_straight_lat,
        tol=1e-6,
    )

    # Route-aware should have turned onto leg 2 by h=15min (reaches wp1
    # at t=4min with 11 minutes of leg-2 travel remaining) and therefore
    # be well north of where the baseline predicts.
    r.check(
        "route-aware prediction has turned north (higher latitude than baseline)",
        ra15.lat > b15.lat + 0.1,
    )
    r.check_close("route-aware heading after the turn is ~0 (north)", ra15.heading_deg, 0.0, tol=1.0)

    divergence_nm = haversine_distance_nm(b15.lat, b15.lon, ra15.lat, ra15.lon)
    r.check(
        "the two predictions diverge substantially by h=15min (>20 NM)",
        divergence_nm > 20.0,
    )


def test_route_aware_falls_back_per_aircraft(r: Runner) -> None:
    """Mixed traffic: one aircraft on a route, one not -- each engine
    path applies independently within a single predict() call."""
    config = ASTRAConfig()
    wp = move_position(10.0, 106.0, 90.0, 30.0)
    on_route = _aircraft("ONROUTE", 10.0, 106.0, hdg=90.0, gs=240.0)
    no_route = _aircraft("NOROUTE", 20.0, 110.0, hdg=180.0, gs=240.0)
    snapshot = _snapshot([on_route, no_route])

    def provider(callsign: str):
        return [wp] if callsign == "ONROUTE" else None

    engine = RouteAwareTrajectoryEngine(config, route_provider=provider)
    result = engine.predict(snapshot)

    baseline_no_route = predict_constant_velocity(no_route, 30 * 60.0, 30 * 60.0)
    predicted_no_route = result.at(30).get("NOROUTE")
    r.check_close(
        "aircraft without a route still matches dead reckoning at h=30min",
        predicted_no_route.lat,
        baseline_no_route.lat,
        tol=1e-9,
    )

    # 30 NM leg at 240 kt is reached at t=7.5min; by h=30min the aircraft
    # has long since passed the single waypoint and continued straight
    # (dead reckoning) on the post-waypoint heading -- verify it's past
    # the waypoint, not artificially stuck there.
    predicted_on_route = result.at(30).get("ONROUTE")
    dist_from_start_nm = haversine_distance_nm(10.0, 106.0, predicted_on_route.lat, predicted_on_route.lon)
    r.check_close(
        "aircraft on a route travels the full 120 NM (240kt x 30min), passing its one waypoint",
        dist_from_start_nm,
        240.0 * 30.0 / 60.0,
        tol=1e-3,
    )


if __name__ == "__main__":
    r = Runner("Trajectory prediction — baseline dead reckoning vs route-aware engine")
    test_baseline_straight_line(r)
    test_baseline_vertical_extrapolation(r)
    test_predict_constant_velocity_matches_engine(r)
    test_advance_along_route_no_route_is_dead_reckoning(r)
    test_advance_along_route_single_leg(r)
    test_advance_along_route_reaches_and_turns(r)
    test_advance_along_route_completes_and_continues_straight(r)
    test_advance_along_route_multi_leg_single_call(r)
    test_route_aware_matches_baseline_with_no_route(r)
    test_route_aware_predicts_the_turn_baseline_misses(r)
    test_route_aware_falls_back_per_aircraft(r)
    r.summary()
