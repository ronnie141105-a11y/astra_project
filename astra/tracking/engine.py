"""
4DARHAC tracking engine (Milestone 5, extended with provisional tracks).

``TrackerEngine`` is the one genuinely *stateful* component in the ASTRA
pipeline: unlike ``ClusterEngine``/``ComplexityEngine`` (stateless after
construction), it holds the current set of open ``FourDArhac`` tracks
across calls, and each ``update()`` call is seeded by that state from the
previous poll cycle -- the self-loop in docs/architecture.md §6.4.

See docs/milestone_5_tracking.md for the full design write-up.

Provisional tracks
-------------------
The original Milestone 5 design only ever opens or extends a track from
an *observed* (horizon 0) cluster -- meaning a genuinely medium-term
hotspot with zero current proximity (no aircraft anywhere near each
other yet) is structurally invisible to the tracker, no matter how
clearly a longer-horizon prediction shows it developing. This was the
single biggest gap found while building this project's own
`arrival_sequencing`/`sector_overload`/`crossing_airways` scenario
presets: every "genuinely 30-60 min out" scenario had to compromise by
keeping some aircraft artificially close together right now, specifically
to give the tracker something to seed a track from at all. See
docs/backend_improvements_backlog.md item 1 for the full write-up.

This adds a new lifecycle stage, ``"PROVISIONAL"``: a track opened from
a *predicted* (non-zero horizon) cluster that has no real-world
counterpart yet, tracked forward purely through this cycle's own
predicted-horizon regions (`FourDArhac.provisional_track`, kept entirely
separate from `FourDArhac.track`'s existing "observed only" invariant --
see that field's docstring). Each poll cycle, `update()` now does three
things instead of two:

1.  The original horizon-0 loop, unchanged in spirit: match each
    observed cluster against open tracks (now including provisional
    ones, since they live in the same `_open_tracks` store) via
    `best_track_match`. A provisional track's first real match is a
    *promotion* (`_promote_provisional_track`), not a normal extension
    -- it gets exactly the same "starts fresh" status logic as a
    brand-new track (`_initial_status`), since a prediction's own
    history should never count toward `tracking_confirm_cycles`;
    `first_detected_cycle_s` is preserved from the original provisional
    detection, so "flagged N minutes before it became real" is always
    recoverable from the track alone.
2.  `_detect_and_extend_provisional`: scans every *other* (non-zero)
    horizon this cycle, ascending, for a predicted cluster that does not
    already correspond to some open track (real or provisional) and
    clears `tracking_provisional_min_complexity` -- opening a new
    PROVISIONAL track for it. An already-provisional track matched again
    this cycle gets one new `provisional_track` entry (at most once per
    cycle, from whichever horizon matched first); a track matched that
    is *not* provisional (i.e. its own predicted future, already handled
    by step 1 or a previous cycle) is left alone -- just enough to
    suppress opening a duplicate.
3.  Aging/closing, unchanged in mechanism -- a provisional track that
    matches nothing at all this cycle (neither observed nor any
    predicted horizon) accumulates `_missed_cycles` exactly like a real
    one, and closes the same way if the phenomenon it predicted stops
    reappearing.

Provisional tracks are forecastable (`ForecastEngine._FORECASTABLE_STATUSES`
includes ``"PROVISIONAL"`` -- an onset time estimate for something not
yet observed at all is the actual point) but never resolvable
(``ResolutionEngine._RESOLVABLE_STATUSES`` does not include it -- there is
nothing concrete yet to issue a clearance against).
"""

import uuid
from typing import Dict, List, Set

from astra.complexity.models import ComplexityRegion
from astra.tracking.association import best_cluster_match, best_track_match
from astra.tracking.models import ArhacStatus, FourDArhac
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: Horizon that drives track identity/lifecycle in Milestone 5. Other
#: horizons in a regions_by_horizon dict are accepted (schema-stable
#: ahead of Milestone 6 forecasting, which will consume predicted
#: horizons attached to a confirmed track) but not yet consumed for
#: identity or status decisions -- see docs/milestone_5_tracking.md
#: "Why horizon 0 only".
_IDENTITY_HORIZON_MIN = 0


