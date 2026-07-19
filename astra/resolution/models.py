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

#: How long a HEADING candidate's new heading is predicted to be held.
#: ``SUSTAINED`` (the original Milestone 7 behaviour): held indefinitely
#: through the evaluated horizon -- the only sensible model for an
#: aircraft with no known route to rejoin. ``VECTOR_AND_REJOIN``: held
#: for ``vector_duration_s``, then the aircraft is predicted to turn
#: back onto its own known route (see
#: ``astra.resolution.vector_rejoin``) -- used whenever the target
#: aircraft has a known route, since that is what a real controller
#: vectoring a route-following aircraft actually does. ``SPEED`` and
#: ``FLIGHT_LEVEL`` candidates are always ``SUSTAINED`` -- there is no
#: "rejoin" concept for a speed or level change.
ManeuverKind = Literal["SUSTAINED", "VECTOR_AND_REJOIN"]


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
            ``target_callsign`` without recomputing anything. For a
            ``VECTOR_AND_REJOIN`` candidate, ``target_callsign``'s entry
            at every horizon already reflects the two-phase manoeuvre
            (vector, then rejoin) -- see
            ``ResolutionEngine._apply_vector_rejoin_override``. ``None``
            only if evaluation could not run at all.
        maneuver_kind: ``SUSTAINED`` (held for the whole evaluated
            horizon) or ``VECTOR_AND_REJOIN`` (only ever set for
            ``HEADING`` candidates on a route-following aircraft --
            see ``ManeuverKind``).
        vector_duration_s: For a ``VECTOR_AND_REJOIN`` candidate, how
            long the vector is held before the predicted rejoin turn.
            ``None`` for ``SUSTAINED`` candidates.
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
    maneuver_kind: ManeuverKind = "SUSTAINED"
    vector_duration_s: Optional[float] = None


@dataclass(frozen=True)
class ResolutionLeg:
    """One aircraft's clearance within a (possibly joint) resolution.

    The single-aircraft building block a ``JointResolutionCandidate``
    is made of -- mirrors the relevant subset of ``ResolutionCandidate``
    (the clearance itself, not its own separately-scored effect, since
    a joint candidate's legs are only ever scored *together*, as one
    combined before/after complexity comparison -- see
    ``ResolutionEngine._build_joint_candidate``).
    """

    target_callsign: str
    clearance_type: ClearanceType
    delta_value: float
    maneuver_kind: ManeuverKind = "SUSTAINED"
    vector_duration_s: Optional[float] = None


@dataclass(frozen=True)
class JointResolutionCandidate:
    """A resolution applying clearances to 2+ cluster members at once.

    For clusters of 3+ aircraft, a single aircraft's own manoeuvre is
    often not enough to meaningfully de-densify the cluster -- e.g. a
    3-aircraft cluster resolved by moving only one aircraft still
    leaves the other two as close as they were. This candidate adjusts
    the primary aircraft (``legs[0]``, the same one/candidate
    ``ResolutionEngine`` would pick as its single-aircraft best) plus
    up to ``resolution_joint_max_targets - 1`` further members
    simultaneously, and scores the *combined* effect on the cluster in
    one before/after comparison -- not the sum of each leg's own
    separately-computed score. See
    ``ResolutionEngine._build_joint_candidate`` for exactly how each
    leg is chosen and how many members are lower a full lever search
    (only the primary) versus a cheaper speed-only search (the rest),
    and why.

    Attributes:
        legs: 2-3 ``ResolutionLeg``s, primary aircraft first. Applied
            simultaneously to one hypothetical snapshot.
        complexity_before / complexity_after / complexity_delta_norm /
        deviation_cost_norm / fuel_cost_proxy_norm / domino_cost_norm /
        resolution_score / complexity_after_components /
        complexity_before_components: Same meaning as the matching
            ``ResolutionCandidate`` fields, computed for the *combined*
            (all-legs-applied) hypothetical snapshot rather than any
            one leg in isolation. ``deviation_cost_norm`` and
            ``fuel_cost_proxy_norm`` are the sum of each leg's own
            normalised cost (more aircraft moved costs more, by
            design).
    """

    legs: List[ResolutionLeg]
    complexity_before: float
    complexity_after: Optional[float]
    complexity_delta_norm: float
    deviation_cost_norm: float
    fuel_cost_proxy_norm: float
    resolution_score: float
    domino_cost_norm: float = 0.0
    complexity_after_components: Optional[Dict[str, float]] = None
    complexity_before_components: Optional[Dict[str, float]] = None


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
            (e.g. no member aircraft resolvable in the snapshot). Every
            entry here is single-aircraft, exactly as in the original
            Milestone 7 design.
        evaluated_horizon_min: The single prediction horizon (minutes)
            every candidate was evaluated at -- the one closest to
            ``track.predicted_onset_s`` (see OQ-5).
        joint_candidate: A multi-aircraft ``JointResolutionCandidate``,
            present only when the track's matched cluster has 3+
            members (see ``ResolutionEngine._build_joint_candidate``).
            Purely additive: ``candidates`` is unchanged whether or not
            a joint candidate was built, so existing single-aircraft
            consumers of this class need no changes.
    """

    track: FourDArhac
    candidates: List[ResolutionCandidate]
    evaluated_horizon_min: int
    joint_candidate: Optional[JointResolutionCandidate] = None

    def best(self) -> Optional[ResolutionCandidate]:
        """Return the top-ranked single-aircraft candidate, or ``None`` if there are none."""
        return self.candidates[0] if self.candidates else None

    def best_overall(self):
        """Return whichever of ``best()`` and ``joint_candidate`` scores higher.

        Returns:
            A ``ResolutionCandidate``, a ``JointResolutionCandidate``,
            or ``None`` if neither is available. Callers that need to
            know which kind they got back can check
            ``isinstance(result, JointResolutionCandidate)`` or look
            for a ``legs`` attribute.
        """
        best_single = self.best()
        if self.joint_candidate is None:
            return best_single
        if best_single is None:
            return self.joint_candidate
        return (
            self.joint_candidate
            if self.joint_candidate.resolution_score > best_single.resolution_score
            else best_single
        )

    def __len__(self) -> int:
        """Number of single-aircraft candidates generated for this track."""
        return len(self.candidates)
