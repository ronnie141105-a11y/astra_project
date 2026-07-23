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
3.  **Joint (multi-aircraft) candidates.** For a cluster of 2+ members,
    ``resolve()`` also builds one or more ``JointResolutionCandidate``s
    that adjust the primary aircraft (the same one/candidate the
    single-aircraft search already found) plus a secondary member
    simultaneously, scored as one combined before/after comparison
    rather than each aircraft's own candidate scored in isolation. Each
    secondary aircraft is tried against every lever set in
    ``resolution_joint_secondary_levers`` (default: speed-only,
    heading-only, flight-level-only), so diverse pairings -- e.g. both
    aircraft change heading, or the primary changes heading while a
    secondary changes speed -- are generated as distinct, independently
    scored candidates, not collapsed into one fixed combination. See
    ``_build_joint_candidates``. Purely additive -- ``ResolutionSet.
    candidates`` is unchanged; the joint candidates are exposed via
    ``ResolutionSet.joint_candidates``.

Two further extensions on top of the above (Issues 1 & 2 follow-up):

4.  **Proactive whole-lookahead evaluation.** ``resolve()`` no longer
    evaluates only the single horizon closest to a track's predicted
    onset -- see ``_lookahead_horizons`` and ``ResolutionSet.
    candidates_by_horizon``. As soon as a track has a predicted onset
    at all, every configured horizon up to that onset is evaluated,
    so resolutions are proposed across the full strategic window from
    the moment a hotspot is discovered, not moments before it happens.
5.  **Impact-based ranking alongside the weighted score.**
    ``ResolutionSet.ranked_by_impact()`` sorts every single- and
    multi-aircraft candidate by ``complexity_delta_norm`` (pure
    complexity reduction) rather than the weighted ``resolution_score``
    -- both fields stay on every candidate so callers can choose either
    view without recomputing anything.
