"""
4DARHAC tracking data model (Milestone 5).

Defines the persistent ``FourDArhac`` object -- the first genuinely
*stateful* domain object in the ASTRA pipeline, surviving across poll
cycles rather than being rebuilt from scratch every cycle like
``Cluster`` (Milestone 3) and ``ComplexityRegion`` (Milestone 4).

See docs/architecture.md §6.2 for the original domain-model sketch,
docs/milestone_5_tracking.md for Milestone 5's as-built design
decisions, and docs/milestone_6_forecast.md for Milestone 6's (the
forecast fields below are populated by
``astra.forecast.engine.ForecastEngine``, not by ``TrackerEngine``).
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
    ``FourDArhac`` is updated in place: ``TrackerEngine`` (Milestone 5)
    owns identity/membership/status; ``ForecastEngine`` (Milestone 6)
    owns the forecast fields below, called afterward in the same poll
    cycle. Instances are owned by exactly one ``TrackerEngine``; nothing
    outside ``astra.tracking``/``astra.forecast`` should mutate one
    directly.

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
        predicted_onset_s: Set by ``ForecastEngine`` -- the estimated
            future time ``complexity_score`` first crosses
            ``forecast_onset_threshold``, interpolated from this cycle's
            matched predicted horizons. ``None`` if the track is already
            above the threshold (onset already happened), if the trend
            never crosses it within the available horizons, or if fewer
            than ``forecast_min_matched_horizons`` horizons matched this
            cycle (insufficient data -- see docs/milestone_6_forecast.md).
            Always ``None`` for ``CANDIDATE``/``CLOSED`` tracks (not
            forecast at all).
        peak_complexity: Highest ``complexity_score`` observed *or*
            predicted so far -- ``ForecastEngine`` may raise this (and
            ``peak_time_s`` alongside it) if a matched predicted horizon
            exceeds the previous value; see ``predicted_peak_time_s``.
        peak_time_s: ``valid_at_s`` (if observed) or predicted time (if
            raised by ``ForecastEngine``) of the entry that produced
            ``peak_complexity``.
        predicted_dissipation_s: Set by ``ForecastEngine`` -- the
            estimated future time ``complexity_score`` first crosses
            ``forecast_dissipation_threshold`` on the way down. Same
            ``None`` conditions as ``predicted_onset_s``.
        predicted_peak_time_s: Set by ``ForecastEngine`` -- the time of
            the highest-scoring *matched predicted horizon* this cycle,
            only if it exceeds the previously-known ``peak_complexity``
            (i.e. a higher peak is expected in the future than anything
            observed so far). ``None`` if no future horizon is expected
            to exceed the current peak, or under the same insufficient-
            data conditions as ``predicted_onset_s``.
        confidence: Composite forecast confidence in ``[0, 1]``.
            ``TrackerEngine`` seeds this with a detection-count ramp
            placeholder (Milestone 5); ``ForecastEngine`` (Milestone 6)
            multiplies that by horizon coverage and a horizon-distance
            decay term. A documented heuristic, not a statistically
            calibrated probability -- see docs/milestone_6_forecast.md.
        priority: FMP triage rank among currently open tracks (1 =
            highest ``peak_complexity``). Owned by ``TrackerEngine``,
            recomputed every ``update()`` call. Unchanged by
            ``ForecastEngine`` -- severity-only ranking is kept separate
            from forecast urgency, see docs/milestone_6_forecast.md.
        forecast_urgency_rank: FMP triage rank by soonest
            ``predicted_onset_s`` among tracks forecast this cycle (1 =
            soonest onset). Owned by ``ForecastEngine``, recomputed every
            ``forecast_many()`` call; ``None`` for tracks with no
            predicted onset this cycle (already active, no crossing
            found, or insufficient matched horizons) and for tracks not
            forecast at all. Deliberately separate from ``priority`` --
            see docs/milestone_6_forecast.md.
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
