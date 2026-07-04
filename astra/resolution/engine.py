"""
AI resolution engine (Milestone 7).

``ResolutionEngine`` generates candidate ATC clearances for each open,
urgency-ranked ``FourDArhac`` track and scores each one by replaying the
existing pipeline (``TrajectoryEngine`` -> ``ClusterEngine`` ->
``ComplexityEngine``) on a hypothetically modified snapshot, re-associated
back to the track via ``astra.tracking.association``. Stateless: called
once per eligible track per poll cycle, after ``ForecastEngine`` has
already run in the same cycle. See
docs/milestone_7_resolution_design_review.md.
"""

from typing import Dict, List, Optional, Tuple

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import TrafficSnapshot
from astra.resolution.candidates import CandidateSpec, generate_candidates
from astra.resolution.models import ResolutionCandidate, ResolutionSet
from astra.tracking.association import best_cluster_match
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: Statuses eligible for resolution -- mirrors ForecastEngine's own
#: `_FORECASTABLE_STATUSES` (see docs/milestone_7_resolution_design_review.md
#: §3): a track additionally needs `forecast_urgency_rank is not None`
#: (an actual predicted onset to react to), checked separately below.
_RESOLVABLE_STATUSES = frozenset({"CONFIRMED", "GROWING", "PEAK", "DISSIPATING"})


