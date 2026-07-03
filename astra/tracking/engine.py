"""
4DARHAC tracking engine (Milestone 5).

``TrackerEngine`` is the one genuinely *stateful* component in the ASTRA
pipeline: unlike ``ClusterEngine``/``ComplexityEngine`` (stateless after
construction), it holds the current set of open ``FourDArhac`` tracks
across calls, and each ``update()`` call is seeded by that state from the
previous poll cycle -- the self-loop in docs/architecture.md §6.4.

See docs/milestone_5_tracking.md for the full design write-up.
"""

import uuid
from typing import Dict, List, Set

from astra.complexity.models import ComplexityRegion
from astra.tracking.association import best_track_match
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
                keyed by ``horizon_min``. Only horizon 0 (observed)
                drives Milestone 5 track identity/lifecycle -- see
                docs/milestone_5_tracking.md.

        Returns:
            Every track touched by this call -- open tracks (new and
            updated) plus any freshly closed this cycle -- sorted by
            descending ``peak_complexity``. Tracks closed on a *previous*
            call are not returned again.
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
            else:
                self._extend_track(match, region)
            matched_ids.add(match.arhac_id)
            self._missed_cycles[match.arhac_id] = 0

        freshly_closed = self._age_and_close_unmatched(matched_ids)
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
