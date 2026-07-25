"""
Full-cycle ASTRA pipeline orchestrator.

Wraps Trajectory -> Cluster -> Complexity -> Tracking -> Forecast ->
Resolution into a single reusable call. `main.py` and Milestone 8's
`astra.dashboard` are the two consumers this was written for; see
docs/architecture.md §6.8 and docs/milestone_8_dashboard.md for the
as-built rationale (in particular why `CycleResult` carries the raw
`PredictionResult`, not just the derived `ComplexityRegion`s, so a
presentation layer can plot predicted aircraft positions without
recomputing anything the pipeline already computed).
"""

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.complexity.sector import SectorComplexityEngine, SectorComplexitySample
from astra.forecast.engine import ForecastEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import TrafficSnapshot
from astra.resolution.engine import ResolutionEngine
from astra.resolution.models import ResolutionSet
from astra.tracking.engine import TrackerEngine
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.trajectory.models import PredictionResult
from astra.trajectory.route_engine import ProfileProvider, RouteAwareTrajectoryEngine, RouteProvider
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)


@dataclass
class CycleResult:
    """Everything one poll cycle of the ASTRA pipeline produced.

    Attributes:
        snapshot: The observed `TrafficSnapshot` this cycle ran on.
        prediction: The raw `TrajectoryEngine.predict()` output for this
            cycle -- kept alongside `regions_by_horizon` (rather than
            only exposing the derived clusters/complexity) so a
            presentation layer can render predicted aircraft positions
            directly, without re-predicting anything.
        regions_by_horizon: `{horizon_min: [ComplexityRegion, ...]}`,
            keyed by 0 (observed) plus every horizon in `prediction`.
        tracks: The current set of open `FourDArhac` tracks after this
            cycle's `TrackerEngine.update()` + `ForecastEngine.forecast_many()`.
        resolution_sets: One `ResolutionSet` per resolved track this
            cycle (bounded by `resolution_max_tracks_per_cycle`).
        sector_regions: `{sector_name: ComplexityRegion}` for this cycle
            (Milestone 9, Tier 3) -- empty if no sectors are configured.
        sector_history: `{sector_name: [SectorComplexitySample, ...]}`,
            oldest first, for the HMI's complexity-charts page.
        routes: `{callsign: [(lat, lon), ...]}` -- each aircraft's own
            known remaining waypoints (first-to-fly-to first), straight
            from `route_provider` at this cycle's snapshot time, for
            any callsign a route is known for. Empty dict if no
            `route_provider` was supplied to this `Pipeline`. Lets a
            presentation layer draw an aircraft's real remaining route
            all the way to its actual final waypoint -- independent of
            `prediction`'s fixed horizon set, which may cut off before
            (or, pre-capping, project past) that final waypoint.
    """

    snapshot: TrafficSnapshot
    prediction: PredictionResult
    regions_by_horizon: Dict[int, List[ComplexityRegion]]
    tracks: List[FourDArhac]
    resolution_sets: List[ResolutionSet]
    sector_regions: Dict[str, ComplexityRegion] = field(default_factory=dict)
    sector_history: Dict[str, List[SectorComplexitySample]] = field(default_factory=dict)
    routes: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)


