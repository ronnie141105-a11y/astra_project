"""
Candidate clearance generation (Milestone 7).

Pure functions, no engine state -- mirrors ``astra.hotspot.distance`` /
``astra.tracking.association``'s pattern of small, independently-testable
modules feeding the stateful/orchestrating engine
(``astra.resolution.engine.ResolutionEngine``). See
docs/milestone_7_resolution_design_review.md OQ-2.

Direct-to candidates are out of scope (OQ-2): ``MockConnector`` has no
``DCT``-equivalent stack command, so only speed / flight-level / heading
are generated here.
"""

import dataclasses
from itertools import combinations
from typing import List, NamedTuple, Optional, Tuple

from astra.complexity.conflict import classify_conflict, closest_point_of_approach
from astra.complexity.models import ComplexityRegion
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.models import ClearanceType
from astra.utils.config import ASTRAConfig


class CandidateSpec(NamedTuple):
    """One not-yet-scored candidate: a lever, its target, and the
    hypothetical snapshot with that lever applied."""

    clearance_type: ClearanceType
    target_callsign: str
    delta_value: float
    hypothetical_snapshot: TrafficSnapshot


def select_target_aircraft(
    cluster: Cluster, snapshot: TrafficSnapshot, config: ASTRAConfig
) -> Optional[AircraftState]:
    """Pick the cluster member to apply candidate clearances to.

    Proxy for "highest-complexity-score-contributing aircraft" (OQ-2):
    ``ComplexityRegion`` has no per-aircraft score breakdown, so this
    reuses the same pairwise MTCA/LTCA machinery
    ``ComplexityEngine``/``count_conflicts`` already runs (see
    ``astra.complexity.conflict``) and picks the member involved in the
    most conflict pairs. If no member is in any conflict pair (a
    density/diversity-only cluster), falls back to the alphabetically
    first callsign -- a documented, deterministic simplification, since
    no other per-aircraft contribution signal exists in the pipeline.

    Args:
        cluster: The track's most recent (or hypothetical) cluster.
        snapshot: Snapshot to resolve member callsigns against.
        config: Shared config (MTCA/LTCA thresholds).

    Returns:
        The selected member's current ``AircraftState``, or ``None`` if
        no member callsign resolves against ``snapshot``.
    """
    members = [
        state
        for state in (snapshot.get(cs) for cs in cluster.member_callsigns)
        if state is not None
    ]
    if not members:
        return None
    if len(members) == 1:
        return members[0]

    conflict_counts = {ac.callsign: 0 for ac in members}
    for ac_a, ac_b in combinations(members, 2):
        approach = closest_point_of_approach(
            cluster.centroid_lat, cluster.centroid_lon, ac_a, ac_b
        )
        if classify_conflict(approach, config) is not None:
            conflict_counts[ac_a.callsign] += 1
            conflict_counts[ac_b.callsign] += 1

    if any(conflict_counts.values()):
        best_callsign = max(
            conflict_counts, key=lambda cs: (conflict_counts[cs], cs), default=None
        )
    else:
        best_callsign = min(conflict_counts)
    return snapshot.get(best_callsign)


def heading_lever_applicable(region: ComplexityRegion) -> bool:
    """True if a track's complexity is at least partly conflict-driven.

    Heading candidates are only generated for MTCA/LTCA-driven
    complexity (OQ-2), since heading is the most direct lever on a
    predicted conflict specifically -- not on density/diversity drivers.

    Args:
        region: The track's matched ``ComplexityRegion`` at the
            evaluated horizon.

    Returns:
        ``True`` if ``region.components`` shows at least one MTCA/LTCA
        pair.
    """
    return (
        region.components.get("mtca_count", 0.0) + region.components.get("ltca_count", 0.0)
        > 0.0
    )


def _apply_clearance(
    snapshot: TrafficSnapshot,
    target_callsign: str,
    clearance_type: ClearanceType,
    delta_value: float,
) -> TrafficSnapshot:
    """Return a new ``TrafficSnapshot`` with one aircraft's state adjusted.

    Never mutates ``snapshot`` -- builds a new aircraft dict and a new
    ``AircraftState`` via ``dataclasses.replace`` (both are Milestone 1
    conventions), per the frozen-state safety requirement in
    docs/milestone_7_resolution_design_review.md (risk table, §9).
    """
    target = snapshot.aircraft[target_callsign]
    if clearance_type == "SPEED":
        new_target = dataclasses.replace(
            target, ground_speed_kt=max(0.0, target.ground_speed_kt + delta_value)
        )
    elif clearance_type == "FLIGHT_LEVEL":
        new_target = dataclasses.replace(target, altitude_ft=target.altitude_ft + delta_value)
    else:  # "HEADING"
        new_target = dataclasses.replace(
            target, heading_deg=(target.heading_deg + delta_value) % 360.0
        )
    new_aircraft = dict(snapshot.aircraft)
    new_aircraft[target_callsign] = new_target
    return TrafficSnapshot(timestamp_s=snapshot.timestamp_s, aircraft=new_aircraft)


def generate_candidates(
    region: ComplexityRegion, snapshot: TrafficSnapshot, config: ASTRAConfig
) -> List[CandidateSpec]:
    """Build the fixed candidate set for one track's matched region.

    One step size per lever, but *both* signed directions (increase and
    decrease) -- widening the search space from Milestone 7's original
    single-direction convention (no per-conflict-geometry sign selection
    exists in the pipeline, so both directions are tried and scored
    rather than guessed). Speed and flight-level candidates are always
    generated in both directions; heading is added in both directions
    only if ``heading_lever_applicable`` (OQ-2). No randomness and no
    optimisation library -- this remains an exhaustive, deterministic
    enumeration over the fixed step sizes already in ``ASTRAConfig``.

    Args:
        region: The track's matched ``ComplexityRegion`` (real,
            unmodified) at the evaluated horizon.
        snapshot: The current *observed* ``TrafficSnapshot`` (the
            candidate's clearance is applied to this before being
            re-predicted forward to the evaluated horizon).
        config: Shared config (step sizes).

    Returns:
        Up to 6 ``CandidateSpec``s (4 without a conflict driver, 6 with
        one), or ``[]`` if no member of ``region.cluster`` resolves
        against ``snapshot``.
    """
    target = select_target_aircraft(region.cluster, snapshot, config)
    if target is None:
        return []

    lever_steps: List[Tuple[ClearanceType, float]] = [
        ("SPEED", config.resolution_speed_step_kt),
        ("SPEED", -config.resolution_speed_step_kt),
        ("FLIGHT_LEVEL", config.resolution_altitude_step_ft),
        ("FLIGHT_LEVEL", -config.resolution_altitude_step_ft),
    ]
    if heading_lever_applicable(region):
        lever_steps.append(("HEADING", config.resolution_heading_step_deg))
        lever_steps.append(("HEADING", -config.resolution_heading_step_deg))

    return [
        CandidateSpec(
            clearance_type,
            target.callsign,
            delta_value,
            _apply_clearance(snapshot, target.callsign, clearance_type, delta_value),
        )
        for clearance_type, delta_value in lever_steps
    ]