class TrackerEngine:
    """Links ``ComplexityRegion``s into persistent ``FourDArhac`` tracks.

    Stateful: one instance owns the current set of open tracks for its
    entire lifetime. Call ``update()`` once per poll cycle, in
    increasing simulation-time order.

    Example::

        tracker = TrackerEngine(config)
        while True:
            snapshot = reader.poll()
            if snapshot is None:
                continue
            clusters = cluster_engine.detect(snapshot)
            regions = complexity_engine.assess_many(clusters, snapshot)
            open_tracks = tracker.update({0: regions})
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise an empty tracker from shared config (thresholds)."""
        self._config = config
        self._open_tracks: Dict[str, FourDArhac] = {}
        self._missed_cycles: Dict[str, int] = {}
        _LOG.debug(
            "TrackerEngine initialised. jaccard_threshold=%.2f "
            "stale_cycles=%d confirm_cycles=%d trend_tolerance=%.2f",
            config.tracking_jaccard_threshold,
            config.tracking_stale_cycles,
            config.tracking_confirm_cycles,
            config.tracking_trend_tolerance,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self, regions_by_horizon: Dict[int, List[ComplexityRegion]]
    ) -> List[FourDArhac]:
        """Advance tracking state by one poll cycle.

        Args:
            regions_by_horizon: This cycle's fresh ``ComplexityRegion``s,
                keyed by ``horizon_min``. Horizon 0 (observed) drives
                identity/lifecycle for real tracks exactly as in the
                original Milestone 5 design; every other horizon is now
                also scanned for not-yet-observed hotspots -- see this
                module's docstring, "Provisional tracks".

        Returns:
            Every track touched by this call -- open tracks (new,
            promoted, extended, or newly opened as provisional) plus any
            freshly closed this cycle -- sorted by descending
            ``peak_complexity``. Tracks closed on a *previous* call are
            not returned again.
        """
        observed_regions = regions_by_horizon.get(_IDENTITY_HORIZON_MIN, [])
        matched_ids: Set[str] = set()

        for region in observed_regions:
            candidates = [
                track
                for arhac_id, track in self._open_tracks.items()
                if arhac_id not in matched_ids
            ]
            match = best_track_match(
                region.cluster, candidates, self._config.tracking_jaccard_threshold
            )
            if match is None:
                match = self._open_new_track(region)
            elif not match.track:
                # First-ever *real* observation for a track that was,
                # until now, PROVISIONAL -- a promotion, not a normal
                # extension (see `_promote_provisional_track`).
                self._promote_provisional_track(match, region)
            else:
                self._extend_track(match, region)
            matched_ids.add(match.arhac_id)

        all_matched_ids = self._detect_and_extend_provisional(
            regions_by_horizon, matched_ids
        )
        for arhac_id in all_matched_ids:
            self._missed_cycles[arhac_id] = 0

        freshly_closed = self._age_and_close_unmatched(all_matched_ids)
        self._assign_priority()

        result = list(self._open_tracks.values()) + freshly_closed
        return sorted(result, key=lambda track: -track.peak_complexity)

    def open_tracks(self) -> List[FourDArhac]:
        """Return the current set of open tracks without advancing state."""
        return list(self._open_tracks.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_new_track(self, region: ComplexityRegion) -> FourDArhac:
        """Create and register a new track from an unmatched region."""
        arhac_id = str(uuid.uuid4())
        track = FourDArhac(
            arhac_id=arhac_id,
            status=self._initial_status(1),
            track=[region],
            member_aircraft=region.cluster.member_callsigns,
            first_detected_cycle_s=region.computed_at_s,
            peak_complexity=region.complexity_score,
            peak_time_s=region.computed_at_s,
            confidence=self._confidence_for(1),
            last_updated_cycle_s=region.computed_at_s,
        )
        self._open_tracks[arhac_id] = track
        _LOG.debug(
            "New %s track %s opened at t=%.0fs (members=%s).",
            track.status,
            arhac_id,
            region.computed_at_s,
            sorted(region.cluster.member_callsigns),
        )
        return track

    def _extend_track(self, track: FourDArhac, region: ComplexityRegion) -> None:
        """Append a new observation to a track and advance its lifecycle."""
        previous_score = track.track[-1].complexity_score
        track.track.append(region)
        track.member_aircraft = (
            track.member_aircraft | region.cluster.member_callsigns
        )
        track.last_updated_cycle_s = region.computed_at_s
        track.confidence = self._confidence_for(len(track.track))

        if region.complexity_score > track.peak_complexity:
            track.peak_complexity = region.complexity_score
            track.peak_time_s = region.computed_at_s

        track.status = self._next_status(track, previous_score, region.complexity_score)

    def _promote_provisional_track(self, track: FourDArhac, region: ComplexityRegion) -> None:
        """First-ever real (horizon-0) observation for a PROVISIONAL track.

        Distinct from ``_extend_track``: a provisional track's ``track``
        list starts empty (see ``FourDArhac.track``'s docstring), so
        this seeds it with exactly one entry and assigns status via
        ``_initial_status`` -- the same rule a brand-new track gets --
        rather than the trend-based ``_next_status`` (which requires a
        *previous* real entry to compare against). This is deliberate:
        provisional (predicted-only) history should never count toward
        ``tracking_confirm_cycles``, so a track that was provisional for
        20 cycles still needs its own real detections to reach CONFIRMED,
        exactly as if it had just been opened fresh. ``arhac_id`` and
        ``first_detected_cycle_s`` (the original provisional detection
        time) are preserved -- only ``status``/``track``/peak/confidence
        change here.
        """
        track.track.append(region)
        track.member_aircraft = track.member_aircraft | region.cluster.member_callsigns
        track.last_updated_cycle_s = region.computed_at_s
        track.confidence = self._confidence_for(len(track.track))

        if region.complexity_score > track.peak_complexity:
            track.peak_complexity = region.complexity_score
            track.peak_time_s = region.computed_at_s

        track.status = self._initial_status(len(track.track))
        _LOG.debug(
            "Provisional track %s promoted to %s at t=%.0fs -- first flagged at "
            "t=%.0fs (%.0fs / %d predicted cycles in advance).",
            track.arhac_id,
            track.status,
            region.computed_at_s,
            track.first_detected_cycle_s,
            region.computed_at_s - track.first_detected_cycle_s,
            len(track.provisional_track),
        )

    def _open_provisional_track(
        self, region: ComplexityRegion, horizon_min: int
    ) -> FourDArhac:
        """Create and register a new PROVISIONAL track from a predicted-only cluster.

        Args:
            region: The predicted (non-zero horizon) ``ComplexityRegion``
                that triggered this -- becomes the sole entry in
                ``provisional_track`` (``track`` stays empty; see that
                field's docstring).
            horizon_min: Which horizon ``region`` came from, used only to
                back out the actual current simulation time
                (``region.computed_at_s`` is the *predicted* time,
                ``computed_at_s - horizon_min * 60`` is "now").
        """
        arhac_id = str(uuid.uuid4())
        now_s = region.computed_at_s - horizon_min * 60.0
        track = FourDArhac(
            arhac_id=arhac_id,
            status="PROVISIONAL",
            track=[],
            provisional_track=[region],
            member_aircraft=region.cluster.member_callsigns,
            first_detected_cycle_s=now_s,
            peak_complexity=region.complexity_score,
            peak_time_s=region.computed_at_s,
            confidence=(
                self._confidence_for(1) * self._config.tracking_provisional_confidence_multiplier
            ),
            last_updated_cycle_s=now_s,
        )
        self._open_tracks[arhac_id] = track
        _LOG.debug(
            "New PROVISIONAL track %s opened at t=%.0fs, predicting a hotspot "
            "~%d min ahead (members=%s, score=%.1f).",
            arhac_id,
            now_s,
            horizon_min,
            sorted(region.cluster.member_callsigns),
            region.complexity_score,
        )
        return track

    def _extend_provisional_track(
        self, track: FourDArhac, region: ComplexityRegion, horizon_min: int
    ) -> None:
        """Append one new predicted-only observation to a still-PROVISIONAL track."""
        track.provisional_track.append(region)
        track.member_aircraft = track.member_aircraft | region.cluster.member_callsigns
        track.last_updated_cycle_s = region.computed_at_s - horizon_min * 60.0

        if region.complexity_score > track.peak_complexity:
            track.peak_complexity = region.complexity_score
            track.peak_time_s = region.computed_at_s

        track.confidence = (
            self._confidence_for(len(track.provisional_track))
            * self._config.tracking_provisional_confidence_multiplier
        )

    def _detect_and_extend_provisional(
        self,
        regions_by_horizon: Dict[int, List[ComplexityRegion]],
        matched_ids: Set[str],
    ) -> Set[str]:
        """Scan every non-zero horizon for not-yet-observed hotspots.

        See this module's docstring, "Provisional tracks", for the full
        rationale. Horizons are scanned in ascending order so that, for
        a phenomenon visible across several horizons this cycle (e.g. a
        converging pair that clusters from horizon 20 through 40), the
        *smallest* (soonest) one is what opens or extends the track --
        maximum lead time, one entry per track per cycle.

        Args:
            regions_by_horizon: This cycle's fresh regions, as passed to
                ``update()``.
            matched_ids: Track ids already matched this cycle by the
                horizon-0 loop -- excluded from being treated as
                candidates for *new* provisional detections (a
                just-matched real track's own predicted future is not a
                new phenomenon), but still counted as "accounted for"
                for the return value.

        Returns:
            ``matched_ids`` unioned with every track id opened or
            extended by this scan -- the complete set of tracks that
            should be considered "seen" this cycle for staleness
            purposes.
        """
        all_matched = set(matched_ids)
        provisional_claimed_this_cycle: Set[str] = set()
        newly_opened_clusters: List = []
        threshold = self._config.tracking_provisional_min_complexity
        jaccard_threshold = self._config.tracking_jaccard_threshold

        for horizon_min in sorted(h for h in regions_by_horizon if h != _IDENTITY_HORIZON_MIN):
            for region in regions_by_horizon[horizon_min]:
                if region.complexity_score < threshold:
                    continue

                match = best_track_match(
                    region.cluster, list(self._open_tracks.values()), jaccard_threshold
                )
                if match is not None:
                    all_matched.add(match.arhac_id)
                    if (
                        match.status == "PROVISIONAL"
                        and match.arhac_id not in matched_ids
                        and match.arhac_id not in provisional_claimed_this_cycle
                    ):
                        self._extend_provisional_track(match, region, horizon_min)
                        provisional_claimed_this_cycle.add(match.arhac_id)
                    continue

                if best_cluster_match(region.cluster, newly_opened_clusters, jaccard_threshold) is not None:
                    # Already opened a provisional track for this same
                    # evolving cluster earlier in this cycle's scan (at
                    # a smaller horizon).
                    continue

                new_track = self._open_provisional_track(region, horizon_min)
                all_matched.add(new_track.arhac_id)
                provisional_claimed_this_cycle.add(new_track.arhac_id)
                newly_opened_clusters.append(region.cluster)

        return all_matched

    def _initial_status(self, length: int) -> ArhacStatus:
        """Status for a brand-new track with ``length`` entries so far."""
        if length >= self._config.tracking_confirm_cycles:
            return "CONFIRMED"
        return "CANDIDATE"

    def _next_status(
        self, track: FourDArhac, previous_score: float, new_score: float
    ) -> ArhacStatus:
        """Derive the next lifecycle status from the complexity trend.

        No time-based forecasting -- trend classification only
        (Milestone 6 adds onset/peak/dissipation *time* prediction on
        top of this). ``track.track`` already includes the new entry
        when this is called.
        """
        length = len(track.track)
        confirm_cycles = self._config.tracking_confirm_cycles

        if length < confirm_cycles:
            return "CANDIDATE"
        if track.status == "CANDIDATE":
            # This cycle's detection is the one that meets the
            # consecutive-detection requirement -- promote.
            return "CONFIRMED"

        tol = self._config.tracking_trend_tolerance
        delta = new_score - previous_score
        if delta > tol:
            return "GROWING"
        if delta < -tol:
            return "PEAK" if track.status == "GROWING" else "DISSIPATING"
        # Roughly flat: a plateau right after growth is the local max;
        # otherwise the track simply holds its current phase.
        return "PEAK" if track.status == "GROWING" else track.status

    def _age_and_close_unmatched(self, matched_ids: Set[str]) -> List[FourDArhac]:
        """Increment staleness for unmatched tracks; close and evict stale ones."""
        freshly_closed: List[FourDArhac] = []
        for arhac_id in list(self._open_tracks.keys()):
            if arhac_id in matched_ids:
                continue
            self._missed_cycles[arhac_id] = self._missed_cycles.get(arhac_id, 0) + 1
            if self._missed_cycles[arhac_id] >= self._config.tracking_stale_cycles:
                track = self._open_tracks.pop(arhac_id)
                del self._missed_cycles[arhac_id]
                track.status = "CLOSED"
                freshly_closed.append(track)
                _LOG.debug("Track %s closed (stale).", arhac_id)
        return freshly_closed

    def _assign_priority(self) -> None:
        """Recompute FMP triage priority (1 = highest peak_complexity)."""
        ranked = sorted(self._open_tracks.values(), key=lambda t: -t.peak_complexity)
        for rank, track in enumerate(ranked, start=1):
            track.priority = rank

    def _confidence_for(self, detection_count: int) -> float:
        """Placeholder confidence: ramps to 1.0 over tracking_confirm_cycles detections."""
        return min(1.0, detection_count / self._config.tracking_confirm_cycles)
