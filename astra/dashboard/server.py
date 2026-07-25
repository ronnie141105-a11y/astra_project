"""
FastAPI + WebSocket dashboard server -- real-time mock telemetry streaming.

Replaces the old HTTP-polling model (frontend `fetch("/state")` every
`poll_interval_s`, see `astra.dashboard.legacy_flask_app`) with a push
model for the mock/offline testing platform this project is actually
developed and evaluated against: `MockConnector.stream()` (async, a
configurable 1-5 Hz -- see `astra.interface.mock_connector`) feeds
`AsyncPipeline.stream()` (`astra.pipeline`), and every resulting
`CycleResult` is broadcast, as it completes, to every WebSocket client
connected to `/ws/telemetry`. There is no poll interval on the
WebSocket path: the server sends a frame the moment one exists.

Scope
-----
This module owns exactly the real-time *mock* streaming loop and its
transport (`/ws/telemetry`, plus a `/state`-shaped REST fallback at
`/api/state`). It deliberately does NOT re-implement the Scenario
Builder, the geo-layer map shell, or live-BlueSky mode -- those are
unchanged and still served by `astra.dashboard.legacy_flask_app`'s
Flask `app`, which this module mounts (via `a2wsgi.WSGIMiddleware`) at
`/` as a catch-all beneath its own FastAPI routes. Concretely:

* `GET /`                -> legacy Flask `index.html` (unchanged)
* `GET /scenario*`       -> legacy Flask Scenario Builder routes (unchanged)
* `GET /state`           -> legacy Flask `/state` (unchanged; still works
                             for anything still polling it)
* `GET /api/state`       -> NEW: same JSON shape as `/state`, served
                             natively by FastAPI, reading the same
                             `CycleStore` the WebSocket loop publishes
                             into (a REST fallback for a client that
                             can't hold a WebSocket open, not the
                             primary transport)
* `WS   /ws/telemetry`   -> NEW: real-time push stream, one JSON frame
                             (the same `serialize_dashboard_snapshot`
                             payload `/state` returns) per completed
                             pipeline cycle

Both transports read from the same one `CycleStore`, so a client on
either one sees the same data -- the WebSocket loop is simply the thing
now responsible for *advancing* the simulation and running the
pipeline, a job `main.py`'s synchronous poll loop used to do.

Why FastAPI owns the mock connector/pipeline (not `main.py`)
---------------------------------------------------------------
The old architecture had `main.py` own the poll loop and push into a
`CycleStore` the Flask app only read from. Real-time streaming needs an
event loop that is also serving WebSocket connections concurrently, so
that ownership moves here: `create_realtime_app()`'s FastAPI startup
hook starts the mock connector + `AsyncPipeline.stream()` as a
background `asyncio` task on the *same* loop Uvicorn is already running
for WebSocket I/O, and stops it (via `stop_event`) on shutdown.
`main.py`'s `--mock` path now simply calls `run_realtime_dashboard()`
(blocking) instead of running its own loop -- see `main.py`.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional, Set

import uvicorn
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from astra.dashboard import serializers
from astra.dashboard.legacy_flask_app import create_app as _create_legacy_flask_app
# Re-exported for backward compatibility: existing callers/tests that
# do `from astra.dashboard.server import create_app` (the Flask
# factory this module used to define directly, pre-refactor) keep
# working unchanged. `run_dashboard_in_background` is re-exported for
# the same reason -- it is still what live-BlueSky mode uses.
from astra.dashboard.legacy_flask_app import create_app  # noqa: F401
from astra.dashboard.legacy_flask_app import run_dashboard_in_background  # noqa: F401
from astra.dashboard.store import CycleStore
from astra.interface.mock_connector import MockConnector
from astra.interface.state_reader import StateReader
from astra.pipeline import AsyncPipeline
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger("astra.dashboard")

#: Where the Scenario Builder page saves/loads named scenarios -- kept
#: here too (not just in `legacy_flask_app`) since `create_realtime_app`
#: forwards it straight through to `_create_legacy_flask_app`.
_DEFAULT_SCENARIOS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scenarios", "builder"
)


class ConnectionManager:
    """Tracks connected `/ws/telemetry` clients and broadcasts frames to all of them.

    Deliberately minimal -- one shared telemetry stream broadcast to
    every connected client (matches the old architecture's single
    shared `CycleStore`/simulation; there is one mock simulation per
    server process, not one per client). A client that fails mid-send
    is dropped from the set rather than allowed to raise out of the
    broadcast loop and block delivery to everyone else.
    """

    def __init__(self) -> None:
        self._active: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._active.add(websocket)
        _LOG.info("WebSocket client connected (%d total).", len(self._active))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._active.discard(websocket)
        _LOG.info("WebSocket client disconnected (%d total).", len(self._active))

    async def broadcast_json(self, payload: dict) -> None:
        """Send `payload` to every connected client, dropping any that fail."""
        async with self._lock:
            targets = list(self._active)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._active)


async def _telemetry_loop(
    pipeline: AsyncPipeline,
    connector: MockConnector,
    store: CycleStore,
    manager: ConnectionManager,
    config: ASTRAConfig,
    hz: float,
    stop_event: asyncio.Event,
) -> None:
    """Background task: stream mock telemetry, run the pipeline, broadcast.

    One iteration of this loop is one real-time frame: pull a snapshot
    from `connector` (`AsyncPipeline.stream()` internally drives
    `connector.stream()`), run the full Milestone 2-7 pipeline on it,
    publish the result into `store` (so `/api/state` and the legacy
    `/state` -- since both share this same `store` -- stay current),
    and broadcast the same JSON payload to every connected WebSocket
    client. Runs until `stop_event` is set (app shutdown) or the task
    is cancelled.
    """
    try:
        async for result in pipeline.stream(connector, hz=hz, stop_event=stop_event):
            store.update(result)
            payload = serializers.serialize_dashboard_snapshot(store.snapshot(), config)
            await manager.broadcast_json(payload)
    except asyncio.CancelledError:
        _LOG.info("Telemetry loop cancelled -- shutting down.")
        raise
    except Exception:
        _LOG.exception("Telemetry loop crashed unexpectedly.")


def _seed_default_mock_traffic(reader: StateReader) -> None:
    """Seed a small converging scenario if the caller didn't supply one.

    Mirrors `main.py`'s `_setup_mock_traffic` at a smaller scale --
    kept here (rather than importing from `main.py`, a script entry
    point, not a library module) purely so `create_realtime_app()` is
    runnable standalone (e.g. `uvicorn astra.dashboard.server:app`)
    without requiring a caller to have already populated the mock.
    Callers that want the full thesis demo scenario should seed the
    `StateReader` themselves before passing it to `create_realtime_app`
    -- this fallback only fires when no aircraft exist yet.
    """
    if reader.list_aircraft():
        return
    reader.create_aircraft("HVN301", "A320", 10.96655, 106.70000, 180.0, 30000, 120)
    reader.create_aircraft("VJC302", "B738", 10.63345, 106.70000, 0.0, 30000, 130)
    reader.create_aircraft("PIC303", "A319", 10.79995, 106.86956, 270.0, 30500, 115)
    reader.create_aircraft("AXJ304", "B77W", 10.79995, 106.53044, 90.0, 30000, 125)
    reader.send_command("OP")
    _LOG.info("Seeded default mock traffic (4 aircraft, converging).")


def create_realtime_app(
    config: ASTRAConfig,
    reader: Optional[StateReader] = None,
    store: Optional[CycleStore] = None,
    hz: Optional[float] = None,
    scenarios_dir: str = _DEFAULT_SCENARIOS_DIR,
    mount_legacy_app: bool = True,
) -> FastAPI:
    """Build the real-time FastAPI dashboard app.

    Args:
        config: The running `ASTRAConfig`.
        reader: A `StateReader` backed by a `MockConnector`, already
            `connect()`-ed. If `None`, one is created and seeded with
            `_seed_default_mock_traffic()` -- convenient for
            `uvicorn astra.dashboard.server:app` / tests, but a caller
            wanting a specific scenario should build and pass their own
            (already-seeded) reader instead.
        store: The `CycleStore` the telemetry loop publishes into and
            `/api/state` (and, if `mount_legacy_app`, the legacy
            Flask `/state`) reads from. A fresh one is created if
            omitted.
        hz: Telemetry frame rate (see `MockConnector.stream`). Defaults
            to `1 / config.poll_interval_s`, clamped to [1, 5], if
            omitted -- so an unmodified `ASTRAConfig`
            (`poll_interval_s=1.0`) streams at 1 Hz, the same cadence
            the old poll loop ran at.
        scenarios_dir: Forwarded to the mounted legacy Flask app's
            Scenario Builder blueprint.
        mount_legacy_app: If True (default), mount the legacy Flask
            app (index.html, `/state`, Scenario Builder) at `/`. Tests
            that only care about the WebSocket/REST telemetry surface
            can pass `False` to skip building the Flask app entirely.

    Returns:
        A configured `FastAPI` app, not yet running. The mock
        connector's streaming loop starts on the app's startup hook
        and stops on shutdown -- it does not run until something
        actually serves this app (`uvicorn.run`, a `TestClient`
        context manager, etc.).
    """
    if reader is None:
        reader = StateReader.for_mock(config, sim_step_s=config.poll_interval_s)
        reader.connect()
        _seed_default_mock_traffic(reader)

    if not reader.is_mock:
        raise TypeError(
            "create_realtime_app() requires a MockConnector-backed StateReader "
            "-- real-time WebSocket streaming targets the mock/offline testing "
            "platform only (see this module's docstring). Live-BlueSky mode "
            "should keep using astra.dashboard.legacy_flask_app directly."
        )

    effective_hz = hz if hz is not None else min(max(1.0 / config.poll_interval_s, 1.0), 5.0)

    store = store if store is not None else CycleStore()
    manager = ConnectionManager()
    pipeline = AsyncPipeline(
        config, route_provider=reader.get_route, profile_provider=reader.get_flight_profile
    )
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def _lifespan(fastapi_app: FastAPI):
        # Startup: launch the telemetry loop as a background task on
        # this same event loop -- see this module's docstring for why
        # ownership of the simulation lives here now, not in main.py.
        stop_event.clear()
        fastapi_app.state.telemetry_task = asyncio.create_task(
            _telemetry_loop(
                pipeline, reader._connector, store, manager, config, effective_hz, stop_event
            )
        )
        _LOG.info(
            "Real-time telemetry loop started at %.2f Hz. WebSocket clients connect at /ws/telemetry.",
            effective_hz,
        )
        try:
            yield
        finally:
            # Shutdown: stop the loop cooperatively, then cancel/await
            # it so the task never outlives the app (e.g. across
            # repeated TestClient context-manager entries in a test
            # suite).
            stop_event.set()
            task = getattr(fastapi_app.state, "telemetry_task", None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(title="ASTRA Real-Time Dashboard", lifespan=_lifespan)
    app.state.store = store
    app.state.manager = manager
    app.state.reader = reader
    app.state.config = config

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        """REST fallback: the same payload `/ws/telemetry` streams, on demand."""
        return JSONResponse(serializers.serialize_dashboard_snapshot(store.snapshot(), config))

    @app.get("/api/health")
    async def api_health() -> JSONResponse:
        """Liveness probe: cycle count so far and how many WS clients are connected."""
        snapshot = store.snapshot()
        return JSONResponse(
            {
                "status": "ok",
                "cycle_count": snapshot.cycle_count,
                "connected_clients": manager.client_count,
                "hz": effective_hz,
            }
        )

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        """Real-time telemetry push. One JSON frame per completed pipeline cycle.

        Sends the current snapshot immediately on connect (so a client
        doesn't wait up to `1/hz` seconds to see anything), then
        relies entirely on `_telemetry_loop`'s broadcast for further
        frames. Any text the client sends is currently ignored (read
        in a loop purely to detect disconnects) -- reserved for a
        future client -> server control message, not needed yet since
        `hz` is a single server-wide setting today.
        """
        await manager.connect(websocket)
        try:
            await websocket.send_json(
                serializers.serialize_dashboard_snapshot(store.snapshot(), config)
            )
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)

    if mount_legacy_app:
        flask_app = _create_legacy_flask_app(store, config, reader=reader, scenarios_dir=scenarios_dir)
        app.mount("/", WSGIMiddleware(flask_app))

    return app


def run_realtime_dashboard(
    config: ASTRAConfig,
    reader: Optional[StateReader] = None,
    hz: Optional[float] = None,
) -> None:
    """Blocking entry point: build and serve the real-time dashboard with Uvicorn.

    `main.py`'s `--mock` path calls this instead of the old
    `run_dashboard_in_background()` + its own poll loop -- this
    function owns both the HTTP/WebSocket server *and* the simulation
    loop (see this module's docstring for why), so there is nothing
    left for `main.py` to run afterwards; this call blocks until the
    process is interrupted (Ctrl+C).

    Args:
        config: The running `ASTRAConfig`.
        reader: See `create_realtime_app`.
        hz: See `create_realtime_app`.
    """
    app = create_realtime_app(config, reader=reader, hz=hz)
    _LOG.info(
        "Real-time dashboard live at http://%s:%d/ (WebSocket: ws://%s:%d/ws/telemetry)",
        config.dashboard_host,
        config.dashboard_port,
        config.dashboard_host,
        config.dashboard_port,
    )
    uvicorn.run(app, host=config.dashboard_host, port=config.dashboard_port, log_level="warning")


if __name__ == "__main__":
    run_realtime_dashboard(ASTRAConfig())
