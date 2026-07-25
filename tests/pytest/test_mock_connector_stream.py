"""
Tests for `MockConnector.stream()` -- the async, real-time telemetry
generator added for the WebSocket dashboard (see
`astra.interface.mock_connector`'s module docstring).

These deliberately do NOT test `MockConnector`'s simulation physics
(landing/TOD profiles, route-following, stack command parsing, ...) --
that is `tests/test_interface.py`'s job (the pre-existing `Runner`-style
suite) and is unchanged by this refactor. These tests are scoped to the
one new thing `stream()` adds: a real-time async cadence around the
same `poll()`/`latest_snapshot()` this class has always had.
"""

import asyncio
import time

import pytest

from astra.interface.mock_connector import MockConnector


def _make_connector(sim_step_s: float = 1.0) -> MockConnector:
    connector = MockConnector(sim_step_s=sim_step_s)
    connector.connect()
    connector.create_aircraft("AC1", "A320", 10.90, 106.70, 180.0, 30000, 250)
    connector.send_command("OP")
    return connector


@pytest.mark.asyncio
async def test_stream_yields_max_frames_snapshots():
    connector = _make_connector()
    snapshots = []
    async for snapshot in connector.stream(hz=20.0, max_frames=5):
        snapshots.append(snapshot)
    assert len(snapshots) == 5


@pytest.mark.asyncio
async def test_stream_advances_sim_clock_one_tick_per_frame():
    connector = _make_connector(sim_step_s=1.0)
    timestamps = []
    async for snapshot in connector.stream(hz=20.0, max_frames=3):
        timestamps.append(snapshot.timestamp_s)
    assert timestamps == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_stream_respects_wall_clock_rate():
    connector = _make_connector()
    start = time.monotonic()
    count = 0
    async for _ in connector.stream(hz=5.0, max_frames=5):
        count += 1
    elapsed = time.monotonic() - start
    # 5 frames at 5 Hz should take ~1.0s wall-clock; generous bounds to
    # keep this robust on a loaded CI box.
    assert count == 5
    assert 0.6 <= elapsed <= 2.5


@pytest.mark.asyncio
async def test_stream_rejects_non_positive_hz():
    connector = _make_connector()
    with pytest.raises(ValueError):
        async for _ in connector.stream(hz=0):
            pass


@pytest.mark.asyncio
async def test_stream_warns_outside_recommended_range(caplog):
    connector = _make_connector()
    with caplog.at_level("WARNING"):
        async for _ in connector.stream(hz=15.0, max_frames=1):
            pass
    assert any("recommended" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_stream_stops_on_stop_event():
    connector = _make_connector()
    stop_event = asyncio.Event()
    frames = []

    async def consume():
        async for snapshot in connector.stream(hz=20.0, stop_event=stop_event):
            frames.append(snapshot)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(frames) >= 1
    assert task.done()


@pytest.mark.asyncio
async def test_stream_does_not_mutate_sync_api():
    """stream() is additive -- poll()/latest_snapshot() still work standalone."""
    connector = _make_connector()
    connector.poll()
    snapshot = connector.latest_snapshot()
    assert snapshot is not None
    assert len(snapshot) == 1
