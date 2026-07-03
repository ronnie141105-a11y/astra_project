"""
Track association heuristics (Milestone 5).

Pure functions, no engine state -- mirrors ``astra.hotspot.distance``'s
pattern of a small, independently-testable pure-math module feeding the
stateful engine (``astra.tracking.engine.TrackerEngine``). See
docs/milestone_5_tracking.md for the matching-strategy rationale.
"""

from typing import FrozenSet, List, Optional, Tuple

from astra.hotspot.models import Cluster
from astra.tracking.models import FourDArhac
from astra.utils.geodesy import haversine_distance_nm


def jaccard_similarity(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    """Jaccard similarity (|intersection| / |union|) of two callsign sets.

    Args:
        a: First set of callsigns.
        b: Second set of callsigns.

    Returns:
        A value in ``[0, 1]``; ``0.0`` if both sets are empty (no
        meaningful overlap to claim) or disjoint.
    """
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def centroid_extent_overlap(cluster_a: Cluster, cluster_b: Cluster) -> bool:
    """True if two clusters' centroid+extent circles intersect.

    Fallback matching signal for cases -- e.g. longer-horizon predictions
    or several elapsed poll cycles -- where member-callsign overlap has
    drifted below the primary Jaccard threshold but the physical area of
    high complexity plausibly coincides.

    Args:
        cluster_a: First cluster.
        cluster_b: Second cluster.

    Returns:
        ``True`` if the great-circle distance between centroids is no
        greater than the sum of both clusters' ``horizontal_extent_nm``.
    """
    distance_nm = haversine_distance_nm(
        cluster_a.centroid_lat,
        cluster_a.centroid_lon,
        cluster_b.centroid_lat,
        cluster_b.centroid_lon,
    )
    return distance_nm <= (
        cluster_a.horizontal_extent_nm + cluster_b.horizontal_extent_nm
    )


def best_track_match(
    new_cluster: Cluster,
    candidate_tracks: List[FourDArhac],
    jaccard_threshold: float,
) -> Optional[FourDArhac]:
    """Find the best-matching open track for a newly detected cluster.

    Primary signal: Jaccard similarity of ``member_callsigns`` against
    each candidate track's most recent entry; a candidate must meet
    ``jaccard_threshold`` to be eligible. Among candidates that clear the
    threshold, the highest Jaccard similarity wins (ties broken by
    smaller centroid distance).

    Fallback: if no candidate clears the Jaccard threshold, centroid/
    extent circle overlap is used instead (smallest centroid distance
    wins among overlapping candidates).

    Args:
        new_cluster: The freshly detected ``Cluster`` to associate.
        candidate_tracks: Open tracks eligible for matching this cycle
            (callers should already exclude tracks claimed earlier in
            the same cycle, to keep matching one-to-one per cycle).
        jaccard_threshold: Minimum Jaccard similarity to accept a
            primary match (``ASTRAConfig.tracking_jaccard_threshold``).

    Returns:
        The best-matching ``FourDArhac``, or ``None`` if no candidate
        clears either the primary or fallback signal.
    """
    scored: List[Tuple[FourDArhac, float, float, bool]] = []
    for track in candidate_tracks:
        if not track.track:
            continue
        last_cluster = track.track[-1].cluster
        jaccard = jaccard_similarity(
            new_cluster.member_callsigns, last_cluster.member_callsigns
        )
        distance_nm = haversine_distance_nm(
            new_cluster.centroid_lat,
            new_cluster.centroid_lon,
            last_cluster.centroid_lat,
            last_cluster.centroid_lon,
        )
        overlaps = centroid_extent_overlap(new_cluster, last_cluster)
        scored.append((track, jaccard, distance_nm, overlaps))

    primary = [entry for entry in scored if entry[1] >= jaccard_threshold]
    if primary:
        primary.sort(key=lambda entry: (-entry[1], entry[2]))
        return primary[0][0]

    fallback = [entry for entry in scored if entry[3]]
    if fallback:
        fallback.sort(key=lambda entry: entry[2])
        return fallback[0][0]

    return None
