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

from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
from astra.trajectory.route_engine import RouteAwareTrajectoryEngine, RouteProvider
from astra.utils.config import ASTRAConfig


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
    """

    snapshot: TrafficSnapshot
    prediction: PredictionResult
    regions_by_horizon: Dict[int, List[ComplexityRegion]]
    tracks: List[FourDArhac]
    resolution_sets: List[ResolutionSet]
    sector_regions: Dict[str, ComplexityRegion] = field(default_factory=dict)
    sector_history: Dict[str, List[SectorComplexitySample]] = field(default_factory=dict)


class Pipeline:
    """Runs the full Milestone 2-7 sequence once per poll cycle.

    Owns one instance of each engine, shared across calls; `TrackerEngine`
    is the only stateful one, so a `Pipeline` instance must persist across
    cycles (do not recreate it per poll).

    Example::

        pipeline = Pipeline(config)
        result = pipeline.run_cycle(reader.poll())
    """

    def __init__(self, config: ASTRAConfig, route_provider: Optional[RouteProvider] = None) -> None:
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
        """
        self._config = config
        if route_provider is not None:
            self._trajectory_engine = RouteAwareTrajectoryEngine(config, route_provider)
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
        return CycleResult(
            snapshot=snapshot,
            prediction=prediction,
            regions_by_horizon=regions_by_horizon,
            tracks=tracks,
            resolution_sets=resolution_sets,
            sector_regions=sector_regions,
            sector_history=sector_history,
        )

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
