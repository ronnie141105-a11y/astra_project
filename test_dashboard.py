"""
Regression tests — Milestone 8 (dashboard / HMI, `astra.dashboard`).

Run with:
    python3 tests/test_dashboard.py

No BlueSky process and no third-party test framework required (Flask's
own `app.test_client()` is used for the integration checks -- it does
not start a real network server, matching the "no pytest" constraint
the rest of the suite follows). Exits non-zero if any check fails (see
`tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.models import ComplexityRegion
from astra.dashboard import serializers
from astra.dashboard.models import DashboardSnapshot
from astra.dashboard.server import create_app
from astra.dashboard.store import CycleStore
from astra.hotspot.models import Cluster
from astra.interface.state_reader import StateReader
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.pipeline import CycleResult, Pipeline
from astra.resolution.models import ResolutionCandidate, ResolutionSet
from astra.tracking.models import FourDArhac
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.utils.config import ASTRAConfig
from tests._runner import Runner


# ----------------------------------------------------------------------
# Hand-built fixtures (mirrors tests/test_forecast.py / test_resolution.py)
# ----------------------------------------------------------------------


def _aircraft(callsign, lat, lon, hdg=90.0, alt=35000.0, gs=15.0, actype="A320", t=0.0):
    """Build a plain `AircraftState` without needing BlueSky/MockConnector."""
    return AircraftState(
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=alt,
        ground_speed_kt=gs,
        heading_deg=hdg,
        vertical_speed_fpm=0.0,
        aircraft_type=actype,
        timestamp_s=t,
    )


def _snapshot(aircraft, t=0.0):
    """Build a `TrafficSnapshot` from a list of `AircraftState`."""
    return TrafficSnapshot(timestamp_s=t, aircraft={ac.callsign: ac for ac in aircraft})


def _cluster(callsigns, lat=47.0, lon=8.0, extent_nm=5.0, horizon_min=0, valid_at_s=0.0, label=0):
    """Build a hand-controlled `Cluster`."""
    return Cluster(
        cluster_id=f"{'observed' if horizon_min == 0 else 'predicted'}:{horizon_min}:{label}",
        source="observed" if horizon_min == 0 else "predicted",
        horizon_min=horizon_min,
        valid_at_s=valid_at_s,
        member_callsigns=frozenset(callsigns),
        centroid_lat=lat,
        centroid_lon=lon,
        centroid_alt_ft=35000.0,
        horizontal_extent_nm=extent_nm,
    )


def _region(callsigns, score, valid_at_s=0.0, horizon_min=0, components=None, lat=47.0, lon=8.0):
    """Build a hand-controlled `ComplexityRegion`."""
    cluster = _cluster(callsigns, lat=lat, lon=lon, horizon_min=horizon_min, valid_at_s=valid_at_s)
    return ComplexityRegion(
        cluster=cluster,
        complexity_score=score,
        components=components or {"density_ac_per_nm2": 0.1},
        computed_at_s=valid_at_s,
    )


def _track(arhac_id="T1", status="GROWING", history_scores=(40.0, 55.0), **overrides):
    """Build a `FourDArhac` with a short observed history, ready to serialize."""
    track_entries = [
        _region(["A1", "A2"], score, valid_at_s=float(i * 60)) for i, score in enumerate(history_scores)
    ]
    kwargs = dict(
        arhac_id=arhac_id,
        status=status,
        track=track_entries,
        member_aircraft=frozenset(["A1", "A2"]),
        confidence=0.8,
        peak_complexity=max(history_scores) if history_scores else 0.0,
        peak_time_s=0.0,
        priority=1,
        forecast_urgency_rank=1,
        predicted_onset_s=120.0,
        predicted_dissipation_s=600.0,
    )
    kwargs.update(overrides)
    return FourDArhac(**kwargs)


def _candidate(score=0.3, clearance_type="SPEED", target="A1", delta=20.0, before=60.0, after=45.0):
    """Build a hand-controlled `ResolutionCandidate`."""
    return ResolutionCandidate(
        clearance_type=clearance_type,
        target_callsign=target,
        delta_value=delta,
        complexity_before=before,
        complexity_after=after,
        complexity_delta_norm=0.25,
        deviation_cost_norm=0.1,
        fuel_cost_proxy_norm=0.0,
        resolution_score=score,
    )


def _prediction(source_time_s=0.0, horizons=(5, 10), aircraft_by_horizon=None):
    """Build a hand-controlled `PredictionResult` with one aircraft moving east."""
    aircraft_by_horizon = aircraft_by_horizon or {}
    snapshots = {}
    for horizon_min in horizons:
        aircraft = aircraft_by_horizon.get(
            horizon_min, [_aircraft("A1", 47.0 + 0.01 * horizon_min, 8.0 + 0.01 * horizon_min)]
        )
        snapshots[horizon_min] = PredictedSnapshot(
            horizon_min=horizon_min,
            source_time_s=source_time_s,
            predicted_time_s=source_time_s + horizon_min * 60,
            aircraft={ac.callsign: ac for ac in aircraft},
        )
    return PredictionResult(
        source_time_s=source_time_s,
        aircraft_count=len(next(iter(snapshots.values())).aircraft) if snapshots else 0,
        horizons_min=tuple(sorted(horizons)),
        snapshots=snapshots,
    )


# ----------------------------------------------------------------------
# astra.dashboard.serializers — pure function unit tests
# ----------------------------------------------------------------------


def test_serialize_aircraft(r: Runner) -> None:
    """One `AircraftState` serializes to a flat, JSON-safe dict."""
    ac = _aircraft("KL204", 52.3, 4.8, hdg=90.0, alt=30000.0, gs=250.0)
    out = serializers.serialize_aircraft(ac)
    r.check("callsign present", out["callsign"] == "KL204")
    r.check_close("lat round-trips", out["lat"], 52.3)
    r.check_close("altitude_ft round-trips", out["altitude_ft"], 30000.0)
    r.check("no AircraftState leaks through (plain dict)", isinstance(out, dict))


def test_serialize_snapshot(r: Runner) -> None:
    """A `TrafficSnapshot` serializes to its timestamp plus an aircraft list."""
    snap = _snapshot([_aircraft("A1", 47.0, 8.0), _aircraft("A2", 47.1, 8.1)], t=42.0)
    out = serializers.serialize_snapshot(snap)
    r.check_close("timestamp_s round-trips", out["timestamp_s"], 42.0)
    r.check("both aircraft present", len(out["aircraft"]) == 2)
    r.check(
        "callsigns present in serialized aircraft",
        {a["callsign"] for a in out["aircraft"]} == {"A1", "A2"},
    )


def test_serialize_prediction_groups_by_callsign(r: Runner) -> None:
    """`serialize_prediction` reshapes {horizon: snapshot} into {callsign: [points]}."""
    prediction = _prediction(
        horizons=(5, 10),
        aircraft_by_horizon={
            5: [_aircraft("A1", 47.05, 8.05)],
            10: [_aircraft("A1", 47.10, 8.10)],
        },
    )
    out = serializers.serialize_prediction(prediction)
    r.check("one path for A1", list(out.keys()) == ["A1"])
    r.check("two points on A1's path", len(out["A1"]) == 2)
    r.check(
        "points ordered by ascending horizon_min",
        [p["horizon_min"] for p in out["A1"]] == [5, 10],
    )
    r.check_close("second point's lat matches horizon-10 snapshot", out["A1"][1]["lat"], 47.10)


def test_serialize_prediction_missing_aircraft_at_horizon(r: Runner) -> None:
    """An aircraft absent from a horizon simply has fewer points, not an error."""
    prediction = _prediction(
        horizons=(5, 10),
        aircraft_by_horizon={5: [_aircraft("A1", 47.05, 8.05)], 10: []},
    )
    out = serializers.serialize_prediction(prediction)
    r.check("A1 has exactly one point (only horizon 5)", len(out["A1"]) == 1)


def test_serialize_cluster_and_region(r: Runner) -> None:
    """`serialize_region` nests a serialized `Cluster` plus score/components."""
    region = _region(["A1", "A2"], score=72.5, components={"mtca_count": 2.0})
    out = serializers.serialize_region(region)
    r.check_close("complexity_score round-trips", out["complexity_score"], 72.5)
    r.check("member_callsigns present and sorted", out["cluster"]["member_callsigns"] == ["A1", "A2"])
    r.check_close("component value round-trips", out["components"]["mtca_count"], 2.0)


def test_serialize_regions_by_horizon(r: Runner) -> None:
    """`{horizon: [region]}` serializes key-for-key, value-for-value."""
    regions_by_horizon = {
        0: [_region(["A1"], 30.0)],
        5: [_region(["A1"], 35.0, horizon_min=5)],
    }
    out = serializers.serialize_regions_by_horizon(regions_by_horizon)
    r.check("both horizon keys present", set(out.keys()) == {0, 5})
    r.check("horizon 0 has one region", len(out[0]) == 1)


def test_serialize_track_includes_history_and_centroid(r: Runner) -> None:
    """A track's `history` mirrors `track.track`; centroid comes from the latest entry."""
    track = _track(history_scores=(30.0, 40.0, 55.0))
    out = serializers.serialize_track(track)
    r.check("history has 3 points", len(out["history"]) == 3)
    r.check_close("history is oldest-first", out["history"][0]["complexity_score"], 30.0)
    r.check_close("current score is the latest entry", out["current_complexity_score"], 55.0)
    r.check("centroid present", out["centroid"] is not None)
    r.check("members sorted", out["member_aircraft"] == ["A1", "A2"])
    r.check_close("predicted_onset_s round-trips", out["predicted_onset_s"], 120.0)


