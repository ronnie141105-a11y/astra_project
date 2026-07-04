"""
Full-cycle ASTRA pipeline orchestrator.

Wraps Trajectory -> Cluster -> Complexity -> Tracking -> Forecast ->
Resolution into a single reusable call, replacing the copy-pasted
`run_cycle()`/`build_regions_by_horizon()` logic previously duplicated
across `demo_tracking.py`, `demo_forecast.py`, and `demo_resolution.py`.
See docs/pipeline.md.
"""

from dataclasses import dataclass
from typing import Dict, List

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.forecast.engine import ForecastEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import TrafficSnapshot
from astra.resolution.engine import ResolutionEngine
from astra.resolution.models import ResolutionSet
from astra.tracking.engine import TrackerEngine
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig


@dataclass
class CycleResult:
    """Everything one poll cycle of the ASTRA pipeline produced."""

    snapshot: TrafficSnapshot
    regions_by_horizon: Dict[int, List[ComplexityRegion]]
    tracks: List[FourDArhac]
    resolution_sets: List[ResolutionSet]


class Pipeline:
    """Runs the full Milestone 2-7 sequence once per poll cycle.

    Owns one instance of each engine, shared across calls; `TrackerEngine`
    is the only stateful one, so a `Pipeline` instance must persist across
    cycles (do not recreate it per poll).

    Example::

        pipeline = Pipeline(config)
        result = pipeline.run_cycle(reader.poll())
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Build one instance of each engine from shared config."""
        self._config = config
        self._trajectory_engine = TrajectoryEngine(config)
        self._cluster_engine = ClusterEngine(config)
        self._complexity_engine = ComplexityEngine(config)
        self._tracker = TrackerEngine(config)
        self._forecaster = ForecastEngine(config)
        self._resolver = ResolutionEngine(config)

    def run_cycle(self, snapshot: TrafficSnapshot) -> CycleResult:
        """Run one full pipeline cycle for `snapshot` and return its results."""
        regions_by_horizon = self._build_regions_by_horizon(snapshot)
        tracks = self._tracker.update(regions_by_horizon)
        self._forecaster.forecast_many(tracks, regions_by_horizon)
        resolution_sets = self._resolver.resolve_many(tracks, snapshot, regions_by_horizon)
        return CycleResult(
            snapshot=snapshot,
            regions_by_horizon=regions_by_horizon,
            tracks=tracks,
            resolution_sets=resolution_sets,
        )

    def _build_regions_by_horizon(
        self, snapshot: TrafficSnapshot
    ) -> Dict[int, List[ComplexityRegion]]:
        """Cluster and score the observed snapshot plus every predicted horizon."""
        observed_clusters = self._cluster_engine.detect(snapshot)
        regions_by_horizon = {0: self._complexity_engine.assess_many(observed_clusters, snapshot)}

        prediction = self._trajectory_engine.predict(snapshot)
        clusters_by_horizon = self._cluster_engine.detect_all(prediction)
        for horizon_min in prediction.horizon_list():
            predicted_snapshot = prediction.at(horizon_min)
            regions_by_horizon[horizon_min] = self._complexity_engine.assess_many(
                clusters_by_horizon[horizon_min], predicted_snapshot
            )
        return regions_by_horizon
