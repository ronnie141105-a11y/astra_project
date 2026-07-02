"""
Cluster detection engine (Milestone 3).

Applies DBSCAN, using a custom horizontal-and-vertical distance metric
(see ``astra.hotspot.distance``), to a single traffic snapshot -- either
the current observed ``TrafficSnapshot`` or one predicted horizon's
``PredictedSnapshot`` -- and returns the resulting ``Cluster`` objects.

Scope
-----
``ClusterEngine`` is deliberately stateless and knows nothing about time
beyond the single snapshot it is given: it does not compare clusters
across horizons or across poll cycles, and it assigns no persistent
identity to a cluster. Linking clusters over time into a persistent
4DARHAC is Milestone 5's responsibility (4DARHAC detection / tracking),
scoped separately by the July 2026 architecture review specifically so
this stateless, mechanical piece and that stateful, novel piece can be
designed, tested, and verified independently.

Reuse
-----
- ``astra.interface.traffic_state.TrafficSnapshot``  -- observed input type
- ``astra.trajectory.models.PredictedSnapshot``       -- predicted input type
- ``astra.trajectory.models.PredictionResult``        -- bulk predicted input
- ``astra.utils.config.ASTRAConfig``                  -- DBSCAN parameters
- ``astra.utils.geodesy.haversine_distance_nm``       -- centroid extent
- ``astra.utils.logger``                              -- logging
- ``astra.hotspot.distance.build_distance_matrix``    -- custom metric
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

#: A snapshot ClusterEngine can cluster. Both types expose the same
#: ``as_list()`` accessor (see ``PredictedSnapshot``'s docstring), but
#: differ in how they carry their time metadata, which is why
#: ``_snapshot_metadata()`` still needs to distinguish them explicitly.
AircraftSnapshot = Union[TrafficSnapshot, PredictedSnapshot]

#: DBSCAN's noise label -- points that are not part of any cluster.
_NOISE_LABEL = -1


class ClusterEngine:
    """DBSCAN-based spatial cluster detector.

    Detects groups of aircraft that are within ``ASTRAConfig``'s
    horizontal and vertical separation thresholds of each other, using
    DBSCAN with a precomputed distance matrix that enforces both
    constraints simultaneously (see ``astra.hotspot.distance``).

    Thread safety
    -------------
    ``ClusterEngine`` is stateless after construction -- ``detect()``
    reads only the snapshot it receives and the config passed at init,
    exactly like ``TrajectoryEngine``. It is safe to call from multiple
    threads, or to share a single instance across the whole ASTRA process.

    Example usage::

        engine = ClusterEngine(config)
        clusters = engine.detect(reader.current())
        for c in clusters:
            print(f"{len(c)} aircraft near ({c.centroid_lat:.3f}, "
                  f"{c.centroid_lon:.3f}), FL{c.centroid_alt_ft/100:.0f}")

        # Or cluster every predicted horizon in one call:
        prediction = trajectory_engine.predict(reader.current())
        clusters_by_horizon = engine.detect_all(prediction)
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise the engine from the shared configuration.

        Args:
            config: Shared ASTRA configuration. Reads
                ``separation_horizontal_nm`` (DBSCAN eps, NM),
                ``separation_vertical_ft`` (vertical neighbourhood gate,
                ft), and ``dbscan_min_samples`` (minimum cluster size).
                None of these are hardcoded in this module.
        """
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
        """Detect spatial clusters in a single snapshot.

        Works identically for an observed ``TrafficSnapshot`` (current
        traffic) or a predicted ``PredictedSnapshot`` (one trajectory
        horizon) -- the same DBSCAN pass is applied either way, with the
        snapshot's own time metadata carried onto the resulting
        ``Cluster`` objects.

        Args:
            snapshot: The traffic state to cluster.

        Returns:
            One ``Cluster`` per DBSCAN group found. Aircraft that DBSCAN
            labels as noise (not part of any group of at least
            ``dbscan_min_samples`` aircraft) are not included in any
            ``Cluster``. Returns an empty list if the snapshot has no
            aircraft, or if no group meets the minimum size.
        """
        source, horizon_min, valid_at_s = self._snapshot_metadata(snapshot)
        return self._cluster_aircraft(
            snapshot.as_list(), source, horizon_min, valid_at_s
        )

    def detect_all(
        self, prediction: PredictionResult
    ) -> Dict[int, List[Cluster]]:
        """Detect spatial clusters independently at every predicted horizon.

        Convenience wrapper that calls ``detect()`` once per horizon in
        ``prediction``. This performs NO linkage between horizons -- each
        horizon's clusters are computed completely independently, exactly
        as ``detect()`` would produce them one at a time. Associating
        clusters across horizons into a persistent object is explicitly
        out of scope (Milestone 5).

        Args:
            prediction: A ``PredictionResult`` from
                ``TrajectoryEngine.predict()``.

        Returns:
            A mapping ``{horizon_min: [Cluster, ...]}`` covering every
            horizon in ``prediction.horizon_list()``.
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
        """Extract (source, horizon_min, valid_at_s) from either snapshot type.

        Args:
            snapshot: An observed ``TrafficSnapshot`` or predicted
                ``PredictedSnapshot``.

        Returns:
            A ``(source, horizon_min, valid_at_s)`` tuple. ``horizon_min``
            is always ``0`` and ``source`` is always ``"observed"`` for a
            ``TrafficSnapshot``.

        Raises:
            TypeError: If ``snapshot`` is neither a ``TrafficSnapshot``
                nor a ``PredictedSnapshot``.
        """
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
        """Run DBSCAN over one list of aircraft and build Cluster objects.

        Args:
            aircraft: Aircraft states to cluster, in a fixed order.
            source: Whether these aircraft came from an observed or
                predicted snapshot (carried onto every resulting Cluster).
            horizon_min: Prediction horizon in minutes (0 for observed).
            valid_at_s: Absolute simulation time these aircraft states
                describe.

        Returns:
            One ``Cluster`` per DBSCAN group of at least
            ``dbscan_min_samples`` aircraft.
        """
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
        """Build one immutable Cluster from a group of member aircraft.

        Args:
            members: Aircraft states DBSCAN assigned to this group.
            label: DBSCAN's integer cluster label for this group (used
                only to build ``cluster_id``; not otherwise meaningful).
            source: Whether these aircraft came from an observed or
                predicted snapshot.
            horizon_min: Prediction horizon in minutes (0 for observed).
            valid_at_s: Absolute simulation time these aircraft states
                describe.

        Returns:
            A new immutable ``Cluster``.
        """
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