def test_serialize_track_with_no_history_has_none_centroid(r: Runner) -> None:
    """A brand-new track with an empty `track` list has no centroid/current score."""
    track = _track(history_scores=(), status="CANDIDATE")
    out = serializers.serialize_track(track)
    r.check("history is empty", out["history"] == [])
    r.check("centroid is None", out["centroid"] is None)
    r.check("current_complexity_score is None", out["current_complexity_score"] is None)


def test_serialize_resolution_candidate(r: Runner) -> None:
    """One `ResolutionCandidate` serializes every scored field."""
    candidate = _candidate(score=0.42, clearance_type="HEADING", target="A2", delta=-15.0)
    out = serializers.serialize_resolution_candidate(candidate)
    r.check("clearance_type round-trips", out["clearance_type"] == "HEADING")
    r.check_close("resolution_score round-trips", out["resolution_score"], 0.42)
    r.check_close("delta_value round-trips (signed)", out["delta_value"], -15.0)


def test_serialize_resolution_set_caps_candidates(r: Runner) -> None:
    """OQ-3(B): the ranked list is shown, capped at `max_candidates`."""
    track = _track()
    candidates = [_candidate(score=s) for s in (0.9, 0.5, 0.2, -0.1)]
    rs = ResolutionSet(track=track, candidates=candidates, evaluated_horizon_min=5)
    out = serializers.serialize_resolution_set(rs, max_candidates=2)
    r.check("capped to 2 candidates even though 4 were ranked", len(out["candidates"]) == 2)
    r.check_close("best candidate kept first", out["candidates"][0]["resolution_score"], 0.9)
    r.check("arhac_id present at top level (not nested track dict)", out["arhac_id"] == track.arhac_id)


