"""
AI resolution engine (Milestone 7, extended with domino-effect scoring,
route-aware evaluation, vector-and-rejoin heading candidates, and
joint multi-aircraft candidates).

``ResolutionEngine`` generates candidate ATC clearances for each open,
urgency-ranked ``FourDArhac`` track and scores each one by replaying the
existing pipeline (``TrajectoryEngine`` -> ``ClusterEngine`` ->
``ComplexityEngine``) on a hypothetically modified snapshot, re-associated
back to the track via ``astra.tracking.association``. Stateless: called
once per eligible track per poll cycle, after ``ForecastEngine`` has
already run in the same cycle. See
docs/milestone_7_resolution_design_review.md.

Each candidate is also checked for a "domino effect": the hypothetical
snapshot is re-clustered in full (not just the track's own cluster), so a
manoeuvre that resolves the target hotspot but creates or worsens a
*different* one elsewhere is penalised via ``_domino_cost`` rather than
scored as if it were free. This remains a deterministic, exhaustive
enumeration over ``ASTRAConfig``'s fixed step sizes -- no learning, no
randomness, no optimisation library (RL is explicitly out of scope; see
docs/PROJECT_STATUS.md "Remaining work").

Three extensions beyond the original Milestone 7 design:

1.  **Route-aware evaluation.** ``ResolutionEngine`` now accepts an
    optional ``route_provider`` (same convention as ``Pipeline``): when
    given, hypothetical snapshots are re-predicted with
    ``RouteAwareTrajectoryEngine`` instead of plain dead reckoning, so a
    SPEED/FLIGHT_LEVEL candidate on a route-following aircraft is scored
    against where it would *actually* fly (through its remaining turns),
    not a straight line off into nowhere.
2.  **Vector-and-rejoin heading candidates** (the "2nd step"). A HEADING
    candidate on an aircraft with a known route is no longer an
    indefinite heading hold -- it is evaluated as a bounded vector
    (``resolution_vector_duration_s``) followed by a predicted turn back
    onto the aircraft's own route, via ``astra.resolution.vector_rejoin``.
    See ``_apply_vector_rejoin_override``.
3.  **Joint (multi-aircraft) candidates.** For a cluster of 3+ members,
    ``resolve()`` also builds one ``JointResolutionCandidate`` that
    adjusts the primary aircraft (the same one/candidate the
    single-aircraft search already found) plus up to
    ``resolution_joint_max_targets - 1`` further members simultaneously,
    scored as one combined before/after comparison rather than each
    aircraft's own candidate scored in isolation. See
    ``_build_joint_candidate``. Purely additive -- ``ResolutionSet.
    candidates`` is unchanged; the joint candidate is a new, optional
    ``ResolutionSet.joint_candidate`` field.
"""

