"""
Candidate clearance generation (Milestone 7, extended with route-aware
vector-and-rejoin heading candidates and a wider step search).

Pure functions, no engine state -- mirrors ``astra.hotspot.distance`` /
``astra.tracking.association``'s pattern of small, independently-testable
modules feeding the stateful/orchestrating engine
(``astra.resolution.engine.ResolutionEngine``). See
docs/milestone_7_resolution_design_review.md OQ-2.

Direct-to candidates are out of scope (OQ-2): ``MockConnector`` has no
``DCT``-equivalent stack command, so only speed / flight-level / heading
are generated here. A HEADING candidate for an aircraft with a *known*
route is, however, no longer generated as an indefinite heading hold --
see ``heading_lever_specs`` -- since that both flies the aircraft away
from its clearance forever (unrealistic) and, per
``scenarios/arrival_sequencing_demo.py``'s finding, does not even match
what ``MockConnector`` would actually do to a route-following aircraft
(it recomputes heading toward the next waypoint every tick regardless).
Instead, a bounded "vector, then predicted rejoin" candidate is
generated, using ``astra.resolution.vector_rejoin`` for the second
phase -- the closest deterministic proxy for a direct-to clearance this
project has, without inventing a stack command the simulator does not
support.
"""

import dataclasses
from itertools import combinations
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

from astra.complexity.conflict import classify_conflict, closest_point_of_approach
from astra.complexity.models import ComplexityRegion
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.resolution.models import ClearanceType
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm

#: `route_provider`'s signature -- matches `StateReader.get_route` /
#: `astra.trajectory.route_engine.RouteProvider` exactly, so a bound
#: method can be passed straight through unmodified.
RouteProvider = Callable[[str], Optional[List[Tuple[float, float]]]]


class CandidateSpec(NamedTuple):
    """One not-yet-scored candidate: a lever, its target, and the
    hypothetical snapshot with that lever applied.

    Attributes:
        clearance_type: Which lever this candidate adjusts.
        target_callsign: The aircraft this candidate's clearance is
            issued to.
        delta_value: Signed magnitude of the adjustment (kt / ft / deg).
        hypothetical_snapshot: A new ``TrafficSnapshot`` with the
            clearance already applied to ``target_callsign``'s current
            state -- Phase 1's starting condition (for a
            ``VECTOR_AND_REJOIN`` candidate) or the whole picture (for
            a ``SUSTAINED`` one).
        vector_duration_s: ``None`` for a ``SUSTAINED`` candidate
            (``ResolutionEngine`` predicts it with whichever trajectory
            engine it was built with, no special handling needed). Set
            for a ``VECTOR_AND_REJOIN`` heading candidate -- how long
            the vectored heading in ``hypothetical_snapshot`` is held
            before ``ResolutionEngine`` predicts the target turning
            back onto ``rejoin_route``.
        rejoin_route: The target's real known remaining route, to
            rejoin after ``vector_duration_s`` -- ``None`` unless
            ``vector_duration_s`` is set.
    """

    clearance_type: ClearanceType
    target_callsign: str
    delta_value: float
    hypothetical_snapshot: TrafficSnapshot
    vector_duration_s: Optional[float] = None
    rejoin_route: Optional[List[Tuple[float, float]]] = None