def test_serialize_resolution_set_empty_candidates(r: Runner) -> None:
    """A track with zero candidates serializes to an empty list, not an error."""
    track = _track()
    rs = ResolutionSet(track=track, candidates=[], evaluated_horizon_min=5)
    out = serializers.serialize_resolution_set(rs, max_candidates=3)
    r.check("empty candidates list", out["candidates"] == [])


def test_serialize_cycle_result_shape(r: Runner) -> None:
    """A hand-built `CycleResult` serializes to every top-level key the frontend expects."""
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)], t=10.0)
    prediction = _prediction(source_time_s=10.0, horizons=(5,))
    regions_by_horizon = {0: [_region(["A1"], 40.0, valid_at_s=10.0)]}
    track = _track()
    resolution_sets = [ResolutionSet(track=track, candidates=[_candidate()], evaluated_horizon_min=5)]
    result = CycleResult(
        snapshot=snapshot,
        prediction=prediction,
        regions_by_horizon=regions_by_horizon,
        tracks=[track],
        resolution_sets=resolution_sets,
    )
    out = serializers.serialize_cycle_result(result, ASTRAConfig())
    r.check(
        "all top-level keys present",
        set(out.keys())
        == {"snapshot", "prediction", "regions_by_horizon", "tracks", "resolution_sets"},
    )
    r.check("one track serialized", len(out["tracks"]) == 1)
    r.check("one resolution set serialized", len(out["resolution_sets"]) == 1)
    r.check("prediction carries horizons_min", out["prediction"]["horizons_min"] == [5])


def test_serialize_dashboard_snapshot_empty(r: Runner) -> None:
    """Before any cycle has run, the payload reports has_data=False and cycle=None."""
    out = serializers.serialize_dashboard_snapshot(DashboardSnapshot.empty(), ASTRAConfig())
    r.check("has_data is False", out["has_data"] is False)
    r.check("cycle is None", out["cycle"] is None)
    r.check("cycle_count is 0", out["cycle_count"] == 0)