class ResolutionEngine:
    """Generates and ranks candidate clearances for urgency-ranked tracks.

    Stateless after construction; safe to share one instance across the
    whole ASTRA process.

    Example::

        forecaster.forecast_many(tracks, regions_by_horizon)
        resolution_sets = resolution_engine.resolve_many(
            tracks, snapshot, regions_by_horizon
        )
        for rs in resolution_sets:
            best = rs.best()
            if best:
                print(rs.track.arhac_id, best.clearance_type, best.resolution_score)
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise from shared config; owns its own engine instances.

        The trajectory/cluster/complexity engines below are the same
        stateless, pure engines Milestones 2-4 already ship -- reused
        unchanged here to replay them on hypothetical snapshots (OQ-3),
        rather than duplicating their logic.
        """
        self._config = config
        self._trajectory_engine = TrajectoryEngine(config)
        self._cluster_engine = ClusterEngine(config)
        self._complexity_engine = ComplexityEngine(config)
        _LOG.debug(
            "ResolutionEngine initialised. speed_step=%.1fkt alt_step=%.0fft "
            "heading_step=%.1fdeg max_tracks_per_cycle=%d",
            config.resolution_speed_step_kt,
            config.resolution_altitude_step_ft,
            config.resolution_heading_step_deg,
            config.resolution_max_tracks_per_cycle,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        track: FourDArhac,
        snapshot: TrafficSnapshot,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
    ) -> ResolutionSet:
        """Generate and rank candidate clearances for one track.

        Args:
            track: An open track, already forecast this cycle by
                ``ForecastEngine``. Not mutated.
            snapshot: This cycle's current *observed* ``TrafficSnapshot``
                (candidates modify a copy of this before re-predicting).
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
                keyed by ``horizon_min``, exactly as passed to
                ``TrackerEngine.update()`` / ``ForecastEngine``.

        Returns:
            A ``ResolutionSet`` with 0-3 candidates, ranked best first.
            Empty (but still returned, never ``None``) if the track is
            not eligible, or has no aircraft resolvable in ``snapshot``.
        """
        horizon_min = self._closest_horizon(track)
        if not self._eligible(track):
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizon_min)

        before_region = self._matched_region(track, regions_by_horizon, horizon_min)
        if before_region is None:
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizon_min)

        specs = generate_candidates(before_region, snapshot, self._config)
        candidates = [
            self._evaluate(spec, before_region, horizon_min) for spec in specs
        ]
        candidates.sort(key=lambda c: c.resolution_score, reverse=True)
        return ResolutionSet(
            track=track, candidates=candidates, evaluated_horizon_min=horizon_min
        )

    def resolve_many(
        self,
        tracks: List[FourDArhac],
        snapshot: TrafficSnapshot,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
    ) -> List[ResolutionSet]:
        """Resolve the most urgent tracks this cycle, up to the safety cap.

        Args:
            tracks: This cycle's open tracks (any status; ineligible ones
                are filtered out below).
            snapshot: This cycle's current observed ``TrafficSnapshot``.
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s.

        Returns:
            One ``ResolutionSet`` per resolved track, ordered by
            ``forecast_urgency_rank`` (most urgent first), capped at
            ``resolution_max_tracks_per_cycle`` (OQ-5).
        """
        eligible = [t for t in tracks if self._eligible(t)]
        eligible.sort(key=lambda t: t.forecast_urgency_rank)
        capped = eligible[: self._config.resolution_max_tracks_per_cycle]
        return [self.resolve(t, snapshot, regions_by_horizon) for t in capped]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _eligible(track: FourDArhac) -> bool:
        """True if a track should be resolved this cycle (§3, OQ-5)."""
        return (
            track.status in _RESOLVABLE_STATUSES
            and track.forecast_urgency_rank is not None
            and track.predicted_onset_s is not None
            and bool(track.track)
        )

    def _closest_horizon(self, track: FourDArhac) -> int:
        """Prediction horizon (min) closest to `track.predicted_onset_s`.

        Bounds cost (OQ-5): every candidate is evaluated at this single
        horizon, not all five configured horizons.
        """
        horizons = self._config.prediction_horizons_min
        if track.predicted_onset_s is None or not track.track:
            return min(horizons)
        anchor_time_s = track.track[-1].computed_at_s
        target_lead_s = max(0.0, track.predicted_onset_s - anchor_time_s)
        return min(horizons, key=lambda h: abs(h * 60 - target_lead_s))

    def _matched_region(
        self,
        track: FourDArhac,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
        horizon_min: int,
    ) -> Optional[ComplexityRegion]:
        """Find this cycle's real predicted region at `horizon_min` matching `track`.

        Reuses ``best_cluster_match`` (the same primitive
        ``astra.forecast.horizon_series`` uses) rather than reimplementing
        matching -- this is the "before" state every candidate is scored
        against.
        """
        regions = regions_by_horizon.get(horizon_min, [])
        if not regions:
            return None
        region_by_cluster = {region.cluster: region for region in regions}
        match = best_cluster_match(
            track.track[-1].cluster,
            list(region_by_cluster.keys()),
            self._config.tracking_jaccard_threshold,
        )
        return region_by_cluster[match] if match is not None else None

    def _evaluate(
        self,
        spec: CandidateSpec,
        before_region: ComplexityRegion,
        horizon_min: int,
    ) -> ResolutionCandidate:
        """Replay Trajectory -> Cluster -> Complexity on one hypothetical
        snapshot and score the result against `before_region` (OQ-3/OQ-4)."""
        prediction = self._trajectory_engine.predict(spec.hypothetical_snapshot)
        hypothetical_snapshot_at_horizon = prediction.at(horizon_min)
        hypothetical_clusters = self._cluster_engine.detect(hypothetical_snapshot_at_horizon)

        after_score: Optional[float] = None
        after_components: Optional[Dict[str, float]] = None
        match = best_cluster_match(
            before_region.cluster, hypothetical_clusters, self._config.tracking_jaccard_threshold
        )
        if match is not None:
            after_region = self._complexity_engine.assess(
                match, hypothetical_snapshot_at_horizon
            )
            after_score = after_region.complexity_score
            after_components = dict(after_region.components)

        before_score = before_region.complexity_score
        complexity_delta_norm = 0.0
        if after_score is not None and before_score > 0.0:
            complexity_delta_norm = max(
                0.0, min(1.0, (before_score - after_score) / before_score)
            )

        deviation_cost_norm, fuel_cost_proxy_norm = self._costs(spec)
        cfg = self._config
        score = (
            cfg.resolution_weight_complexity * complexity_delta_norm
            - cfg.resolution_weight_deviation * deviation_cost_norm
            - cfg.resolution_weight_fuel * fuel_cost_proxy_norm
        )
        return ResolutionCandidate(
            clearance_type=spec.clearance_type,
            target_callsign=spec.target_callsign,
            delta_value=spec.delta_value,
            complexity_before=before_score,
            complexity_after=after_score,
            complexity_delta_norm=complexity_delta_norm,
            deviation_cost_norm=deviation_cost_norm,
            fuel_cost_proxy_norm=fuel_cost_proxy_norm,
            resolution_score=score,
            complexity_after_components=after_components,
            complexity_before_components=dict(before_region.components),
            hypothetical_prediction=prediction,
        )

    def _costs(self, spec: CandidateSpec) -> Tuple[float, float]:
        """Deviation-cost and fuel-cost-proxy norms for one candidate (OQ-4).

        Both are normalised against this lever's own configured step, so
        a candidate built at exactly that step (the only magnitude
        Milestone 7 generates -- see OQ-5) always yields 1.0; kept as a
        ratio (rather than a hardcoded 1.0) so a future sweep over step
        sizes would not require touching this method.
        """
        cfg = self._config
        if spec.clearance_type == "SPEED":
            return abs(spec.delta_value) / cfg.resolution_speed_step_kt, 0.0
        if spec.clearance_type == "FLIGHT_LEVEL":
            deviation = abs(spec.delta_value) / cfg.resolution_altitude_step_ft
            return deviation, deviation
        return abs(spec.delta_value) / cfg.resolution_heading_step_deg, 0.0