def select_target_aircraft_ranked(
    cluster: Cluster, snapshot: TrafficSnapshot, config: ASTRAConfig
) -> List[AircraftState]:
    """Rank every resolvable cluster member by how good a resolution target it is.

    Generalises the original single-target selection (see
    ``select_target_aircraft``, now a thin wrapper around this) so
    ``ResolutionEngine`` can also build joint (multi-aircraft)
    candidates for larger clusters -- see
    ``ResolutionEngine._build_joint_candidate``.

    Same primary proxy signal as before (OQ-2): members involved in more
    pairwise MTCA/LTCA conflicts rank first. Ties -- including the
    no-conflict-pairs case entirely (a density/diversity-only cluster,
    where every member's conflict count is 0) -- now break by distance
    from the cluster centroid, closest first, rather than purely
    alphabetically: the most central member is the one whose own
    movement does the most to actually de-densify the cluster (nudging
    an aircraft already near the edge barely changes the cluster's
    density/extent; nudging the one nearest the centroid does).
    Alphabetical callsign order is now only the final, last-resort
    tie-break, for aircraft equidistant from the centroid (e.g. a
    perfectly symmetric geometry) -- kept so the ranking stays fully
    deterministic even then. Previously, ties broke on callsign alone,
    which for the all-zero-conflict-count case meant target selection
    for density-only clusters was not grounded in anything about the
    actual traffic picture (see docs/backend_improvements_backlog.md
    item 3).

    Args:
        cluster: The track's most recent (or hypothetical) cluster.
        snapshot: Snapshot to resolve member callsigns against.
        config: Shared config (MTCA/LTCA thresholds).

    Returns:
        All resolvable members, best-target-first. Empty if no member
        callsign resolves against ``snapshot``.
    """
    members = [
        state
        for state in (snapshot.get(cs) for cs in cluster.member_callsigns)
        if state is not None
    ]
    if not members:
        return []
    if len(members) == 1:
        return members

    conflict_counts = {ac.callsign: 0 for ac in members}
    for ac_a, ac_b in combinations(members, 2):
        approach = closest_point_of_approach(
            cluster.centroid_lat, cluster.centroid_lon, ac_a, ac_b
        )
        if classify_conflict(approach, config) is not None:
            conflict_counts[ac_a.callsign] += 1
            conflict_counts[ac_b.callsign] += 1

    centroid_distance_nm = {
        ac.callsign: haversine_distance_nm(
            ac.lat, ac.lon, cluster.centroid_lat, cluster.centroid_lon
        )
        for ac in members
    }

    by_callsign = {ac.callsign: ac for ac in members}
    ranked_callsigns = sorted(
        conflict_counts,
        key=lambda cs: (-conflict_counts[cs], centroid_distance_nm[cs], cs),
    )
    return [by_callsign[cs] for cs in ranked_callsigns]


def select_target_aircraft(
    cluster: Cluster, snapshot: TrafficSnapshot, config: ASTRAConfig
) -> Optional[AircraftState]:
    """Pick the single best cluster member to apply candidate clearances to.

    Thin wrapper around ``select_target_aircraft_ranked`` -- kept as its
    own function since "the one primary target" is still the common
    case (2-aircraft clusters, and the primary leg of a joint
    candidate) and every existing call site expects a single
    ``Optional[AircraftState]``.

    Returns:
        The top-ranked member's current ``AircraftState``, or ``None``
        if no member callsign resolves against ``snapshot``.
    """
    ranked = select_target_aircraft_ranked(cluster, snapshot, config)
    return ranked[0] if ranked else None


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


def matches_rvsm_parity(heading_deg: float, altitude_ft: float) -> bool:
    """True if `altitude_ft` is a conventionally-correct flight level for
    an aircraft tracking `heading_deg` -- semicircular (odd/east,
    even/west) RVSM flight-level allocation.

    Real airspace assigns flight levels by direction of flight so that
    same-direction traffic is vertically staggered from opposite-direction
    traffic: eastbound tracks (magnetic 000-179) fly odd flight levels
    (FL290, 330, 370, 410, ...), westbound tracks (180-359) fly even ones
    (FL280, 320, 360, 400, ...) -- simplified here to whole-thousands
    parity (`round(altitude_ft / 1000)` odd/even) rather than modelling
    the exact ICAO RVSM table (2000 ft spacing above FL290, 1000 ft
    below), consistent with this scoring model's existing "crude proxy,
    not a certified system" framing elsewhere (see OQ-4). Good enough to
    tell a FLIGHT_LEVEL candidate's *resulting* level apart from a
    same-direction one that would actually be assigned in practice.

    Args:
        heading_deg: The aircraft's track direction, degrees (any range;
            normalised internally).
        altitude_ft: The altitude to check -- typically a candidate's
            *resulting* altitude (current + delta), not its current one.

    Returns:
        ``True`` if the whole-thousands parity of `altitude_ft` matches
        the expected one for `heading_deg`'s hemisphere.
    """
    eastbound = (heading_deg % 360.0) < 180.0
    is_odd_thousand = round(altitude_ft / 1000.0) % 2 == 1
    return is_odd_thousand == eastbound


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


