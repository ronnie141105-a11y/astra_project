"""
Complexity assessment engine (Milestone 4, extended with small-cluster
conflict-reference scaling).

Resolves each ``Cluster``'s member aircraft from its source snapshot,
computes five raw diagnostic metrics, normalises and weight-combines them
into a 0-100 ``complexity_score``, and returns an immutable
``ComplexityRegion``. Stateless and per-instant, like ``ClusterEngine``.
See docs/milestone_4_complexity.md for the combination method and its
relationship to the reference ASTRA system's PCA-based approach.

The MTCA/LTCA "conflict" sub-score's saturation references
(``complexity_mtca_reference_count``/``complexity_ltca_reference_count``)
are scaled down for small clusters -- see
``ComplexityEngine._effective_conflict_reference`` for the full
rationale. In short: a 2-aircraft cluster has only one possible conflict
pair, so normalising it against a reference calibrated for 3 *concurrent*
pairs structurally caps that pair's contribution well below
``forecast_onset_threshold`` regardless of how severe the actual conflict
is -- found empirically while validating this project's own
``arrival_sequencing`` scenario preset (see
docs/backend_improvements_backlog.md item 2). This only ever lowers the
effective reference for clusters smaller than the configured reference
implies; every cluster at or above that size is unaffected.
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
        score = self._combine(components, member_count=len(members))
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

    def _combine(self, components: Dict[str, float], member_count: int) -> float:
        """Normalise (0-100 vs. config reference) and weight-combine components.

        Args:
            components: Raw component values from `_compute_components`.
            member_count: Number of aircraft in the cluster -- needed to
                scale the MTCA/LTCA saturation references for small
                clusters; see `_effective_conflict_reference`.
        """
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
        # time horizons. The saturation references below are scaled down
        # for small clusters -- see `_effective_conflict_reference`'s
        # docstring for why an un-scaled reference structurally caps a
        # genuine 2-aircraft conflict's contribution regardless of how
        # severe it is.
        mtca_score = self._normalise(
            components["mtca_count"],
            self._effective_conflict_reference(cfg.complexity_mtca_reference_count, member_count),
        )
        ltca_score = self._normalise(
            components["ltca_count"],
            self._effective_conflict_reference(cfg.complexity_ltca_reference_count, member_count),
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
    def _effective_conflict_reference(configured_reference: int, member_count: int) -> int:
        """Scale down an MTCA/LTCA saturation reference for small clusters.

        `complexity_mtca_reference_count`/`complexity_ltca_reference_count`
        are calibrated as "how many *concurrent* conflict pairs looks like
        a fully complex, saturated scenario" -- e.g. 3 simultaneous MTCA
        pairs. That calibration implicitly assumes a cluster large enough
        to have that many pairs at all: a 2-aircraft cluster has exactly
        one possible pair (``C(2,2) = 1``), so even a single, severe,
        already-inside-minima MTCA conflict there is normalised against a
        reference of 3 and can never contribute more than ~33% of the
        conflict sub-score's own weight -- capping the achievable
        composite score for *any* 2-aircraft conflict well below
        `forecast_onset_threshold` regardless of how close or fast-closing
        the pair actually is. This was found empirically while validating
        `arrival_sequencing_aircraft()`'s preset (see
        `astra/dashboard/scenario_presets_operational.py`): MTCA/LTCA
        *does* correctly react to a same-heading, same-altitude pair
        closing to minima (the CPA calculation has no heading/altitude
        blind spot -- see `astra.complexity.conflict`), it just could
        never be *counted* as fully saturating for a pair that small.

        The fix: cap the configured reference at the cluster's actual
        maximum possible pair count, ``C(n, 2) = n * (n - 1) / 2``, so a
        2-aircraft cluster's one possible pair being in conflict *is*
        treated as saturating (matching what "this pair is as bad as it
        gets" should mean), while every cluster at or above the
        configured reference's own implied size (``C(n,2) >= reference``,
        e.g. n=3 for reference=3) is completely unaffected -- this only
        ever lowers the effective reference, never raises it, so no
        existing 3+-aircraft scenario's calibrated behaviour changes.

        Args:
            configured_reference: `complexity_mtca_reference_count` or
                `complexity_ltca_reference_count` as configured.
            member_count: Number of aircraft in the cluster.

        Returns:
            ``min(configured_reference, C(member_count, 2))``, floored at
            1 to keep `_normalise` well-defined for a (structurally
            impossible, but defensively handled) cluster of fewer than 2
            resolvable members.
        """
        max_possible_pairs = member_count * (member_count - 1) // 2
        return max(1, min(configured_reference, max_possible_pairs))

    @staticmethod
    def _normalise(raw_value: float, reference_value: float) -> float:
        """Linearly scale a raw metric onto [0, 100] against a reference value."""
        if reference_value <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * raw_value / reference_value))