"""
Regression tests -- airway spawn/follow (`astra.interface.mock_connector`,
`astra.dashboard.scenario_routes`).

Run with:
    python3 tests/test_interface.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.dashboard.server import create_app
from astra.dashboard.store import CycleStore
from astra.interface.mock_connector import MockConnector
from astra.interface.state_reader import StateReader
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import bearing_deg, haversine_distance_nm
from tests._runner import Runner


def _run_ticks(connector: MockConnector, n: int) -> None:
    """Advance a connected, running MockConnector `n` poll ticks."""
    connector.send_command("OP")
    for _ in range(n):
        connector.poll()


def test_no_route_is_unaffected(r: Runner) -> None:
    """Backward compatibility: omitting `route_waypoints` keeps straight dead reckoning."""
    mc = MockConnector(sim_step_s=10.0)
    mc.connect()
    mc.create_aircraft("AC1", "A320", 47.0, 8.0, 90.0, 35000.0, 420.0)
    _run_ticks(mc, 3)
    ac = mc.latest_snapshot().aircraft["AC1"]
    r.check("heading unchanged without a route", ac.heading_deg == 90.0)
    r.check("moved east (positive lon change)", ac.lon > 8.0)
    r.check("no route stored internally", mc._aircraft["AC1"].route_waypoints is None)


def test_route_following_reaches_each_waypoint(r: Runner) -> None:
    """A 3-point route is flown leg by leg, in order."""
    mc = MockConnector(sim_step_s=5.0)
    mc.connect()
    route = [(47.0, 8.0), (47.2, 8.3), (47.5, 8.6)]
    mc.create_aircraft("AC2", "A320", 46.8, 7.8, 0.0, 35000.0, 420.0, route_waypoints=route)

    initial = mc._aircraft["AC2"]
    expected_heading = bearing_deg(46.8, 7.8, *route[0])
    r.check(
        "initial heading points toward the first waypoint, not the given 0deg",
        abs(initial.heading_deg - expected_heading) < 0.5,
    )

    _run_ticks(mc, 2)
    mid = mc.latest_snapshot().aircraft["AC2"]
    r.check(
        "aircraft has moved toward the first waypoint",
        haversine_distance_nm(mid.lat, mid.lon, *route[0])
        < haversine_distance_nm(46.8, 7.8, *route[0]),
    )
    r.check("route index still on the first leg", mc._aircraft["AC2"].route_index == 0)


def test_route_clears_after_final_waypoint(r: Runner) -> None:
    """Once the final waypoint is passed, the aircraft reverts to straight flight."""
    mc = MockConnector(sim_step_s=60.0)
    mc.connect()
    route = [(47.0, 8.0), (47.2, 8.3)]
    mc.create_aircraft("AC3", "A320", 46.8, 7.8, 0.0, 35000.0, 420.0, route_waypoints=route)
    _run_ticks(mc, 20)  # far more distance than the whole route needs
    r.check("route cleared once flown in full", mc._aircraft["AC3"].route_waypoints is None)
    ac = mc.latest_snapshot().aircraft["AC3"]
    r.check(
        "aircraft continued past the final waypoint (didn't stop dead)",
        haversine_distance_nm(ac.lat, ac.lon, *route[-1]) > 5.0,
    )


def test_multi_leg_single_tick(r: Runner) -> None:
    """A large `dt` that overshoots multiple short legs in one tick still lands correctly."""
    mc = MockConnector(sim_step_s=3600.0)  # one huge tick
    mc.connect()
    route = [(47.00, 8.00), (47.01, 8.01), (47.02, 8.02)]
    mc.create_aircraft("AC4", "A320", 46.99, 7.99, 0.0, 35000.0, 420.0, route_waypoints=route)
    mc.send_command("OP")
    mc.poll()
    r.check("route consumed in full within one oversized tick", mc._aircraft["AC4"].route_waypoints is None)


def test_scenario_airways_endpoint(r: Runner) -> None:
    """`GET /scenario/airways` returns the static airway list, reshaped for the UI."""
    config = ASTRAConfig()
    reader = StateReader.for_mock(config)
    reader.connect()
    app = create_app(CycleStore(), config, reader=reader)
    client = app.test_client()

    resp = client.get("/scenario/airways")
    body = resp.get_json()
    r.check("200 OK", resp.status_code == 200)
    r.check("at least one airway returned", len(body["airways"]) > 0)
    first = body["airways"][0]
    r.check("airway has a designator", isinstance(first["designator"], str) and first["designator"])
    r.check("airway has >= 2 coordinate points", len(first["coordinates"]) >= 2)
    r.check(
        "coordinates are {lat, lon} dicts",
        set(first["coordinates"][0].keys()) == {"lat", "lon"},
    )


def test_scenario_aircraft_spawn_on_airway(r: Runner) -> None:
    """`POST /scenario/aircraft` with `airway_designator` spawns onto and follows it."""
    config = ASTRAConfig()
    reader = StateReader.for_mock(config)
    reader.connect()
    app = create_app(CycleStore(), config, reader=reader)
    client = app.test_client()

    designator = client.get("/scenario/airways").get_json()["airways"][0]["designator"]
    resp = client.post(
        "/scenario/aircraft",
        json={
            "callsign": "AWY01",
            "aircraft_type": "A320",
            "altitude_ft": 35000,
            "speed_kt": 420,
            "airway_designator": designator,
        },
    )
    body = resp.get_json()
    r.check("spawn accepted", resp.status_code == 200 and body["ok"])
    r.check("marked as on_route", body["on_route"] is True)

    state = client.get("/scenario/state").get_json()
    spawned = next(a for a in state["aircraft"] if a["callsign"] == "AWY01")
    r.check("spawned aircraft is flagged on_route", spawned["on_route"] is True)


def test_scenario_aircraft_unknown_airway_rejected(r: Runner) -> None:
    """Spawning onto a non-existent airway designator is a clean 404, not a crash."""
    config = ASTRAConfig()
    reader = StateReader.for_mock(config)
    reader.connect()
    app = create_app(CycleStore(), config, reader=reader)
    client = app.test_client()

    resp = client.post(
        "/scenario/aircraft",
        json={
            "callsign": "AWY02",
            "aircraft_type": "A320",
            "altitude_ft": 35000,
            "speed_kt": 420,
            "airway_designator": "NOT_A_REAL_AIRWAY",
        },
    )
    r.check("unknown airway is rejected with 404", resp.status_code == 404)


def main() -> None:
    r = Runner("Airway spawn/follow (astra.interface.mock_connector, astra.dashboard.scenario_routes)")
    test_no_route_is_unaffected(r)
    test_route_following_reaches_each_waypoint(r)
    test_route_clears_after_final_waypoint(r)
    test_multi_leg_single_tick(r)
    test_scenario_airways_endpoint(r)
    test_scenario_aircraft_spawn_on_airway(r)
    test_scenario_aircraft_unknown_airway_rejected(r)
    r.summary()


if __name__ == "__main__":
    main()