def apply_clearances(
    snapshot: TrafficSnapshot,
    legs: Sequence[Tuple[str, ClearanceType, float]],
) -> TrafficSnapshot:
    """Apply several aircraft's clearances to one snapshot, in order.

    Thin, public wrapper chaining ``_apply_clearance`` once per leg --
    used to build a *joint* hypothetical snapshot (multiple aircraft
    adjusted simultaneously) from a plain ``TrafficSnapshot``, without
    ``ResolutionEngine`` needing to touch this module's private
    ``_apply_clearance`` directly. Each leg targets a different
    aircraft in every call site today, so application order does not
    matter, but is preserved (first-to-last) for determinism regardless.

    Args:
        snapshot: The starting (real, observed) snapshot.
        legs: ``(target_callsign, clearance_type, delta_value)`` tuples.

    Returns:
        A new ``TrafficSnapshot`` with every leg applied.
    """
    result = snapshot
    for target_callsign, clearance_type, delta_value in legs:
        result = _apply_clearance(result, target_callsign, clearance_type, delta_value)
    return result


def _step_magnitudes(base_step: float, config: ASTRAConfig) -> List[float]:
    """Every positive step magnitude to try for one lever (OQ-2, widened).

    ``base_step * m`` for each configured
    ``config.resolution_step_multipliers`` -- e.g. ``[1.0, 2.0]``
    produces both a "small" and a "large" adjustment. Deduplicated and
    sorted so a multiplier list with repeats or out-of-order values
    (e.g. user config error) still produces a sane, small candidate set.
    """
    magnitudes = sorted({base_step * m for m in config.resolution_step_multipliers})
    return [m for m in magnitudes if m > 0]


def heading_lever_specs(
    region: ComplexityRegion,
    snapshot: TrafficSnapshot,
    config: ASTRAConfig,
    target: AircraftState,
    route: Optional[List[Tuple[float, float]]],
) -> List[CandidateSpec]:
    """Build this target's HEADING candidates, sustained or vector-and-rejoin.

    Only called when ``heading_lever_applicable(region)`` (OQ-2). Two
    behaviours, chosen per-target rather than globally, since whether a
    heading candidate should be a bounded vector depends on whether
    *this* aircraft has a known route, not on the cluster as a whole:

    * ``route`` is truthy: one ``VECTOR_AND_REJOIN`` candidate per
      (sign, magnitude) combination -- see module docstring for why an
      indefinite heading hold is both unrealistic and, per
      ``arrival_sequencing_demo.py``, not actually achievable on a
      route-following aircraft in this simulator.
    * ``route`` is falsy (``None``/empty -- no known route, e.g. a
      dead-reckoning-only aircraft): the original ``SUSTAINED``
      indefinite heading hold, unchanged from Milestone 7's initial
      behaviour, since there is nothing to rejoin.

    Args:
        region: The track's matched region (unused directly here, kept
            for a consistent signature alongside the other lever-spec
            builders and potential future per-region tuning).
        snapshot: Current *observed* snapshot the clearance is applied to.
        config: Shared config (heading step, multipliers, vector duration).
        target: The aircraft this candidate targets.
        route: ``target``'s current known remaining route, or ``None``.

    Returns:
        One ``CandidateSpec`` per (sign, magnitude) combination -- e.g.
        4 with the default ``[1.0, 2.0]`` multiplier list.
    """
    specs: List[CandidateSpec] = []
    for magnitude in _step_magnitudes(config.resolution_heading_step_deg, config):
        for delta_value in (magnitude, -magnitude):
            hypothetical = _apply_clearance(snapshot, target.callsign, "HEADING", delta_value)
            if route:
                specs.append(
                    CandidateSpec(
                        "HEADING", target.callsign, delta_value, hypothetical,
                        vector_duration_s=config.resolution_vector_duration_s,
                        rejoin_route=route,
                    )
                )
            else:
                specs.append(
                    CandidateSpec("HEADING", target.callsign, delta_value, hypothetical)
                )
    return specs