def test_serialize_dashboard_snapshot_with_data(r: Runner) -> None:
    """Once a cycle has run, has_data flips True and cycle nests the full payload."""
    snapshot = _snapshot([_aircraft("A1", 47.0, 8.0)], t=5.0)
    result = CycleResult(
        snapshot=snapshot,
        prediction=_prediction(source_time_s=5.0, horizons=(5,)),
        regions_by_horizon={0: []},
        tracks=[],
        resolution_sets=[],
    )
    dash_snapshot = DashboardSnapshot(cycle_result=result, cycle_count=3, updated_at_s=5.0)
    out = serializers.serialize_dashboard_snapshot(dash_snapshot, ASTRAConfig())
    r.check("has_data is True", out["has_data"] is True)
    r.check("cycle_count round-trips", out["cycle_count"] == 3)
    r.check_close("updated_at_s round-trips", out["updated_at_s"], 5.0)
    r.check("poll_interval_s comes from config, not the cycle", out["poll_interval_s"] == ASTRAConfig().poll_interval_s)


# ----------------------------------------------------------------------
# astra.dashboard.store.CycleStore
# ----------------------------------------------------------------------


def test_cycle_store_starts_empty(r: Runner) -> None:
    """A fresh `CycleStore` reports no cycle yet."""
    store = CycleStore()
    snap = store.snapshot()
    r.check("no cycle_result yet", snap.cycle_result is None)
    r.check("cycle_count starts at 0", snap.cycle_count == 0)


def test_cycle_store_update_increments_count(r: Runner) -> None:
    """Each `update()` call increments cycle_count and replaces cycle_result."""
    store = CycleStore()
    snapshot1 = _snapshot([_aircraft("A1", 47.0, 8.0)], t=1.0)
    result1 = CycleResult(
        snapshot=snapshot1,
        prediction=_prediction(source_time_s=1.0, horizons=(5,)),
        regions_by_horizon={0: []},
        tracks=[],
        resolution_sets=[],
    )
    store.update(result1)
    r.check("cycle_count is 1 after first update", store.snapshot().cycle_count == 1)
    r.check_close("updated_at_s matches snapshot timestamp", store.snapshot().updated_at_s, 1.0)

    snapshot2 = _snapshot([_aircraft("A1", 47.0, 8.0)], t=2.0)
    result2 = CycleResult(
        snapshot=snapshot2,
        prediction=_prediction(source_time_s=2.0, horizons=(5,)),
        regions_by_horizon={0: []},
        tracks=[],
        resolution_sets=[],
    )
    store.update(result2)
    r.check("cycle_count is 2 after second update", store.snapshot().cycle_count == 2)
    r.check_close(
        "latest snapshot replaces the previous one", store.snapshot().updated_at_s, 2.0
    )


# ----------------------------------------------------------------------
# astra.pipeline.Pipeline — CycleResult now carries PredictionResult
# ----------------------------------------------------------------------


def test_pipeline_cycle_result_carries_prediction(r: Runner) -> None:
    """`Pipeline.run_cycle()` returns a `CycleResult` whose `prediction` matches the snapshot."""
    config = ASTRAConfig()
    reader = StateReader.for_mock(config, sim_step_s=30.0)
    reader.connect()
    reader.create_aircraft("A1", "A320", 47.0, 8.0, 90.0, 35000.0, 15.0)
    reader.send_command("OP")
    pipeline = Pipeline(config)

    snapshot = reader.poll() or reader.current()
    result = pipeline.run_cycle(snapshot)

    r.check("prediction is a PredictionResult", isinstance(result.prediction, PredictionResult))
    r.check_close(
        "prediction.source_time_s matches the cycle's snapshot",
        result.prediction.source_time_s,
        snapshot.timestamp_s,
    )
    r.check(
        "regions_by_horizon has an entry for every predicted horizon plus 0",
        set(result.regions_by_horizon.keys()) == {0} | set(result.prediction.horizon_list()),
    )


# ----------------------------------------------------------------------
# astra.dashboard.server / routes — Flask integration (via test_client)
# ----------------------------------------------------------------------


def test_flask_state_endpoint_before_any_cycle(r: Runner) -> None:
    """`/state` returns valid JSON with has_data=False before main.py's loop has run once."""
    config = ASTRAConfig()
    store = CycleStore()
    app = create_app(store, config)
    client = app.test_client()

    response = client.get("/state")
    r.check("status 200", response.status_code == 200)
    payload = response.get_json()
    r.check("has_data is False", payload["has_data"] is False)
    r.check("cycle is None", payload["cycle"] is None)