class Pipeline:
    """Runs the full Milestone 2-7 sequence once per poll cycle.

    Owns one instance of each engine, shared across calls; `TrackerEngine`
    is the only stateful one, so a `Pipeline` instance must persist across
    cycles (do not recreate it per poll).

    Example::

        pipeline = Pipeline(config)
        result = pipeline.run_cycle(reader.poll())
    """

    def __init__(
        self,
        config: ASTRAConfig,
        route_provider: Optional[RouteProvider] = None,
        profile_provider: Optional[ProfileProvider] = None,
    ) -> None:
        """Build one instance of each engine from shared config.

        Args:
            config: Shared ASTRA configuration.
            route_provider: Optional callable returning an aircraft's
                current remaining route given its callsign (typically
                ``state_reader.get_route``). When supplied, trajectory
                prediction uses ``RouteAwareTrajectoryEngine`` -- which
                follows known routes and falls back to plain dead
                reckoning per-aircraft for anything with no known route,
                so this is always at least as accurate as the baseline
                and safe to pass whenever route data might be available.
                When omitted (the default), prediction is plain
                constant-velocity dead reckoning (``TrajectoryEngine``),
                unchanged from Milestone 6. See
                ``astra/trajectory/route_engine.py`` for the full
                rationale and ``scripts/evaluate_trajectory_predictors.py``
                for the baseline-vs-route-aware evaluation this choice is
                based on.
            profile_provider: Optional callable returning an aircraft's
                Scenario Builder spawn profile given its callsign
                (typically ``state_reader.get_flight_profile``). Only
                used when ``route_provider`` is also supplied (a
                profile without a route has no "final waypoint" to
                profile against) -- lets ``RouteAwareTrajectoryEngine``
                predict a "LANDING" aircraft's Top of Descent-aware
                altitude instead of a flat constant-vertical-speed
                extrapolation. Ignored (with no effect) when
                ``route_provider`` is ``None``.
        """
        self._config = config
        self._route_provider = route_provider
        if route_provider is not None:
            self._trajectory_engine = RouteAwareTrajectoryEngine(config, route_provider, profile_provider)
        else:
            self._trajectory_engine = TrajectoryEngine(config)
        self._cluster_engine = ClusterEngine(config)
        self._complexity_engine = ComplexityEngine(config)
        self._tracker = TrackerEngine(config)
        self._forecaster = ForecastEngine(config)
        self._resolver = ResolutionEngine(config, route_provider=route_provider)
        self._sector_engine = SectorComplexityEngine(config)

    def run_cycle(self, snapshot: TrafficSnapshot) -> CycleResult:
        """Run one full pipeline cycle for `snapshot` and return its results."""
        prediction = self._trajectory_engine.predict(snapshot)
        regions_by_horizon = self._build_regions_by_horizon(snapshot, prediction)
        tracks = self._tracker.update(regions_by_horizon)
        self._forecaster.forecast_many(tracks, regions_by_horizon)
        resolution_sets = self._resolver.resolve_many(tracks, snapshot, regions_by_horizon)
        sector_regions = self._sector_engine.update(snapshot)
        sector_history = {
            sector.name: self._sector_engine.history(sector.name)
            for sector in self._config.sectors
        }
        routes = self._collect_routes(snapshot)
        return CycleResult(
            snapshot=snapshot,
            prediction=prediction,
            regions_by_horizon=regions_by_horizon,
            tracks=tracks,
            resolution_sets=resolution_sets,
            sector_regions=sector_regions,
            sector_history=sector_history,
            routes=routes,
        )

    def _collect_routes(self, snapshot: TrafficSnapshot) -> Dict[str, List[Tuple[float, float]]]:
        """Each in-snapshot aircraft's own known remaining route, if any.

        Empty dict when this `Pipeline` has no `route_provider` (plain
        dead-reckoning mode) -- mirrors the same "no route known" case
        the trajectory/resolution engines already handle per-aircraft.
        """
        if self._route_provider is None:
            return {}
        routes: Dict[str, List[Tuple[float, float]]] = {}
        for ac in snapshot:
            route = self._route_provider(ac.callsign)
            if route:
                routes[ac.callsign] = list(route)
        return routes

    def _build_regions_by_horizon(
        self, snapshot: TrafficSnapshot, prediction: PredictionResult
    ) -> Dict[int, List[ComplexityRegion]]:
        """Cluster and score the observed snapshot plus every predicted horizon.

        `prediction` is computed once by the caller (`run_cycle`) and passed
        in here rather than re-predicted, so `CycleResult.prediction` and the
        clusters/complexity derived from it are always the same prediction.
        """
        observed_clusters = self._cluster_engine.detect(snapshot)
        regions_by_horizon = {0: self._complexity_engine.assess_many(observed_clusters, snapshot)}

        clusters_by_horizon = self._cluster_engine.detect_all(prediction)
        for horizon_min in prediction.horizon_list():
            predicted_snapshot = prediction.at(horizon_min)
            regions_by_horizon[horizon_min] = self._complexity_engine.assess_many(
                clusters_by_horizon[horizon_min], predicted_snapshot
            )
        return regions_by_horizon