def generate_candidates(
    region: ComplexityRegion,
    snapshot: TrafficSnapshot,
    config: ASTRAConfig,
    route_provider: Optional[RouteProvider] = None,
    target: Optional[AircraftState] = None,
    levers: Optional[Sequence[ClearanceType]] = None,
) -> List[CandidateSpec]:
    """Build the candidate set for one track's matched region.

    Every lever/sign combination is tried at every configured step
    magnitude (``config.resolution_step_multipliers``, default
    ``[1.0, 2.0]``) -- still a fully deterministic, exhaustive
    enumeration over fixed points, no randomness and no optimisation
    library, just more of them than Milestone 7's original single
    magnitude. Speed and flight-level candidates are always generated
    in both directions at every magnitude; heading is added (both
    directions, every magnitude) only if ``heading_lever_applicable``
    (OQ-2), as either ``SUSTAINED`` or ``VECTOR_AND_REJOIN`` candidates
    depending on whether the target has a known route -- see
    ``heading_lever_specs``.

    Args:
        region: The track's matched ``ComplexityRegion`` (real,
            unmodified) at the evaluated horizon.
        snapshot: The current *observed* ``TrafficSnapshot`` (the
            candidate's clearance is applied to this before being
            re-predicted forward to the evaluated horizon).
        config: Shared config (step sizes, multipliers, vector duration).
        route_provider: Optional ``Callable[[str], Optional[route]]``
            (typically ``StateReader.get_route``) -- if given, used to
            look up ``target``'s known route for the heading lever.
            ``None`` (the default) means "route information not
            available", not "this aircraft has no route" -- callers
            that have route data should always pass it, the same way
            ``Pipeline``/``RouteAwareTrajectoryEngine`` are opted into
            by passing a route provider rather than defaulting to one.
        target: Which cluster member to target. When omitted (the
            default), the highest-conflict-count member is selected via
            ``select_target_aircraft`` -- unchanged single-aircraft
            behaviour. Passed explicitly by
            ``ResolutionEngine._build_joint_candidate`` when building a
            joint candidate's secondary legs, so each leg targets a
            *different* member instead of always re-selecting the same
            top-ranked one.
        levers: Restrict which clearance types are generated (e.g.
            ``["SPEED"]`` for a joint candidate's secondary legs, which
            intentionally only search speed -- see
            ``ResolutionEngine._build_joint_candidate`` for why).
            ``None`` (the default) generates every applicable lever.

    Returns:
        ``CandidateSpec``s for every requested lever/sign/magnitude
        combination, or ``[]`` if no member of ``region.cluster``
        resolves against ``snapshot`` (or, when ``target`` is passed
        explicitly, if that specific aircraft is not resolvable).
    """
    if target is None:
        target = select_target_aircraft(region.cluster, snapshot, config)
    if target is None:
        return []

    allowed = set(levers) if levers is not None else {"SPEED", "FLIGHT_LEVEL", "HEADING"}
    specs: List[CandidateSpec] = []

    if "SPEED" in allowed:
        for magnitude in _step_magnitudes(config.resolution_speed_step_kt, config):
            for delta_value in (magnitude, -magnitude):
                specs.append(
                    CandidateSpec(
                        "SPEED", target.callsign, delta_value,
                        _apply_clearance(snapshot, target.callsign, "SPEED", delta_value),
                    )
                )

    if "FLIGHT_LEVEL" in allowed:
        for magnitude in _step_magnitudes(config.resolution_altitude_step_ft, config):
            for delta_value in (magnitude, -magnitude):
                specs.append(
                    CandidateSpec(
                        "FLIGHT_LEVEL", target.callsign, delta_value,
                        _apply_clearance(snapshot, target.callsign, "FLIGHT_LEVEL", delta_value),
                    )
                )

    if "HEADING" in allowed and heading_lever_applicable(region):
        route = route_provider(target.callsign) if route_provider is not None else None
        specs.extend(heading_lever_specs(region, snapshot, config, target, route))

    return specs
