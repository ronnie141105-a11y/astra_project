"""
Cluster detection data model (Milestone 3).

See docs/milestone_3_hotspot.md for design rationale, in particular why
``Cluster`` is intentionally stateless and not the persistent 4DARHAC.
"""

from dataclasses import dataclass
from typing import FrozenSet, Literal

#: "observed" = current TrafficSnapshot (horizon_min == 0).
#: "predicted" = a PredictedSnapshot at a configured horizon.
ClusterSource = Literal["observed", "predicted"]


@dataclass(frozen=True)
class Cluster:
    """A spatial grouping of aircraft detected by DBSCAN at one instant.

    Attributes:
        cluster_id: Unique within one ``ClusterEngine.detect()`` call only
            (``"{source}:{horizon_min}:{dbscan_label}"``) -- not a
            persistent identity across polls/horizons (see Milestone 5).
        source: "observed" or "predicted".
        horizon_min: 0 for observed; else the prediction horizon.
        valid_at_s: Absolute simulation time this cluster describes.
        member_callsigns: Callsigns of every aircraft in the cluster.
        centroid_lat: Mean latitude of member aircraft (decimal degrees).
        centroid_lon: Mean longitude of member aircraft (decimal degrees).
        centroid_alt_ft: Mean altitude of member aircraft (feet).
        horizontal_extent_nm: Max great-circle distance from centroid to
            any member (a simple cluster "radius").
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
        """Number of aircraft in this cluster."""
        return len(self.member_callsigns)