class AsyncPipeline(Pipeline):
    """`Pipeline`, plus an async, real-time streaming interface.

    `Pipeline.run_cycle()` itself is unchanged (still synchronous --
    every engine underneath is plain CPU-bound numpy/Python code with
    no I/O of its own, so there is nothing to natively `await`). What
    `AsyncPipeline` adds is:

    1. `run_cycle_async()`: runs `run_cycle()` in a worker thread
       (`asyncio.to_thread`) instead of directly on the event loop, so
       one cycle's computation never blocks other coroutines running
       concurrently on the same loop -- in particular, `asyncio.sleep()`
       in `MockConnector.stream()`'s timer, and any WebSocket
       send/receive `astra.dashboard.server` is doing for *other*
       connected clients while this cycle runs.
    2. `stream()`: an async generator that consumes a connector's own
       `async for snapshot in connector.stream(hz=...)` feed and
       re-yields one `CycleResult` per snapshot -- the same
       "trajectory -> cluster -> complexity -> tracking -> forecast ->
       resolution" sequence `run_cycle()` has always run, just wired to
       a real-time source instead of a caller manually calling
       `run_cycle()` once per manual `poll()`.

    A `Pipeline`/`AsyncPipeline` instance is still stateful (owns one
    `TrackerEngine` across cycles -- see `Pipeline`'s own docstring), so
    the same "construct once, reuse across the whole run" rule applies.

    Example::

        pipeline = AsyncPipeline(config, route_provider=reader.get_route)
        async for result in pipeline.stream(connector, hz=2.0):
            store.update(result)   # e.g. astra.dashboard.store.CycleStore
    """

    async def run_cycle_async(self, snapshot: TrafficSnapshot) -> CycleResult:
        """Run one pipeline cycle for `snapshot` off the event loop thread.

        Identical result to `run_cycle(snapshot)` -- this only changes
        *where* the (synchronous, CPU-bound) work happens, not what it
        computes. See the class docstring for why this matters once
        multiple coroutines (streaming timers, WebSocket I/O for
        several connected dashboard clients) share one event loop.

        Args:
            snapshot: The `TrafficSnapshot` to run the cycle on.

        Returns:
            This cycle's `CycleResult`, exactly as `run_cycle()` would
            return it.
        """
        return await asyncio.to_thread(self.run_cycle, snapshot)

    async def stream(
        self,
        connector,
        hz: float = 1.0,
        *,
        max_frames: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[CycleResult]:
        """Async-generate one `CycleResult` per real-time telemetry frame.

        Thin orchestration layer over `connector.stream()` (see
        `astra.interface.mock_connector.MockConnector.stream` -- any
        connector exposing the same `async def stream(...)` shape
        works, though only `MockConnector` implements one today) plus
        `run_cycle_async()`: for each `TrafficSnapshot` the connector
        yields, run one full pipeline cycle and yield its `CycleResult`.

        Args:
            connector: An object with an `async def stream(hz=...,
                max_frames=..., stop_event=...)` method yielding
                `TrafficSnapshot`s, e.g. a connected, running
                `MockConnector`.
            hz: Forwarded to `connector.stream()` -- frames per second.
                1-5 Hz recommended (see `MockConnector.stream`'s
                docstring for the same reasoning).
            max_frames: Forwarded to `connector.stream()` -- stop after
                this many cycles (mainly for tests).
            stop_event: Forwarded to `connector.stream()` -- an
                `asyncio.Event` that ends the stream cooperatively when
                set, instead of only via task cancellation.

        Yields:
            One `CycleResult` per input snapshot, in the same order the
            connector produced them (this method never reorders or
            drops frames -- one input frame always means exactly one
            output `CycleResult`, computed serially, so `TrackerEngine`'s
            per-cycle state stays correct; see `Pipeline`'s docstring on
            why cycles cannot run out of order or concurrently against
            each other).
        """
        async for snapshot in connector.stream(
            hz=hz, max_frames=max_frames, stop_event=stop_event
        ):
            try:
                yield await self.run_cycle_async(snapshot)
            except Exception:
                # A single bad cycle (e.g. a transient engine error on
                # unusual geometry) should not silently kill the whole
                # live stream out from under every connected dashboard
                # client -- log it and keep consuming subsequent frames,
                # the same "stay up" posture `main.py`'s old poll loop
                # had (it only ever stopped on KeyboardInterrupt).
                _LOG.exception(
                    "AsyncPipeline.stream(): run_cycle_async failed for "
                    "snapshot at t=%.1fs -- skipping this frame.",
                    snapshot.timestamp_s,
                )
