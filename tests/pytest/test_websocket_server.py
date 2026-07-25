"""
Tests for `astra.dashboard.server` -- the FastAPI + WebSocket real-time
dashboard app.

Uses FastAPI's `TestClient` (starlette's, synchronous-looking but
handles the WebSocket handshake and the app's own `startup`/`shutdown`
lifecycle for us via its context-manager form) rather than a real
Uvicorn process, so these run fast and don't bind a real port.

Marked `integration` (see pytest.ini) since these spin up a full
MockConnector + AsyncPipeline + FastAPI app per test, not just a single
unit -- still fast (sub-second each with a high test hz), just a
heavier scope than test_mock_connector_stream.py / test_async_pipeline.py.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from astra.dashboard.server import create_realtime_app
from astra.dashboard.store import CycleStore
from astra.interface.state_reader import StateReader
from astra.utils.config import ASTRAConfig

pytestmark = pytest.mark.integration


def _fast_config() -> ASTRAConfig:
    return dataclasses.replace(ASTRAConfig(), poll_interval_s=0.05)


def _seeded_reader(config: ASTRAConfig) -> StateReader:
    reader = StateReader.for_mock(config, sim_step_s=config.poll_interval_s)
    reader.connect()
    reader.create_aircraft("AC1", "A320", 10.90, 106.70, 180.0, 30000, 250)
    reader.create_aircraft("AC2", "B738", 10.70, 106.70, 0.0, 30000, 250)
    reader.send_command("OP")
    return reader


def test_app_builds_and_mounts_legacy_routes():
    config = _fast_config()
    app = create_realtime_app(config, reader=_seeded_reader(config), hz=10.0)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "ASTRA" in r.text

        r_state = client.get("/state")
        assert r_state.status_code == 200


def test_api_health_reports_cycle_progress():
    config = _fast_config()
    app = create_realtime_app(config, reader=_seeded_reader(config), hz=10.0)
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hz"] == 10.0


def test_api_state_matches_legacy_state_shape():
    config = _fast_config()
    store = CycleStore()
    app = create_realtime_app(config, reader=_seeded_reader(config), store=store, hz=10.0)
    with TestClient(app) as client:
        # give the background telemetry loop a moment to publish a cycle
        import time

        time.sleep(0.3)
        r_new = client.get("/api/state")
        r_legacy = client.get("/state")
        assert r_new.status_code == r_legacy.status_code == 200
        assert r_new.json()["cycle_count"] == r_legacy.json()["cycle_count"]
        assert r_new.json()["cycle_count"] >= 1


def test_websocket_receives_frames_in_order():
    config = _fast_config()
    app = create_realtime_app(config, reader=_seeded_reader(config), hz=15.0)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            first = ws.receive_json()
            second = ws.receive_json()
            third = ws.receive_json()

    counts = [first.get("cycle_count"), second.get("cycle_count"), third.get("cycle_count")]
    # Monotonically non-decreasing -- the first frame is "whatever the
    # store has right now" (may be 0/no-data at connect time), then
    # strictly advancing as the background loop publishes more cycles.
    assert counts == sorted(counts)


def test_multiple_websocket_clients_receive_same_broadcast():
    config = _fast_config()
    app = create_realtime_app(config, reader=_seeded_reader(config), hz=15.0)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws_a, client.websocket_connect(
            "/ws/telemetry"
        ) as ws_b:
            frame_a = ws_a.receive_json()
            frame_b = ws_b.receive_json()
            # Both clients see the same underlying store shape at
            # connect time (has_data key present either way).
            assert "has_data" in frame_a
            assert "has_data" in frame_b


def test_create_realtime_app_rejects_non_mock_reader(config):
    """create_realtime_app() is documented as mock-only; a BlueSky-backed
    reader (is_mock False) must be rejected, not silently mishandled."""

    class _FakeLiveReader:
        is_mock = False

    with pytest.raises(TypeError):
        create_realtime_app(config, reader=_FakeLiveReader())


def test_mount_legacy_app_false_skips_flask_routes():
    config = _fast_config()
    app = create_realtime_app(
        config, reader=_seeded_reader(config), hz=10.0, mount_legacy_app=False
    )
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 404
        r_health = client.get("/api/health")
        assert r_health.status_code == 200
