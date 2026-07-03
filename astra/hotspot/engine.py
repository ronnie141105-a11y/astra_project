"""
Cluster detection engine (Milestone 3).

Applies DBSCAN (custom horizontal+vertical metric, see
``astra.hotspot.distance``) to one traffic snapshot -- observed or
predicted -- and returns ``Cluster`` objects. Stateless: no linkage
across horizons/polls (that is Milestone 5, see docs/milestone_3_hotspot.md).
"""

from typing import Dict, List, Sequence, Tuple, Union

from sklearn.cluster import DBSCAN

from astra.hotspot.distance import build_distance_matrix
from astra.hotspot.models import Cluster, ClusterSource
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)

#: A snapshot ClusterEngine can cluster (observed or one predicted horizon).
AircraftSnapshot = Union[TrafficSnapshot, PredictedSnapshot]

#: DBSCAN's noise label -- points not part of any cluster.
_NOISE_LABEL = -1


class ClusterEngine:
    """DBSCAN-based spatial cluster detector.

    Stateless after construction; safe to share one instance across the
    whole ASTRA process.

    Example::

        engine = ClusterEngine(config)
        clusters = engine.detect(reader.current())

        prediction = trajectory_engine.predict(reader.current())
        clusters_by_horizon = engine.detect_all(prediction)
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise from shared config (eps, vertical gate, min_samples)."""
        self._config = config
        _LOG.debug(
            "ClusterEngine initialised. eps=%.1f NM, vertical_gate=%.0f ft, "
            "min_samples=%d",
            config.separation_horizontal_nm,
            config.separation_vertical_ft,
            config.dbscan_min_samples,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, snapshot: AircraftSnapshot) -> List[Cluster]:
        """Detect spatial clusters in one observed or predicted snapshot.

        Args:
            snapshot: The traffic state to cluster.

        Returns:
            One ``Cluster`` per DBSCAN group; noise aircraft are excluded.
        """
        source, horizon_min, valid_at_s = self._snapshot_metadata(snapshot)
        return self._cluster_aircraft(
            snapshot.as_list(), source, horizon_min, valid_at_s
        )

    def detect_all(
        self, prediction: PredictionResult
    ) -> Dict[int, List[Cluster]]:
        """Detect clusters independently at every horizon in ``prediction``.

        Args:
            prediction: A ``PredictionResult`` from
                ``TrajectoryEngine.predict()``.

        Returns:
            ``{horizon_min: [Cluster, ...]}`` for every predicted horizon.
        """
        return {
            horizon_min: self.detect(prediction.at(horizon_min))
            for horizon_min in prediction.horizon_list()
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_metadata(
        snapshot: AircraftSnapshot,
    ) -> Tuple[ClusterSource, int, float]:
        """Extract (source, horizon_min, valid_at_s) from either snapshot type."""
        if isinstance(snapshot, PredictedSnapshot):
            return "predicted", snapshot.horizon_min, snapshot.predicted_time_s
        if isinstance(snapshot, TrafficSnapshot):
            return "observed", 0, snapshot.timestamp_s
        raise TypeError(
            "ClusterEngine.detect() expects a TrafficSnapshot or "
            f"PredictedSnapshot, got {type(snapshot).__name__}"
        )

    def _cluster_aircraft(
        self,
        aircraft: List[AircraftState],
        source: ClusterSource,
        horizon_min: int,
        valid_at_s: float,
    ) -> List[Cluster]:
        """Run DBSCAN over one list of aircraft and build Cluster objects."""
        n = len(aircraft)
        if n == 0:
            _LOG.debug(
                "No aircraft to cluster (source=%s, horizon=%d min).",
                source,
                horizon_min,
            )
            return []

        distance_matrix = build_distance_matrix(
            aircraft, self._config.separation_vertical_ft
        )
        dbscan = DBSCAN(
            eps=self._config.separation_horizontal_nm,
            min_samples=self._config.dbscan_min_samples,
            metric="precomputed",
        )
        labels = dbscan.fit_predict(distance_matrix)

        members_by_label: Dict[int, List[AircraftState]] = {}
        for ac, label in zip(aircraft, labels):
            if label == _NOISE_LABEL:
                continue
            members_by_label.setdefault(int(label), []).append(ac)

        clusters = [
            self._build_cluster(members, label, source, horizon_min, valid_at_s)
            for label, members in sorted(members_by_label.items())
        ]
        _LOG.debug(
            "Cluster detection: %d aircraft -> %d cluster(s), %d noise "
            "(source=%s, horizon=%d min).",
            n,
            len(clusters),
            n - sum(len(c) for c in clusters),
            source,
            horizon_min,
        )
        return clusters

    @staticmethod
    def _build_cluster(
        members: Sequence[AircraftState],
        label: int,
        source: ClusterSource,
        horizon_min: int,
        valid_at_s: float,
    ) -> Cluster:
        """Build one immutable Cluster from a group of member aircraft."""
        centroid_lat = sum(ac.lat for ac in members) / len(members)
        centroid_lon = sum(ac.lon for ac in members) / len(members)
        centroid_alt_ft = sum(ac.altitude_ft for ac in members) / len(members)
        horizontal_extent_nm = max(
            haversine_distance_nm(centroid_lat, centroid_lon, ac.lat, ac.lon)
            for ac in members
        )

        return Cluster(
            cluster_id=f"{source}:{horizon_min}:{label}",
            source=source,
            horizon_min=horizon_min,
            valid_at_s=valid_at_s,
            member_callsigns=frozenset(ac.callsign for ac in members),
            centroid_lat=centroid_lat,
            centroid_lon=centroid_lon,
            centroid_alt_ft=centroid_alt_ft,
            horizontal_extent_nm=horizontal_extent_nm,
        )