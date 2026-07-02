"""
Cluster detection data model (Milestone 3).

Defines ``Cluster``: a purely spatial grouping of aircraft, produced by
DBSCAN at ONE instant -- either one observed ``TrafficSnapshot`` or one
predicted ``PredictedSnapshot`` at a single horizon.

Design note -- this is intentionally NOT the persistent 4DARHAC
------------------------------------------------------------------
The July 2026 architecture review (see ``docs/architecture.md`` section 6)
identified that the original "hotspot detection" phase conflated two
different concerns:

* spatial clustering (this module) -- stateless, pure, re-derived fresh
  from a single snapshot every time.
* temporal linkage -- deciding whether a cluster observed at one horizon
  or poll cycle is "the same" persistent area as a cluster observed
  earlier. That is a stateful tracking problem, explicitly out of scope
  for Milestone 3, and reserved for Milestone 5 (4DARHAC detection).

``Cluster`` therefore has no notion of identity beyond a single detection
pass -- ``cluster_id`` is only unique *within* one ``ClusterEngine.detect()``
call, and is rebuilt from scratch on every call. Nothing here should be
read as "the same cluster from last poll cycle"; that comparison does not
exist yet. ``ComplexityRegion`` and ``FourDArhac`` (the next two layers of
the proposed domain model) are future milestones, not implemented here.
"""

from dataclasses import dataclass
from typing import FrozenSet, Literal

#: Where the snapshot this cluster was derived from came from.
#: "observed"  -- the current ``TrafficSnapshot`` (horizon_min == 0).
#: "predicted" -- a ``PredictedSnapshot`` at one of the configured
#:                trajectory-prediction horizons.
ClusterSource = Literal["observed", "predicted"]


@dataclass(frozen=True)
class Cluster:
    """A spatial grouping of aircraft detected by DBSCAN at one instant.

    Immutable (frozen): a ``Cluster`` represents the fixed geometric
    result of one clustering pass over one snapshot. Later pipeline stages
    (complexity assessment, tracking) build new objects that *reference*
    a ``Cluster`` rather than mutating it in place, mirroring how
    ``AircraftState`` and ``PredictedSnapshot`` are treated elsewhere in
    the codebase.

    Attributes:
        cluster_id: Identifier unique within a single detection pass, of
            the form ``f"{source}:{horizon_min}:{dbscan_label}"``. This is
            NOT a persistent identity -- the same physical group of
            aircraft will get a freshly generated ``cluster_id`` on every
            call to ``ClusterEngine.detect()``, including across
            successive horizons of the same prediction and across
            successive poll cycles. Persistent identity is the explicit
            responsibility of the (not yet implemented) Milestone 5
            4DARHAC tracker.
        source: Whether this cluster came from the observed snapshot or a
            predicted one.
        horizon_min: Prediction horizon in minutes. ``0`` for an observed
            snapshot; one of ``ASTRAConfig.prediction_horizons_min``
            (e.g. 5, 10, 15, 30, 60) for a predicted snapshot.
        valid_at_s: Absolute simulation time (seconds) this cluster
            describes -- the snapshot's ``timestamp_s`` for an observed
            snapshot, or ``predicted_time_s`` for a predicted one. Using
            an absolute time base (rather than just ``horizon_min``)
            keeps clusters comparable across different poll cycles, which
            matters for the future tracking stage.
        member_callsigns: Callsigns of every aircraft in this cluster.
            DBSCAN requires at least ``ASTRAConfig.dbscan_min_samples``
            aircraft for a group to form a cluster at all, so this set
            always has at least that many members.
        centroid_lat: Arithmetic mean latitude (decimal degrees) of all
            member aircraft. A simple mean is an adequate approximation
            for the sub-15 NM cluster extents this system operates on;
            it is not a proper spherical centroid.
        centroid_lon: Arithmetic mean longitude (decimal degrees) of all
            member aircraft. Same simplifying-mean caveat as
            ``centroid_lat``.
        centroid_alt_ft: Arithmetic mean altitude (feet) of all member
            aircraft.
        horizontal_extent_nm: Maximum great-circle distance (nautical
            miles) from the centroid to any member aircraft -- a simple
            cluster "radius", useful for later overlap-based track
            association (Milestone 5) and for HMI rendering (Milestone 8).
    """

    cluster_id: str
    source: ClusterSource
    horizon_min: int
    valid_at_s: float
    member_callsigns: FrozenSet[str]
    centroid_lat: float
    centroid_lon: float
    centroid_alt_ft: float
    horizontal_extent_nm: float

    def __len__(self) -> int:
        """Number of aircraft in this cluster.

        Returns:
            The number of member callsigns.
        """
        return len(self.member_callsigns)
