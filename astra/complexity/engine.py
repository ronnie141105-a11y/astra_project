"""
Complexity assessment engine (Milestone 4).

Resolves each ``Cluster``'s member aircraft from its source snapshot,
computes five raw diagnostic metrics, normalises and weight-combines them
into a 0-100 ``complexity_score``, and returns an immutable
``ComplexityRegion``. Stateless and per-instant, like ``ClusterEngine``.
See docs/milestone_4_complexity.md for the combination method and its
relationship to the reference ASTRA system's PCA-based approach.
"""

import math
from typing import Dict, List

from astra.complexity.conflict import count_conflicts
from astra.complexity.models import ComplexityRegion
from astra.complexity.stats import circular_std_dev_deg, population_std_dev
from astra.hotspot.engine import AircraftSnapshot
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger(__name__)


class ComplexityEngine:
    """Computes a 0-100 complexity score for each detected cluster.

    Stateless after construction; safe to share one instance across the
    whole ASTRA process.

    Example::

        clusters = cluster_engine.detect(snapshot)
        regions = complexity_engine.assess_many(clusters, snapshot)
        for r in regions:
            print(r.complexity_score, r.components)
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise from shared config (thresholds, references, weights)."""
        self._config = config
        _LOG.debug(
            "ComplexityEngine initialised. weights: density=%.2f "
            "conflict=%.2f heading_div=%.2f alt_div=%.2f type_mix=%.2f",
            config.complexity_weight_density,
            config.complexity_weight_conflict,
            config.complexity_weight_heading_div,
            config.complexity_weight_alt_div,
            config.complexity_weight_type_mix,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(self, cluster: Cluster, snapshot: AircraftSnapshot) -> ComplexityRegion:
        """Assess the complexity of a single cluster.

        Args:
            cluster: A ``Cluster`` produced against ``snapshot``.
            snapshot: The snapshot ``cluster`` was derived from (used to
                resolve member callsigns to full aircraft state).

        Returns:
            A new immutable ``ComplexityRegion``.

        Raises:
            KeyError: If a member callsign is not in ``snapshot``.
        """
        members = self._resolve_members(cluster, snapshot)
        components = self._compute_components(cluster, members)
        score = self._combine(components)
        return ComplexityRegion(
            cluster=cluster,
            complexity_score=score,
            components=components,
            computed_at_s=cluster.valid_at_s,
        )

    def assess_many(
        self, clusters: List[Cluster], snapshot: AircraftSnapshot
    ) -> List[ComplexityRegion]:
        """Assess every cluster in a list against the same snapshot."""
        return [self.assess(cluster, snapshot) for cluster in clusters]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_members(
        cluster: Cluster, snapshot: AircraftSnapshot
    ) -> List[AircraftState]:
        """Look up each cluster member's full state from the snapshot."""
        members = []
        for callsign in cluster.member_callsigns:
            state = snapshot.get(callsign)
            if state is None:
                raise KeyError(
                    f"Cluster {cluster.cluster_id!r} references callsign "
                    f"{callsign!r} not present in the given snapshot."
                )
            members.append(state)
        return members

    def _compute_components(
        self, cluster: Cluster, members: List[AircraftState]
    ) -> Dict[str, float]:
        """Compute every raw (unnormalised) complexity component."""
        extent_nm = max(cluster.horizontal_extent_nm, self._config.complexity_min_extent_nm)
        area_nm2 = math.pi * extent_nm * extent_nm
        density = len(members) / area_nm2

        mtca_count, ltca_count = count_conflicts(
            members, cluster.centroid_lat, cluster.centroid_lon, self._config
        )

        heading_div = circular_std_dev_deg([ac.heading_deg for ac in members])
        alt_div = population_std_dev([ac.altitude_ft for ac in members])
        type_mix = len({ac.aircraft_type for ac in members})

        return {
            "density_ac_per_nm2": density,
            "mtca_count": float(mtca_count),
            "ltca_count": float(ltca_count),
            "heading_div_deg": heading_div,
            "alt_div_ft": alt_div,
            "type_mix_count": float(type_mix),
        }

    def _combine(self, components: Dict[str, float]) -> float:
        """Normalise (0-100 vs. config reference) and weight-combine components."""
        cfg = self._config

        density_score = self._normalise(
            components["density_ac_per_nm2"], cfg.complexity_density_reference_ac_per_nm2
        )
        heading_div_score = self._normalise(
            components["heading_div_deg"], cfg.complexity_heading_div_reference_deg
        )
        alt_div_score = self._normalise(
            components["alt_div_ft"], cfg.complexity_alt_div_reference_ft
        )
        type_mix_score = self._normalise(
            components["type_mix_count"], cfg.complexity_type_mix_reference_count
        )

        # MTCA/LTCA fold into one "conflict" sub-score before combination:
        # both represent the same driver (conflict potential) at different
        # time horizons.
        mtca_score = self._normalise(
            components["mtca_count"], cfg.complexity_mtca_reference_count
        )
        ltca_score = self._normalise(
            components["ltca_count"], cfg.complexity_ltca_reference_count
        )
        conflict_score = (
            cfg.complexity_mtca_weight_in_conflict * mtca_score
            + cfg.complexity_ltca_weight_in_conflict * ltca_score
        )

        combined = (
            cfg.complexity_weight_density * density_score
            + cfg.complexity_weight_conflict * conflict_score
            + cfg.complexity_weight_heading_div * heading_div_score
            + cfg.complexity_weight_alt_div * alt_div_score
            + cfg.complexity_weight_type_mix * type_mix_score
        )
        return max(0.0, min(100.0, combined))

    @staticmethod
    def _normalise(raw_value: float, reference_value: float) -> float:
        """Linearly scale a raw metric onto [0, 100] against a reference value."""
        if reference_value <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * raw_value / reference_value))