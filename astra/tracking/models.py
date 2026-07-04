"""
4DARHAC tracking data model (Milestone 5).

Defines the persistent ``FourDArhac`` object -- the first genuinely
*stateful* domain object in the ASTRA pipeline, surviving across poll
cycles rather than being rebuilt from scratch every cycle like
``Cluster`` (Milestone 3) and ``ComplexityRegion`` (Milestone 4).

See docs/architecture.md §6.2 for the original domain-model sketch and
docs/milestone_5_tracking.md for the as-built design decisions.
"""

from dataclasses import dataclass, field
from typing import FrozenSet, List, Literal, Optional

from astra.complexity.models import ComplexityRegion

#: Lifecycle status of a tracked 4DARHAC.
#:
#: CANDIDATE  -- detected, not yet confirmed (damps single-cycle noise).
#: CONFIRMED  -- seen for `tracking_confirm_cycles` consecutive cycles.
#: GROWING    -- complexity_score rising cycle over cycle.
#: PEAK       -- growth just flattened or reversed; the local maximum.
#: DISSIPATING -- complexity_score falling cycle over cycle.
#: CLOSED     -- not re-observed for `tracking_stale_cycles` cycles.
#:
#: Derived mechanically from the trend of complexity_score across the
#: track's most recent entries (see
#: ``astra.tracking.engine.TrackerEngine._next_status``) plus the
#: staleness check for CLOSED. No time-based forecasting -- trend
#: classification only; onset/peak/dissipation *time* prediction and
#: calibrated confidence belong to Milestone 6, layered on top of this.
ArhacStatus = Literal[
    "CANDIDATE", "CONFIRMED", "GROWING", "PEAK", "DISSIPATING", "CLOSED"
]


@dataclass
class FourDArhac:
    """A persistent 4D Area of Relatively High ATC Complexity.

    Mutable -- unlike ``Cluster`` and ``ComplexityRegion``, a
    ``FourDArhac`` is updated in place by ``TrackerEngine`` as new
    observations arrive across poll cycles, rather than being
    reconstructed each time. Instances are owned by exactly one
    ``TrackerEngine``; nothing outside ``astra.tracking`` should mutate
    one directly.

    Attributes:
        arhac_id: Stable identifier assigned at first detection;
            persists for the life of the track.
        status: Current lifecycle stage (see ``ArhacStatus``).
        track: ``ComplexityRegion`` history for this ARHAC, ordered by
            ``valid_at_s``. Milestone 5 populates this from observed
            (``horizon_min == 0``) regions only -- see
            docs/milestone_5_tracking.md "Why horizon 0 only".
        member_aircraft: Union of ``member_callsigns`` across every
            entry in ``track`` (membership can change cycle to cycle as
            aircraft join or leave the area).
        first_detected_cycle_s: Absolute sim time of the first entry.
        predicted_onset_s: Reserved for Milestone 6 (4DARHAC forecast).
            Always ``None`` until that milestone is built.
        peak_complexity: Highest ``complexity_score`` observed so far
            across ``track``.
        peak_time_s: ``valid_at_s`` of the entry that produced
            ``peak_complexity``.
        predicted_dissipation_s: Reserved for Milestone 6. Always
            ``None`` until that milestone is built.
        predicted_peak_time_s: Set by ``ForecastEngine`` (Milestone 6) --
            the time of the highest-scoring matched predicted horizon
            this cycle, only if it exceeds the previously-known
            ``peak_complexity``. ``None`` otherwise -- see
            docs/milestone_6_forecast.md OQ-2.
        confidence: Placeholder strength signal in ``[0, 1]`` that ramps
            up with consecutive detections (see
            ``TrackerEngine._confidence_for``). Not a calibrated
            forecast confidence -- that is Milestone 6's job.
        priority: FMP triage rank among currently open tracks (1 =
            highest ``peak_complexity``). Recomputed every
            ``update()`` call.
        forecast_urgency_rank: Set by ``ForecastEngine`` (Milestone 6) --
            FMP triage rank by soonest ``predicted_onset_s`` among tracks
            forecast this cycle (1 = soonest onset). ``None`` if the
            track has no predicted onset this cycle. Deliberately kept
            separate from ``priority`` -- see
            docs/milestone_6_forecast.md OQ-4.
        last_updated_cycle_s: ``valid_at_s`` of the most recent
            extending observation; compared against
            ``tracking_stale_cycles`` to detect staleness.
    """

    arhac_id: str
    status: ArhacStatus
    track: List[ComplexityRegion] = field(default_factory=list)
    member_aircraft: FrozenSet[str] = field(default_factory=frozenset)
    first_detected_cycle_s: float = 0.0
    predicted_onset_s: Optional[float] = None
    peak_complexity: float = 0.0
    peak_time_s: Optional[float] = None
    predicted_dissipation_s: Optional[float] = None
    predicted_peak_time_s: Optional[float] = None
    confidence: float = 0.0
    priority: int = 0
    forecast_urgency_rank: Optional[int] = None
    last_updated_cycle_s: float = 0.0

    def __len__(self) -> int:
        """Number of distinct aircraft that have ever been part of this track."""
        return len(self.member_aircraft)