def test_flask_state_endpoint_after_cycles(r: Runner) -> None:
    """`/state` reflects the latest `CycleStore.update()` call, end to end through Pipeline."""
    config = ASTRAConfig()
    reader = StateReader.for_mock(config, sim_step_s=30.0)
    reader.connect()
    # Same converging geometry as demo_resolution.py: observed complexity
    # below onset, 5-minute predicted horizon above it -- guarantees a
    # forecast urgency rank and at least one ranked resolution candidate.
    reader.create_aircraft("AC1", "A320", 47.000, 7.880, 90.0, 35000.0, 15.0)
    reader.create_aircraft("AC2", "A320", 47.000, 8.120, 270.0, 35000.0, 15.0)
    reader.create_aircraft("AC3", "A320", 47.090, 8.000, 180.0, 34000.0, 8.0)
    reader.send_command("OP")

    pipeline = Pipeline(config)
    store = CycleStore()
    for _ in range(3):
        snapshot = reader.poll()
        if snapshot is not None:
            store.update(pipeline.run_cycle(snapshot))

    app = create_app(store, config)
    client = app.test_client()
    response = client.get("/state")
    r.check("status 200", response.status_code == 200)
    payload = response.get_json()
    r.check("has_data is True after 3 cycles", payload["has_data"] is True)
    r.check("cycle_count is 3", payload["cycle_count"] == 3)
    r.check("at least one open track", len(payload["cycle"]["tracks"]) >= 1)
    r.check(
        "resolution candidates capped at dashboard_max_resolution_candidates_shown",
        all(
            len(rs["candidates"]) <= config.dashboard_max_resolution_candidates_shown
            for rs in payload["cycle"]["resolution_sets"]
        ),
    )


def test_flask_index_serves_html_shell(r: Runner) -> None:
    """`/` renders the HMI page shell and injects the configured poll interval."""
    config = ASTRAConfig(poll_interval_s=2.5)
    store = CycleStore()
    app = create_app(store, config)
    client = app.test_client()

    response = client.get("/")
    r.check("status 200", response.status_code == 200)
    body = response.get_data(as_text=True)
    r.check("page mentions ASTRA", "ASTRA" in body)
    r.check("configured poll interval is injected, not hard-coded", "2.5" in body)


# ----------------------------------------------------------------------
# ASTRAConfig — Phase 8 field validation
# ----------------------------------------------------------------------


def test_config_dashboard_defaults(r: Runner) -> None:
    """Phase 8 fields have the defaults documented in the design review."""
    config = ASTRAConfig()
    r.check("dashboard_host default", config.dashboard_host == "127.0.0.1")
    r.check("dashboard_port default", config.dashboard_port == 8050)
    r.check("dashboard_max_resolution_candidates_shown default", config.dashboard_max_resolution_candidates_shown == 3)


def test_config_dashboard_validation(r: Runner) -> None:
    """Out-of-range Phase 8 fields raise ValueError, like every other phase's fields."""
    r.check_raises(
        "dashboard_port <= 0 raises ValueError",
        lambda: ASTRAConfig(dashboard_port=0),
        ValueError,
    )
    r.check_raises(
        "dashboard_port > 65535 raises ValueError",
        lambda: ASTRAConfig(dashboard_port=70000),
        ValueError,
    )
    r.check_raises(
        "dashboard_max_resolution_candidates_shown < 1 raises ValueError",
        lambda: ASTRAConfig(dashboard_max_resolution_candidates_shown=0),
        ValueError,
    )


def main() -> None:
    r = Runner("Milestone 8 — Dashboard / HMI (astra.dashboard)")
    test_serialize_aircraft(r)
    test_serialize_snapshot(r)
    test_serialize_prediction_groups_by_callsign(r)
    test_serialize_prediction_missing_aircraft_at_horizon(r)
    test_serialize_cluster_and_region(r)
    test_serialize_regions_by_horizon(r)
    test_serialize_track_includes_history_and_centroid(r)
    test_serialize_track_with_no_history_has_none_centroid(r)
    test_serialize_resolution_candidate(r)
    test_serialize_resolution_set_caps_candidates(r)
    test_serialize_resolution_set_empty_candidates(r)
    test_serialize_cycle_result_shape(r)
    test_serialize_dashboard_snapshot_empty(r)
    test_serialize_dashboard_snapshot_with_data(r)
    test_cycle_store_starts_empty(r)
    test_cycle_store_update_increments_count(r)
    test_pipeline_cycle_result_carries_prediction(r)
    test_flask_state_endpoint_before_any_cycle(r)
    test_flask_state_endpoint_after_cycles(r)
    test_flask_index_serves_html_shell(r)
    test_config_dashboard_defaults(r)
    test_config_dashboard_validation(r)
    r.summary()


if __name__ == "__main__":
    main()