"""

import math
from typing import Dict, List, Optional, Tuple

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.candidates import (
    CandidateSpec,
    RouteProvider,
    apply_clearances,
    generate_candidates,
    matches_rvsm_parity,
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

        Proactive, whole-lookahead evaluation (Issue 2 fix): rather than
        scoring candidates at only the single horizon closest to
        ``track.predicted_onset_s`` (the old behaviour -- see
        ``_closest_horizon``, still used as a fallback), this evaluates
        every configured horizon up to and including the predicted
        onset (``_lookahead_horizons``) as soon as the track is
        eligible at all, so a strategic resolution is available well
        before the hotspot is imminent, not just moments before it.

        Args:
            track: An open track, already forecast this cycle by
                ``ForecastEngine``. Not mutated.
            snapshot: This cycle's current *observed* ``TrafficSnapshot``
                (candidates modify a copy of this before re-predicting).
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
                keyed by ``horizon_min``, exactly as passed to
                ``TrackerEngine.update()`` / ``ForecastEngine``.

        Returns:
            A ``ResolutionSet`` whose ``candidates``/``joint_candidates``
            are ranked best first at ``evaluated_horizon_min`` (the
            earliest horizon with a genuinely effective option), plus
            ``candidates_by_horizon`` covering every horizon actually
            evaluated. Still returned (never ``None``) if the track is
            not eligible, or has no aircraft resolvable in ``snapshot``
            -- just with empty fields.
        """
        horizons = self._lookahead_horizons(track)
        if not self._eligible(track):
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizons[0])

        candidates_by_horizon: Dict[int, List[ResolutionCandidate]] = {}
        joint_candidates_by_horizon: Dict[int, List["JointResolutionCandidate"]] = {}

        for h in horizons:
            before_region = self._matched_region(track, regions_by_horizon, h)
            if before_region is None:
                continue

            specs = generate_candidates(
                before_region, snapshot, self._config, route_provider=self._route_provider
            )
            scored = [
                self._evaluate(spec, before_region, h, regions_by_horizon) for spec in specs
            ]
            scored.sort(key=lambda c: c.resolution_score, reverse=True)
            candidates_by_horizon[h] = scored

            joint_candidates_by_horizon[h] = self._build_joint_candidates(
                before_region, snapshot, h, regions_by_horizon, scored
            )

        if not candidates_by_horizon:
            return ResolutionSet(track=track, candidates=[], evaluated_horizon_min=horizons[0])

        # Recommended horizon: earliest one (within the lookahead window,
        # already time-ordered by `_lookahead_horizons`) offering a
        # genuinely effective single- or joint-aircraft option, so the
        # controller gets the earliest actionable intervention rather
        # than the whole lookahead dumped with no primary suggestion.
        # Falls back to the first evaluated horizon if nothing in the
        # window clears the bar (still exposed via `candidates_by_horizon`
        # for visibility).
        def _has_effective_option(h: int) -> bool:
            singles = candidates_by_horizon.get(h) or []
            joints = joint_candidates_by_horizon.get(h) or []
            return (singles and singles[0].complexity_delta_norm > 0) or (
                joints and joints[0].complexity_delta_norm > 0
            )

        recommended_h = next(
            (h for h in horizons if h in candidates_by_horizon and _has_effective_option(h)),
            horizons[0] if horizons[0] in candidates_by_horizon else next(iter(candidates_by_horizon)),
        )

        return ResolutionSet(
            track=track,
            candidates=candidates_by_horizon.get(recommended_h, []),
            evaluated_horizon_min=recommended_h,
            joint_candidates=joint_candidates_by_horizon.get(recommended_h, []),
            candidates_by_horizon=candidates_by_horizon,
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

        Superseded by `_lookahead_horizons` as `resolve()`'s primary
        horizon-selection path (Issue 2 fix) -- kept as the single-value
        fallback `_lookahead_horizons` itself uses when a track has no
        onset estimate yet (e.g. `predicted_onset_s is None`, so there
        is no "up to onset" window to build), and for any other caller
        that still wants a single representative horizon rather than
        the full lookahead sweep.
        """
        horizons = self._config.prediction_horizons_min
        if track.predicted_onset_s is None or not track.track:
            return min(horizons)
        anchor_time_s = track.track[-1].computed_at_s
        target_lead_s = max(0.0, track.predicted_onset_s - anchor_time_s)
        return min(horizons, key=lambda h: abs(h * 60 - target_lead_s))

    def _lookahead_horizons(self, track: FourDArhac) -> List[int]:
        """Every configured horizon (ascending) up to and including the
        predicted onset -- the full proactive strategic window (Issue 2),
        not just the single point closest to the event.

        As soon as a hotspot is discovered and forecast (i.e. as soon as
        `track.predicted_onset_s` is set at all), this returns every
        horizon in `ASTRAConfig.prediction_horizons_min` that lands at or
        before the predicted onset lead time -- so `resolve()` proposes
        resolutions across the *entire* remaining lookahead immediately,
        rather than waiting until a single horizon (e.g. 5 min) prior to
        the event. Evaluating past the predicted onset is not useful --
        the hotspot is expected to have already begun by then -- so the
        window is capped there, not extended to every configured horizon
        unconditionally (that would also multiply per-cycle cost for no
        benefit on far-out onsets).

        Returns:
            Ascending list of horizon minutes, always non-empty. Falls
            back to `[_closest_horizon(track)]` when there is no onset
            estimate yet, or when every configured horizon is already
            past the predicted onset (a near-immediate hotspot) -- in
            both cases there is no multi-point window to sweep, only a
            single meaningful horizon.
        """
        horizons = sorted(self._config.prediction_horizons_min)
        if track.predicted_onset_s is None or not track.track:
            return horizons

        anchor_time_s = track.track[-1].computed_at_s
        target_lead_s = max(0.0, track.predicted_onset_s - anchor_time_s)
        in_window = [h for h in horizons if h * 60 <= target_lead_s]
        return in_window or [self._closest_horizon(track)]

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
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
    ) -> ResolutionCandidate:
        """Replay Trajectory -> Cluster -> Complexity on one hypothetical
        snapshot and score the result against `before_region` (OQ-3/OQ-4),
        plus a domino-effect penalty scanned across every horizon (see
        `_domino_cost`).

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
            spec.hypothetical_snapshot,
            prediction,
            regions_by_horizon,
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

    def _build_joint_candidates(
        self,
        before_region: ComplexityRegion,
        snapshot: TrafficSnapshot,
        horizon_min: int,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
        primary_candidates: List[ResolutionCandidate],
    ) -> List[JointResolutionCandidate]:
        """Build diverse multi-aircraft candidates for clusters of 2+ members.

        Reuses the already-ranked single-aircraft search rather than
        re-deriving it: every joint candidate's primary leg is exactly
        ``primary_candidates[0]`` (the same aircraft/lever the
        single-aircraft search already found best), so joint candidates
        are only ever offered as *additions* on top of that result,
        never a different, unrelated primary choice.

        Unlike the original single-combination design, this now:

        * Allows clusters of exactly 2 resolvable members (previously
          required 3+) -- the most common real conflict, and the case
          Issue 1 explicitly asked for ("2 or more aircraft").
        * Tries every lever set in
          ``ASTRAConfig.resolution_joint_secondary_levers`` (default
          ``[["SPEED"], ["HEADING"], ["FLIGHT_LEVEL"]]``) for each
          secondary aircraft, rather than always defaulting to
          speed-only -- e.g. "primary HEADING + secondary HEADING"
          (both change heading) and "primary HEADING + secondary
          SPEED" both become distinct, independently-scored
          candidates, giving genuine maneuver-pairing diversity.

        One lever set per secondary aircraft per combination stays the
        rule (not a cross-product of levers *within* one leg): 2-3
        aircraft x every lever x every combination would be a genuine
        combinatorial blow-up the exhaustive, no-optimisation-library
        approach this project uses elsewhere is not designed for,
        whereas one clean lever per secondary aircraft per combination
        stays deterministic and cheap -- the same bounded-search
        reasoning as before, just no longer collapsed down to a single
        result.

        Each combination's legs are applied *simultaneously* to one
        hypothetical snapshot and scored as a single combined
        before/after comparison (via ``_score_joint_legs``) -- not the
        sum of each leg's own separately-computed score -- so every
        returned candidate reflects what actually happens to the
        cluster when every aircraft in it moves at once.

        Args:
            before_region: Same "before" region every single-aircraft
                candidate in ``primary_candidates`` was scored against.
            snapshot: The current *observed* snapshot (legs are applied
                to a copy of this, exactly as the single-aircraft path
                does).
            horizon_min: The evaluated horizon (same one every
                single-aircraft candidate used).
            regions_by_horizon: This cycle's real regions at every
                horizon, for the domino-cost penalty (scanned across
                all of them, not just `horizon_min` -- see
                `_domino_cost`).
            primary_candidates: This track's already-ranked
                single-aircraft candidates (``resolve()`` computes
                these first) -- ``primary_candidates[0]`` becomes every
                joint candidate's primary leg.

        Returns:
            Zero or more ``JointResolutionCandidate``s (one per
            secondary aircraft x lever-set combination that produced a
            usable secondary candidate), sorted descending by
            ``complexity_delta_norm`` (impact -- matches
            ``ResolutionSet.ranked_by_impact``). Empty if there are
            fewer than 2 resolvable cluster members, no single-aircraft
            candidates to build a primary leg from, or no combination
            yields a usable secondary candidate.
        """
        if not primary_candidates:
            return []

        ranked = select_target_aircraft_ranked(before_region.cluster, snapshot, self._config)
        if len(ranked) < 2:
            # Nothing left to pair the primary with.
            return []

        # For a 2-aircraft cluster, both aircraft must move -- "leave one
        # as a fixed reference point" only makes sense for 3+ members
        # (moving only 1 of 2 aircraft is just the single-aircraft
        # candidate the primary search already returned, not a genuine
        # joint option). For 3+ members, preserve the original design:
        # one member is deliberately left as a fixed reference point
        # rather than moving everyone at once (e.g. a 3-aircraft cluster
        # gets a 2-leg joint candidate, a 4-aircraft cluster gets 3
        # legs), further capped by `resolution_joint_max_targets` for
        # larger clusters still.
        if len(ranked) == 2:
            secondary_targets = ranked[1:2]
        else:
            max_targets = min(len(ranked) - 1, self._config.resolution_joint_max_targets)
            secondary_targets = ranked[1:max_targets]
        if not secondary_targets:
            return []

        primary_best = primary_candidates[0]
        primary_leg = ResolutionLeg(
            target_callsign=primary_best.target_callsign,
            clearance_type=primary_best.clearance_type,
            delta_value=primary_best.delta_value,
            maneuver_kind=primary_best.maneuver_kind,
            vector_duration_s=primary_best.vector_duration_s,
        )
        primary_leg_tuple: Tuple[str, str, float] = (
            primary_best.target_callsign, primary_best.clearance_type, primary_best.delta_value,
        )

        results: List[JointResolutionCandidate] = []
        seen_leg_signatures = set()

        for lever_set in self._config.resolution_joint_secondary_levers:
            for secondary in secondary_targets:
                if secondary.callsign == primary_best.target_callsign:
                    continue  # same aircraft as the primary leg -- nothing to add

                specs = generate_candidates(
                    before_region,
                    snapshot,
                    self._config,
                    route_provider=self._route_provider,
                    target=secondary,
                    levers=lever_set,
                )
                if not specs:
                    continue
                scored = [
                    self._evaluate(spec, before_region, horizon_min, regions_by_horizon)
                    for spec in specs
                ]
                best_secondary = max(scored, key=lambda c: c.resolution_score)

                secondary_leg = ResolutionLeg(
                    target_callsign=best_secondary.target_callsign,
                    clearance_type=best_secondary.clearance_type,
                    delta_value=best_secondary.delta_value,
                    maneuver_kind=best_secondary.maneuver_kind,
                    vector_duration_s=best_secondary.vector_duration_s,
                )
                signature = (
                    primary_leg.target_callsign, primary_leg.clearance_type,
                    secondary_leg.target_callsign, secondary_leg.clearance_type,
                )
                if signature in seen_leg_signatures:
                    continue  # same (aircraft, lever) pairing already scored
                seen_leg_signatures.add(signature)

                legs = [primary_leg, secondary_leg]
                leg_tuples = [
                    primary_leg_tuple,
                    (best_secondary.target_callsign, best_secondary.clearance_type, best_secondary.delta_value),
                ]
                total_deviation = primary_best.deviation_cost_norm + best_secondary.deviation_cost_norm
                total_fuel = primary_best.fuel_cost_proxy_norm + best_secondary.fuel_cost_proxy_norm

                candidate = self._score_joint_legs(
                    legs, leg_tuples, total_deviation, total_fuel,
                    primary_best, before_region, snapshot, horizon_min, regions_by_horizon,
                )
                if candidate is not None:
                    results.append(candidate)

        results.sort(key=lambda c: c.complexity_delta_norm, reverse=True)
        return results

    def _score_joint_legs(
        self,
        legs: List[ResolutionLeg],
        leg_tuples: List[Tuple[str, str, float]],
        total_deviation: float,
        total_fuel: float,
        primary_best: ResolutionCandidate,
        before_region: ComplexityRegion,
        snapshot: TrafficSnapshot,
        horizon_min: int,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
    ) -> Optional[JointResolutionCandidate]:
        """Apply a set of legs simultaneously and score the combined effect.

        Factored out of the old single-combination `_build_joint_candidate`
        so `_build_joint_candidates` can call it once per lever/secondary
        combination without duplicating the apply-predict-rescore tail.
        """
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
            joint_snapshot,
            prediction,
            regions_by_horizon,
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
        hypothetical_snapshot_now: TrafficSnapshot,
        prediction: PredictionResult,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
        before_region: ComplexityRegion,
    ) -> float:
        """Worst-case penalty in ``[0, 1]`` for hotspots this candidate
        creates or worsens *elsewhere*, scanned across every horizon this
        cycle has real regions or a prediction for -- not just the one
        horizon the primary before/after comparison uses.

        A candidate can look clean at `evaluated_horizon_min` (where the
        target track's own onset is expected) while still spiking a
        *different* hotspot at an earlier or later horizon -- e.g. a
        SPEED reduction that fixes the resolved track's own conflict at
        40 min but pushes the same aircraft into a new one at 20 min.
        The original (Milestone 7) domino check only ever looked at
        `evaluated_horizon_min`, so this was silently uncounted; see
        docs/backend_improvements_backlog.md item 3.

        This scans horizon 0 (the clearance's immediate effect, via
        `hypothetical_snapshot_now`) plus every horizon in
        `prediction.horizons_min` (the same configured horizons
        `ResolutionEngine`'s own trajectory engine predicts), and takes
        the maximum per-horizon penalty from `_domino_cost_at_horizon`.
        Because `evaluated_horizon_min` is always one of the horizons
        scanned, this is a strict generalisation: the result can only be
        greater than or equal to what the original single-horizon check
        would have returned, never less -- no existing candidate's
        domino cost silently drops.

        This does cost more per candidate than the original single-
        horizon check (one `ClusterEngine.detect()` + a
        `ComplexityEngine.assess()` per new hypothetical cluster, now
        repeated at every horizon instead of one) -- see
        docs/backend_improvements_backlog.md item 4, which flags this as
        a compounding cost alongside the wider step-multiplier search
        and joint candidates. Not yet a measured problem at this
        project's traffic scale (~40 aircraft).

        Args:
            hypothetical_snapshot_now: The candidate's clearance applied
                to the current, real (horizon-0) snapshot -- `_evaluate`
                passes `spec.hypothetical_snapshot`;
                `_build_joint_candidate` passes its own `joint_snapshot`.
            prediction: The full multi-horizon prediction already
                computed for this candidate (including any
                vector-and-rejoin override already applied -- callers
                pass the same `prediction` they used for their own
                before/after comparison, so this sees exactly the same
                predicted future).
            regions_by_horizon: This cycle's real regions at every
                horizon (as passed to `resolve()`).
            before_region: The track's own matched region -- excluded
                from the "elsewhere" scan at every horizon (by cluster
                identity, same as the original single-horizon check).

        Returns:
            The worst (maximum) per-horizon penalty across every horizon
            scanned, in ``[0, 1]``.
        """
        worst = self._domino_cost_at_horizon(
            hypothetical_snapshot_now, regions_by_horizon.get(0, []), before_region
        )
        for horizon_min in prediction.horizons_min:
            hypothetical_snapshot = prediction.at(horizon_min)
            real_regions = regions_by_horizon.get(horizon_min, [])
            worst = max(
                worst,
                self._domino_cost_at_horizon(hypothetical_snapshot, real_regions, before_region),
            )
        return worst

    def _domino_cost_at_horizon(
        self,
        hypothetical_snapshot: TrafficSnapshot,
        real_regions: List[ComplexityRegion],
        before_region: ComplexityRegion,
    ) -> float:
        """Domino-cost penalty at a single horizon (see `_domino_cost`).

        For every hypothetical cluster at this horizon other than the
        one matched back to the track being resolved (`before_region`),
        this re-associates it against `real_regions` (this cycle's real
        regions at the *same* horizon, again via `best_cluster_match`,
        excluding `before_region` itself):

        * If it matches an existing real region, only a *worsening*
          (``after - before`` clipped to ``>= 0``) counts -- an
          unrelated region that was already there and stays flat or
          improves contributes nothing.
        * If it matches no real region at all, it is a brand-new
          hotspot the candidate introduced; its full complexity score
          counts (this also correctly handles a horizon with no real
          regions at all: every hypothetical cluster there counts in
          full, since there is no real baseline to compare against).

        Contributions sum in raw ``complexity_score`` units (0-100,
        see ``ComplexityEngine``) and are clipped to ``[0, 1]`` by
        dividing by 100 -- so a single new max-severity hotspot alone
        saturates the penalty.
        """
        hypothetical_clusters = self._cluster_engine.detect(hypothetical_snapshot)
        matched_cluster = best_cluster_match(
            before_region.cluster, hypothetical_clusters, self._config.tracking_jaccard_threshold
        )
        other_hypothetical = [c for c in hypothetical_clusters if c is not matched_cluster]
        if not other_hypothetical:
            return 0.0

        other_real_regions = [r for r in real_regions if r.cluster != before_region.cluster]
        real_region_by_cluster = {region.cluster: region for region in other_real_regions}
        real_clusters = list(real_region_by_cluster.keys())

        total_penalty = 0.0
        for cluster in other_hypothetical:
            region = self._complexity_engine.assess(cluster, hypothetical_snapshot)
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

        Deviation is normalised against this lever's own configured step
        for every clearance type, so a candidate built at exactly the
        base step always yields 1.0 (a candidate at 2x the base step,
        from `resolution_step_multipliers`, yields 2.0, and so on) --
        kept as a ratio rather than a hardcoded 1.0 so the wider
        step-multiplier search does not require touching this method.

        Fuel-cost proxy is lever-specific, and -- as everywhere else in
        this scoring model -- explicitly a crude proxy, not a real
        fuel-burn model (no aircraft-type fuel-flow curves, no altitude/
        speed/weight interaction; see OQ-4):

        * ``FLIGHT_LEVEL``: the altitude-change magnitude itself
          (climbing/descending burns extra fuel beyond level cruise) --
          same value as deviation, unchanged from the original
          Milestone 7 design.
        * ``SPEED``: also the deviation magnitude itself, mirroring
          FLIGHT_LEVEL's convention -- a sustained speed change away
          from filed cruise speed, in either direction, costs fuel
          (flying faster increases drag-driven burn; flying slower
          means more total time at the burn rate for a given distance).
          This was previously hardcoded to 0.0 -- SPEED candidates paid
          no fuel cost at all regardless of magnitude, discovered while
          working through docs/backend_improvements_backlog.md item 3.
        * ``HEADING``: ``|sin(radians(delta_value))|`` -- the fraction of
          distance flown during the vector that goes *sideways* rather
          than toward the destination, a standard great-circle-agnostic
          proxy for wasted track miles on a modest-angle vector (bounded
          in ``[0, 1]``, peaking at a 90-degree vector). Also previously
          hardcoded to 0.0, including for `VECTOR_AND_REJOIN` candidates
          -- even a bounded, rejoin-ending vector still flies extra
          distance during the vector phase itself, which was going
          uncounted. Deliberately smaller than the deviation cost for
          the same modest angle (e.g. `sin(15 deg) ~= 0.26` vs. a
          deviation ratio of 1.0) -- a heading nudge wastes proportionally
          less of its flown distance than its "operational deviation"
          magnitude alone would suggest, since most of that distance
          still counts toward covering ground.

        FLIGHT_LEVEL's deviation cost also picks up a flat
        `resolution_rvsm_parity_penalty` on top of the magnitude ratio
        above when the candidate's *resulting* altitude would violate
        semicircular (odd-east/even-west) RVSM flight-level convention
        for the target's current track direction -- see
        `astra.resolution.candidates.matches_rvsm_parity`. This only
        affects deviation, not fuel: a non-standard level costs extra
        coordination, not (in this crude model) extra fuel. Previously
        unmodelled entirely -- a FLIGHT_LEVEL candidate that happened to
        recommend a level wrong for the aircraft's direction of flight
        scored no worse than one that didn't.
        """
        cfg = self._config
        if spec.clearance_type == "SPEED":
            deviation = abs(spec.delta_value) / cfg.resolution_speed_step_kt
            return deviation, deviation
        if spec.clearance_type == "FLIGHT_LEVEL":
            deviation = abs(spec.delta_value) / cfg.resolution_altitude_step_ft
            fuel_proxy = deviation
            target = spec.hypothetical_snapshot.get(spec.target_callsign)
            if target is not None and not matches_rvsm_parity(
                target.heading_deg, target.altitude_ft
            ):
                deviation += cfg.resolution_rvsm_parity_penalty
            return deviation, fuel_proxy
        deviation = abs(spec.delta_value) / cfg.resolution_heading_step_deg
        fuel_proxy = abs(math.sin(math.radians(spec.delta_value)))
        return deviation, fuel_proxy