from typing import Dict, List, Optional, Tuple

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.hotspot.engine import ClusterEngine
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.candidates import (
    CandidateSpec,
    RouteProvider,
    apply_clearances,
    generate_candidates,
    select_target_aircraft_ranked,
)
from astra.resolution.models import (
    JointResolutionCandidate,
    ResolutionCandidate,
    ResolutionLeg,
    ResolutionSet,
)
from astra.resolution.vector_rejoin import predict_vector_and_rejoin
from astra.tracking.association import best_cluster_match
from astra.tracking.models import FourDArhac
from astra.trajectory.engine import TrajectoryEngine
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.trajectory.route_engine import RouteAwareTrajectoryEngine
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
            best = rs.best_overall()
            if best:
                print(rs.track.arhac_id, best)
    """

    def __init__(
        self, config: ASTRAConfig, route_provider: Optional[RouteProvider] = None
    ) -> None:
        """Initialise from shared config; owns its own engine instances.

        Args:
            config: Shared ASTRA configuration.
            route_provider: Optional callable returning an aircraft's
                current remaining route given its callsign (typically
                ``state_reader.get_route``) -- same convention as
                ``astra.pipeline.Pipeline``. When given, hypothetical
                snapshots are re-predicted with
                ``RouteAwareTrajectoryEngine`` and it also becomes
                possible to generate vector-and-rejoin heading
                candidates (see module docstring). When omitted (the
                default), behaviour is unchanged from the original
                Milestone 7 design: plain dead-reckoning evaluation,
                heading candidates always a sustained hold.

        The cluster/complexity engines below are the same stateless,
        pure engines Milestones 2-4 already ship -- reused unchanged
        here to replay them on hypothetical snapshots (OQ-3), rather
        than duplicating their logic.
        """
        self._config = config
        self._route_provider = route_provider
        if route_provider is not None:
            self._trajectory_engine = RouteAwareTrajectoryEngine(config, route_provider)
        else:
            self._trajectory_engine = TrajectoryEngine(config)
        self._cluster_engine = ClusterEngine(config)
        self._complexity_engine = ComplexityEngine(config)
        _LOG.debug(
            "ResolutionEngine initialised. speed_step=%.1fkt alt_step=%.0fft "
            "heading_step=%.1fdeg multipliers=%s vector_duration_s=%.0f "
            "route_aware=%s max_tracks_per_cycle=%d joint_max_targets=%d",
            config.resolution_speed_step_kt,
            config.resolution_altitude_step_ft,
            config.resolution_heading_step_deg,
            config.resolution_step_multipliers,
            config.resolution_vector_duration_s,
            route_provider is not None,
            config.resolution_max_tracks_per_cycle,
            config.resolution_joint_max_targets,
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
            A ``ResolutionSet`` ranked best first (0 or more
            single-aircraft candidates, count depends on
            ``resolution_step_multipliers``/conflict-driven heading
            eligibility), plus ``joint_candidate`` when the matched
            cluster has 3+ resolvable members. Still returned (never
            ``None``) if the track is not eligible, or has no aircraft
            resolvable in ``snapshot`` -- just with empty/``None``
            fields.
        """
        horizon_min = self._closest_horizon(track)
        if not self._eligible(track):
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizon_min)

        before_region = self._matched_region(track, regions_by_horizon, horizon_min)
        if before_region is None:
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizon_min)

        specs = generate_candidates(
            before_region, snapshot, self._config, route_provider=self._route_provider
        )
        original_regions = regions_by_horizon.get(horizon_min, [])
        candidates = [
            self._evaluate(spec, before_region, horizon_min, original_regions)
            for spec in specs
        ]
        candidates.sort(key=lambda c: c.resolution_score, reverse=True)

        joint_candidate = self._build_joint_candidate(
            before_region, snapshot, horizon_min, original_regions, candidates
        )

        return ResolutionSet(
            track=track,
            candidates=candidates,
            evaluated_horizon_min=horizon_min,
            joint_candidate=joint_candidate,
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
        original_regions: List[ComplexityRegion],
    ) -> ResolutionCandidate:
        """Replay Trajectory -> Cluster -> Complexity on one hypothetical
        snapshot and score the result against `before_region` (OQ-3/OQ-4),
        plus a domino-effect penalty against `original_regions` (every
        real region at `horizon_min` this cycle, i.e. the track being
        resolved and everything else).

        For a ``VECTOR_AND_REJOIN`` candidate (``spec.vector_duration_s``
        set), the target's predicted trajectory from
        ``self._trajectory_engine`` -- which knows nothing about the
        vector, only "current heading" or "current route" -- is replaced
        at every horizon with the real two-phase kinematic via
        ``_apply_vector_rejoin_override`` before anything downstream
        (clustering, complexity, domino cost) runs.
        """
        prediction = self._trajectory_engine.predict(spec.hypothetical_snapshot)
        if spec.vector_duration_s is not None and spec.rejoin_route:
            ac_now = spec.hypothetical_snapshot.get(spec.target_callsign)
            if ac_now is not None:
                prediction = self._apply_vector_rejoin_override(
                    prediction, ac_now, spec.vector_duration_s, spec.rejoin_route
                )
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

        domino_cost_norm = self._domino_cost(
            hypothetical_clusters,
            match,
            hypothetical_snapshot_at_horizon,
            original_regions,
            before_region,
        )

        deviation_cost_norm, fuel_cost_proxy_norm = self._costs(spec)
        cfg = self._config
        score = (
            cfg.resolution_weight_complexity * complexity_delta_norm
            - cfg.resolution_weight_domino * domino_cost_norm
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
            domino_cost_norm=domino_cost_norm,
            complexity_after_components=after_components,
            complexity_before_components=dict(before_region.components),
            hypothetical_prediction=prediction,
            maneuver_kind="VECTOR_AND_REJOIN" if spec.vector_duration_s is not None else "SUSTAINED",
            vector_duration_s=spec.vector_duration_s,
        )

    def _apply_vector_rejoin_override(
        self,
        prediction: PredictionResult,
        ac_now: AircraftState,
        vector_duration_s: float,
        route: List[Tuple[float, float]],
    ) -> PredictionResult:
        """Rebuild `prediction` with `ac_now.callsign`'s entry at every
        horizon replaced by the two-phase vector-then-rejoin kinematic
        (`astra.resolution.vector_rejoin.predict_vector_and_rejoin`).

        Neither trajectory engine models this on its own: plain
        ``TrajectoryEngine`` would hold the vectored heading forever
        (never rejoins); ``RouteAwareTrajectoryEngine`` would ignore the
        vector entirely and fly straight back onto the route from the
        very first horizon (``advance_along_route`` always turns toward
        the next waypoint from wherever the aircraft currently is,
        regardless of its current heading -- see
        ``astra.trajectory.route_engine``'s module docstring). This
        patches in the correct in-between behaviour for just this one
        aircraft, leaving every other aircraft's prediction from
        ``self._trajectory_engine`` untouched.

        Args:
            prediction: The full multi-aircraft, multi-horizon
                prediction already computed for the hypothetical
                snapshot (every other aircraft's entries are reused
                as-is).
            ac_now: The target's state right after the clearance was
                applied (i.e. already at the vectored heading) --
                Phase 1's starting condition.
            vector_duration_s: How long Phase 1 (the vector) lasts
                before Phase 2 (the rejoin) begins.
            route: The target's real known remaining route to rejoin.

        Returns:
            A new ``PredictionResult`` with ``ac_now.callsign``'s entry
            overridden at every horizon; everything else unchanged.
        """
        new_snapshots: Dict[int, PredictedSnapshot] = {}
        for horizon_min, snap in prediction.snapshots.items():
            dt_s = horizon_min * 60.0
            overridden = predict_vector_and_rejoin(
                ac_now, route, ac_now.heading_deg, vector_duration_s, dt_s, snap.predicted_time_s
            )
            new_aircraft = dict(snap.aircraft)
            new_aircraft[overridden.callsign] = overridden
            new_snapshots[horizon_min] = PredictedSnapshot(
                horizon_min=snap.horizon_min,
                source_time_s=snap.source_time_s,
                predicted_time_s=snap.predicted_time_s,
                aircraft=new_aircraft,
            )
        return PredictionResult(
            source_time_s=prediction.source_time_s,
            aircraft_count=prediction.aircraft_count,
            horizons_min=prediction.horizons_min,
            snapshots=new_snapshots,
        )

    def _build_joint_candidate(
        self,
        before_region: ComplexityRegion,
        snapshot: TrafficSnapshot,
        horizon_min: int,
        original_regions: List[ComplexityRegion],
        primary_candidates: List[ResolutionCandidate],
    ) -> Optional[JointResolutionCandidate]:
        """Build one multi-aircraft candidate for clusters of 3+ members.

        Reuses the already-ranked single-aircraft search rather than
        re-deriving it: the primary leg is exactly
        ``primary_candidates[0]`` (the same aircraft/lever the
        single-aircraft search already found best), so a joint
        candidate is only ever offered as an *addition* on top of that
        result, never a different, unrelated primary choice. Each
        secondary aircraft (up to ``resolution_joint_max_targets - 1``
        of them, ranked the same way as the primary -- see
        ``select_target_aircraft_ranked``) gets its own best SPEED-only
        candidate, evaluated the ordinary single-aircraft way (against
        the same ``before_region``, in isolation) purely to *choose*
        the direction/magnitude -- SPEED is deliberately the only lever
        tried for secondaries (not the full HEADING/FLIGHT_LEVEL search)
        to keep this bounded: 2-3 aircraft x every lever combination
        would be a genuine combinatorial blow-up the exhaustive,
        no-optimisation-library approach this project uses elsewhere is
        not designed for, whereas one clean secondary-speed lever per
        aircraft is deterministic, cheap, and operationally realistic
        (nudge trailing aircraft's speed to help a primary manoeuvre
        actually de-densify a larger cluster -- exactly this project's
        own ``arrival_sequencing`` scenario's chosen lever, see
        ``scenarios/arrival_sequencing_demo.py``).

        All chosen legs are then applied *simultaneously* to one
        hypothetical snapshot and scored as a single combined
        before/after comparison -- not the sum of each leg's own
        separately-computed score -- so this candidate reflects what
        actually happens to the cluster when every aircraft moves at
        once, including cases where two aircraft's individually-good
        moves partially cancel each other out (or compound).

        Args:
            before_region: Same "before" region every single-aircraft
                candidate in ``primary_candidates`` was scored against.
            snapshot: The current *observed* snapshot (legs are applied
                to a copy of this, exactly as the single-aircraft path
                does).
            horizon_min: The evaluated horizon (same one every
                single-aircraft candidate used).
            original_regions: This cycle's real regions at
                ``horizon_min``, for the domino-cost penalty.
            primary_candidates: This track's already-ranked
                single-aircraft candidates (``resolve()`` computes
                these first) -- ``primary_candidates[0]`` becomes the
                joint candidate's primary leg.

        Returns:
            ``None`` if there are fewer than 3 resolvable cluster
            members, no single-aircraft candidates to build a primary
            leg from, or no secondary aircraft yields any SPEED
            candidate at all (nothing to add beyond the single-aircraft
            search already returned). Otherwise one
            ``JointResolutionCandidate`` with 2-3 legs.
        """
        if not primary_candidates:
            return None

        ranked = select_target_aircraft_ranked(before_region.cluster, snapshot, self._config)
        if len(ranked) < 3:
            # A 2-aircraft cluster has only the primary to move -- the
            # single-aircraft search above already covers it fully.
            return None

        # N-1 aircraft get a leg (one member is deliberately left as a
        # fixed reference point rather than moving everyone at once --
        # e.g. a 3-aircraft cluster gets a 2-leg joint candidate, a
        # 4-aircraft cluster gets 3 legs), further capped by
        # `resolution_joint_max_targets` for larger clusters still.
        max_targets = min(len(ranked) - 1, self._config.resolution_joint_max_targets)
        secondary_targets = ranked[1:max_targets]
        if not secondary_targets:
            return None

        primary_best = primary_candidates[0]
        legs: List[ResolutionLeg] = [
            ResolutionLeg(
                target_callsign=primary_best.target_callsign,
                clearance_type=primary_best.clearance_type,
                delta_value=primary_best.delta_value,
                maneuver_kind=primary_best.maneuver_kind,
                vector_duration_s=primary_best.vector_duration_s,
            )
        ]
        leg_tuples: List[Tuple[str, str, float]] = [
            (primary_best.target_callsign, primary_best.clearance_type, primary_best.delta_value)
        ]
        total_deviation = primary_best.deviation_cost_norm
        total_fuel = primary_best.fuel_cost_proxy_norm

        for secondary in secondary_targets:
            specs = generate_candidates(
                before_region,
                snapshot,
                self._config,
                route_provider=self._route_provider,
                target=secondary,
                levers=["SPEED"],
            )
            if not specs:
                continue
            scored = [
                self._evaluate(spec, before_region, horizon_min, original_regions)
                for spec in specs
            ]
            best_secondary = max(scored, key=lambda c: c.resolution_score)
            legs.append(
                ResolutionLeg(
                    target_callsign=best_secondary.target_callsign,
                    clearance_type="SPEED",
                    delta_value=best_secondary.delta_value,
                )
            )
            leg_tuples.append(
                (best_secondary.target_callsign, "SPEED", best_secondary.delta_value)
            )
            total_deviation += best_secondary.deviation_cost_norm
            total_fuel += best_secondary.fuel_cost_proxy_norm

        if len(legs) < 2:
            # No secondary aircraft contributed a usable candidate.
            return None

        joint_snapshot = apply_clearances(snapshot, leg_tuples)
        prediction = self._trajectory_engine.predict(joint_snapshot)
        if primary_best.maneuver_kind == "VECTOR_AND_REJOIN" and self._route_provider is not None:
            route = self._route_provider(primary_best.target_callsign)
            ac_now = joint_snapshot.get(primary_best.target_callsign)
            if route and ac_now is not None and primary_best.vector_duration_s is not None:
                prediction = self._apply_vector_rejoin_override(
                    prediction, ac_now, primary_best.vector_duration_s, route
                )

        hypothetical_snapshot_at_horizon = prediction.at(horizon_min)
        hypothetical_clusters = self._cluster_engine.detect(hypothetical_snapshot_at_horizon)

        after_score: Optional[float] = None
        after_components: Optional[Dict[str, float]] = None
        match = best_cluster_match(
            before_region.cluster, hypothetical_clusters, self._config.tracking_jaccard_threshold
        )
        if match is not None:
            after_region = self._complexity_engine.assess(match, hypothetical_snapshot_at_horizon)
            after_score = after_region.complexity_score
            after_components = dict(after_region.components)

        before_score = before_region.complexity_score
        complexity_delta_norm = 0.0
        if after_score is not None and before_score > 0.0:
            complexity_delta_norm = max(
                0.0, min(1.0, (before_score - after_score) / before_score)
            )

        domino_cost_norm = self._domino_cost(
            hypothetical_clusters,
            match,
            hypothetical_snapshot_at_horizon,
            original_regions,
            before_region,
        )

        cfg = self._config
        score = (
            cfg.resolution_weight_complexity * complexity_delta_norm
            - cfg.resolution_weight_domino * domino_cost_norm
            - cfg.resolution_weight_deviation * total_deviation
            - cfg.resolution_weight_fuel * total_fuel
        )

        return JointResolutionCandidate(
            legs=legs,
            complexity_before=before_score,
            complexity_after=after_score,
            complexity_delta_norm=complexity_delta_norm,
            deviation_cost_norm=total_deviation,
            fuel_cost_proxy_norm=total_fuel,
            resolution_score=score,
            domino_cost_norm=domino_cost_norm,
            complexity_after_components=after_components,
            complexity_before_components=dict(before_region.components),
        )

    def _domino_cost(
        self,
        hypothetical_clusters: List[Cluster],
        matched_cluster: Optional[Cluster],
        hypothetical_snapshot_at_horizon: TrafficSnapshot,
        original_regions: List[ComplexityRegion],
        before_region: ComplexityRegion,
    ) -> float:
        """Penalty in ``[0, 1]`` for hotspots this candidate creates or
        worsens *elsewhere* -- i.e. everywhere in the hypothetical
        picture at this horizon except the track being resolved.

        For every hypothetical cluster other than the one matched back
        to the track (`matched_cluster`), this re-associates it against
        this cycle's real ("before") regions at the same horizon (again
        via `best_cluster_match`, excluding `before_region` itself):

        * If it matches an existing real region, only a *worsening*
          (``after - before`` clipped to ``>= 0``) counts -- an
          unrelated region that was already there and stays flat or
          improves contributes nothing.
        * If it matches no real region at all, it is a brand-new
          hotspot the candidate introduced; its full complexity score
          counts.

        Contributions sum in raw ``complexity_score`` units (0-100,
        see ``ComplexityEngine``) and are clipped to ``[0, 1]`` by
        dividing by 100 -- so a single new max-severity hotspot alone
        saturates the penalty.
        """
        other_hypothetical = [c for c in hypothetical_clusters if c is not matched_cluster]
        if not other_hypothetical:
            return 0.0

        other_real_regions = [r for r in original_regions if r.cluster != before_region.cluster]
        real_region_by_cluster = {region.cluster: region for region in other_real_regions}
        real_clusters = list(real_region_by_cluster.keys())

        total_penalty = 0.0
        for cluster in other_hypothetical:
            region = self._complexity_engine.assess(cluster, hypothetical_snapshot_at_horizon)
            match = best_cluster_match(
                cluster, real_clusters, self._config.tracking_jaccard_threshold
            )
            if match is not None:
                before_score = real_region_by_cluster[match].complexity_score
                total_penalty += max(0.0, region.complexity_score - before_score)
            else:
                total_penalty += region.complexity_score

        return max(0.0, min(1.0, total_penalty / 100.0))

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
