"""
AI resolution framework data model (Milestone 7).

Defines ``ResolutionCandidate`` and ``ResolutionSet``: a composed type
(``ResolutionSet`` *has-a* ``FourDArhac`` plus its ranked candidates)
rather than a field bolted onto ``FourDArhac`` itself -- see
docs/milestone_7_resolution_design_review.md OQ-1. Mirrors
``ComplexityRegion``'s composition over ``Cluster`` (Milestone 4).
"""

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from astra.tracking.models import FourDArhac
from astra.trajectory.models import PredictionResult

#: The clearance types Milestone 7 generates candidates for. Direct-to is
#: deferred -- see docs/milestone_7_resolution_design_review.md OQ-2.
ClearanceType = Literal["SPEED", "FLIGHT_LEVEL", "HEADING"]


@dataclass(frozen=True)
class ResolutionCandidate:
    """One hypothetical ATC clearance and its scored effect on a track.

    Attributes:
        clearance_type: Which lever this candidate adjusts.
        target_callsign: The member aircraft the clearance is issued to
            (the track's highest-conflict-contributing aircraft -- see
            ``astra.resolution.candidates``).
        delta_value: Signed magnitude of the adjustment, in the
            clearance's natural unit (kt / ft / deg). Positive or
            negative per the +/- step in ``ASTRAConfig``.
        complexity_before: ``complexity_score`` of the track's matched
            region at the evaluated horizon, on the real (unmodified)
            predicted snapshot.
        complexity_after: ``complexity_score`` of the hypothetical region
            at the same horizon, on the snapshot with this candidate's
            clearance applied. ``None`` if the hypothetical cluster could
            not be re-associated back to the track (see OQ-3) -- such a
            candidate is scored with a zero complexity_delta rather than
            discarded, so it still surfaces its deviation/fuel cost.
        complexity_delta_norm: ``(before - after) / before``, clipped to
            ``[0, 1]``. ``0.0`` if ``complexity_before`` is ``0`` or
            ``complexity_after`` is ``None``.
        deviation_cost_norm: Normalised magnitude of the clearance itself
            (e.g. ``|delta_value| / max_step``) -- a proxy for
            operational cost, not a real route-deviation distance (no
            flight-plan leg data available; see OQ-4).
        fuel_cost_proxy_norm: Crude fuel-cost proxy in ``[0, 1]`` --
            altitude-change magnitude for flight-level candidates, else
            ``0.0``. Explicitly not a real fuel-burn model (see OQ-4).
        domino_cost_norm: Normalised penalty in ``[0, 1]`` for
            "domino-effect" side effects -- new or worsened hotspots the
            candidate's manoeuvre introduces *elsewhere* in the traffic
            picture (outside the track being resolved), evaluated at the
            same horizon. ``0.0`` if the manoeuvre introduces no such
            side effect. See ``ResolutionEngine._domino_cost``.
        resolution_score: Weighted composite --
            ``w_complexity * complexity_delta_norm
            - w_domino * domino_cost_norm
            - w_deviation * deviation_cost_norm
            - w_fuel * fuel_cost_proxy_norm``. Higher is better.
        complexity_after_components: Per-component breakdown of the
            hypothetical region at ``complexity_after`` (same keys as
            ``ComplexityRegion.components``), for before/after HMI bar
            charts. ``None`` under the same conditions as
            ``complexity_after``.
        complexity_before_components: Per-component breakdown of the
            real, unmodified region at ``complexity_before`` -- the
            "before" side of the same bar chart.
        hypothetical_prediction: The full re-predicted ``PredictionResult``
            for the snapshot with this candidate's clearance applied
            (every configured horizon, not just the evaluated one) --
            lets the HMI plot a "what-if" trajectory for
            ``target_callsign`` without recomputing anything. ``None``
            only if evaluation could not run at all.
    """

    clearance_type: ClearanceType
    target_callsign: str
    delta_value: float
    complexity_before: float
    complexity_after: Optional[float]
    complexity_delta_norm: float
    deviation_cost_norm: float
    fuel_cost_proxy_norm: float
    resolution_score: float
    domino_cost_norm: float = 0.0
    complexity_after_components: Optional[Dict[str, float]] = None
    complexity_before_components: Optional[Dict[str, float]] = None
    hypothetical_prediction: Optional[PredictionResult] = None


@dataclass(frozen=True)
class ResolutionSet:
    """A track together with its ranked candidate clearances.

    Composition over ``FourDArhac``, analogous to ``ComplexityRegion``'s
    composition over ``Cluster`` -- see OQ-1. Read-only: produced fresh
    each poll cycle by ``ResolutionEngine.resolve()``, never mutated.

    Attributes:
        track: The ``FourDArhac`` these candidates were generated for.
        candidates: Ranked descending by ``resolution_score`` (best
            first). May be empty if no candidate could be constructed
            (e.g. no member aircraft resolvable in the snapshot).
        evaluated_horizon_min: The single prediction horizon (minutes)
            every candidate was evaluated at -- the one closest to
            ``track.predicted_onset_s`` (see OQ-5).
    """

    track: FourDArhac
    candidates: List[ResolutionCandidate]
    evaluated_horizon_min: int

    def best(self) -> Optional[ResolutionCandidate]:
        """Return the top-ranked candidate, or ``None`` if there are none."""
        return self.candidates[0] if self.candidates else None

    def __len__(self) -> int:
        """Number of candidates generated for this track."""
        return len(self.candidates)
