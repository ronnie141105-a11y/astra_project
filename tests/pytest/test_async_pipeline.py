"""
Tests for `astra.pipeline.AsyncPipeline` -- the async streaming
orchestrator layered on top of the existing (synchronous) `Pipeline`.

Scope: verify `run_cycle_async()` matches `run_cycle()`'s result, and
that `stream()` correctly wires a connector's `stream()` output through
the pipeline, in order, one `CycleResult` per input snapshot -- not the
underlying engines' correctness (covered by
`tests/test_trajectory.py`, `tests/test_hotspot.py`, etc., which are
unchanged by this refactor).
"""

import asyncio

import pytest

from astra.interface.mock_connector import MockConnector
from astra.interface.state_reader import StateReader
from astra.pipeline import AsyncPipeline, Pipeline


def _seeded_mock(config, n_aircraft: int = 2) -> MockConnector:
    connector = MockConnector(sim_step_s=1.0)
    connector.connect()
    lats = [10.90, 10.70, 10.80][:n_aircraft]
    for i, lat in enumerate(lats):
        connector.create_aircraft(f"AC{i}", "A320", lat, 106.70, 180.0 if i % 2 == 0 else 0.0, 30000, 250)
    connector.send_command("OP")
    return connector


@pytest.mark.asyncio
async def test_run_cycle_async_matches_sync_run_cycle(config):
    connector = _seeded_mock(config)
    connector.poll()
    snapshot = connector.latest_snapshot()

    sync_pipeline = Pipeline(config)
    async_pipeline = AsyncPipeline(config)

    sync_result = sync_pipeline.run_cycle(snapshot)
    async_result = await async_pipeline.run_cycle_async(snapshot)

    assert async_result.snapshot.timestamp_s == sync_result.snapshot.timestamp_s
    assert len(async_result.tracks) == len(sync_result.tracks)
    assert set(async_result.regions_by_horizon.keys()) == set(sync_result.regions_by_horizon.keys())


@pytest.mark.asyncio
async def test_stream_yields_one_cycle_result_per_frame(config):
    connector = _seeded_mock(config)
    pipeline = AsyncPipeline(config)

    results = []
    async for result in pipeline.stream(connector, hz=20.0, max_frames=4):
        results.append(result)

    assert len(results) == 4
    # Frames must come out in the same order the connector produced
    # them -- TrackerEngine state depends on cycle order.
    timestamps = [r.snapshot.timestamp_s for r in results]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_stream_preserves_tracker_continuity_across_cycles(config):
    """The same AsyncPipeline instance's TrackerEngine should persist
    ARHAC identity across streamed cycles, same as calling run_cycle()
    repeatedly on one Pipeline instance would."""
    connector = _seeded_mock(config, n_aircraft=2)
    pipeline = AsyncPipeline(config)

    arhac_ids_per_cycle = []
    async for result in pipeline.stream(connector, hz=20.0, max_frames=3):
        arhac_ids_per_cycle.append({t.arhac_id for t in result.tracks})

    # Whatever ARHAC IDs appear in later cycles should be a subset of
    # (or overlap with) earlier ones -- IDs shouldn't just be discarded
    # and regenerated every cycle for continuously-tracked traffic.
    if arhac_ids_per_cycle[0]:
        assert arhac_ids_per_cycle[0] & arhac_ids_per_cycle[-1]


@pytest.mark.asyncio
async def test_stream_with_route_and_profile_provider(config):
    """AsyncPipeline.stream() works with the TOD-aware profile_provider
    wiring (route_provider + profile_provider), not just the
    route-less default engine."""
    reader = StateReader.for_mock(config, sim_step_s=1.0)
    reader.connect()
    reader.create_aircraft(
        "LAND1",
        "A320",
        10.90,
        106.70,
        180.0,
        30000,
        250,
        route_waypoints=[(10.5, 106.70), (10.2, 106.70)],
        flight_type="LANDING",
    )
    reader.send_command("OP")

    pipeline = AsyncPipeline(
        config, route_provider=reader.get_route, profile_provider=reader.get_flight_profile
    )

    results = []
    async for result in pipeline.stream(reader._connector, hz=20.0, max_frames=2):
        results.append(result)

    assert len(results) == 2
    assert "LAND1" in results[0].routes


@pytest.mark.asyncio
async def test_stream_stops_on_stop_event(config):
    connector = _seeded_mock(config)
    pipeline = AsyncPipeline(config)
    stop_event = asyncio.Event()
    results = []

    async def consume():
        async for result in pipeline.stream(connector, hz=20.0, stop_event=stop_event):
            results.append(result)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert len(results) >= 1
    assert task.done()
