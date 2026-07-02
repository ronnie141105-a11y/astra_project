"""
Complexity assessment engine (Milestone 4).

Takes the ``Cluster`` objects produced by Milestone 3's ``ClusterEngine``
and, for each one, resolves its member aircraft back out of the snapshot
they came from, computes five raw diagnostic metrics, normalises and
combines them into a single 0-100 ``complexity_score``, and returns the
result as an immutable ``ComplexityRegion``.

Scope
-----
Like ``ClusterEngine``, ``ComplexityEngine`` is stateless and per-instant:
it assesses one ``Cluster`` against the one snapshot it came from, and
retains no memory of any assessment across horizons or poll cycles.
Tracking a region's complexity trend over time (rising/falling, onset,
peak) is Milestone 5/6's job (4DARHAC detection and forecast), which
consumes a *sequence* of ``ComplexityRegion`` objects that this engine
produces one at a time.

Combination method
-------------------
The full reference ASTRA system decorrelates its (larger) metric set with
PCA fitted on a multi-year historical reference dataset, then combines
the decorrelated components with a quadratic mean (see
``framework_for_predict_and_resolve_hotspot.md`` Sec 2.4.2). Both the PCA
basis and the percentile-based [0, 100] metric scaling it depends on
require historical data this thesis-scale prototype does not have.

This engine instead uses a simpler, fully-specified alternative that
preserves the same overall shape (normalise each metric to [0, 100]
against a fixed reference value, then combine into one [0, 100] score):
a weighted linear combination, with reference values and weights
centralised in ``ASTRAConfig`` (see ``astra.utils.config`` Sec "Phase 4").
This is a documented simplification, not a claim of equivalence to the
literature method -- see "Known limitations" in ``Developer_Handover.md``.

Reuse
-----
- ``astra.hotspot.models.Cluster``               -- input
- ``astra.hotspot.engine.AircraftSnapshot``       -- shared snapshot union type
- ``astra.complexity.conflict``                   -- MTCA/LTCA pairwise counting
- ``astra.complexity.stats``                      -- circular/linear std dev
- ``astra.utils.config.ASTRAConfig``              -- reference values, weights
- ``astra.utils.logger``                          -- logging
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

    Thread safety
    -------------
    Stateless after construction, exactly like ``ClusterEngine`` and
    ``TrajectoryEngine`` -- safe to share a single instance across the
    whole ASTRA process.

    Example usage::

        cluster_engine = ClusterEngine(config)
        complexity_engine = ComplexityEngine(config)

        snapshot = reader.current()
        clusters = cluster_engine.detect(snapshot)
        regions = complexity_engine.assess_many(clusters, snapshot)
        for r in regions:
            print(f"{len(r.cluster)} aircraft, score={r.complexity_score:.1f}, "
                  f"components={r.components}")
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise the engine from the shared configuration.

        Args:
            config: Shared ASTRA configuration. Reads the
                ``mtca_*``/``ltca_*`` conflict thresholds, every
                ``complexity_*_reference_*`` normalisation value, and
                every ``complexity_weight_*`` combination weight. None of
                these are hardcoded in this module.
        """
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
            cluster: A ``Cluster`` produced by ``ClusterEngine.detect()``
                against ``snapshot`` (or an equivalent snapshot at the
                same horizon/time -- the engine trusts the caller to pass
                a matching pair, exactly as ``ClusterEngine`` does not
                itself verify horizon consistency either).
            snapshot: The observed or predicted snapshot ``cluster`` was
                derived from, used to resolve each member callsign back
                to a full ``AircraftState`` (position, heading, speed,
                altitude, type).

        Returns:
            A new immutable ``ComplexityRegion``.

        Raises:
            KeyError: If a callsign in ``cluster.member_callsigns`` is not
                present in ``snapshot`` (i.e. the wrong snapshot was
                passed for this cluster).
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
        """Assess every cluster in a list against the same snapshot.

        Args:
            clusters: Clusters to assess, all derived from ``snapshot``
                (e.g. the full output of one ``ClusterEngine.detect()``
                call).
            snapshot: The snapshot every cluster in ``clusters`` was
                derived from.

        Returns:
            One ``ComplexityRegion`` per input cluster, in the same order.
        """
        return [self.assess(cluster, snapshot) for cluster in clusters]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_members(
        cluster: Cluster, snapshot: AircraftSnapshot
    ) -> List[AircraftState]:
        """Look up each cluster member's full state from the snapshot.

        Args:
            cluster: The cluster whose members are being resolved.
            snapshot: The snapshot to resolve callsigns against.

        Returns:
            One ``AircraftState`` per callsign in
            ``cluster.member_callsigns``.

        Raises:
            KeyError: If a member callsign is not found in ``snapshot``.
        """
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
        """Compute every raw (unnormalised) complexity component.

        Args:
            cluster: The cluster being assessed (used for its centroid
                and horizontal extent).
            members: The cluster's member aircraft states, resolved by
                ``_resolve_members``.

        Returns:
            A dict with keys ``"density_ac_per_nm2"``, ``"mtca_count"``,
            ``"ltca_count"``, ``"heading_div_deg"``, ``"alt_div_ft"``, and
            ``"type_mix_count"`` -- see ``ComplexityRegion.components``
            for the meaning of each.
        """
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
        """Normalise and weight-combine raw components into one 0-100 score.

        Each component is linearly scaled against its
        ``ASTRAConfig.complexity_*_reference_*`` value and clamped to
        [0, 100] (a value at or beyond the reference saturates at 100).
        The MTCA and LTCA counts are first combined into one "conflict"
        sub-score (weighted by ``complexity_mtca_weight_in_conflict`` /
        ``complexity_ltca_weight_in_conflict``) before being normalised,
        since both represent the same underlying complexity driver
        (conflict potential) at different time horizons.

        Args:
            components: Raw component values from ``_compute_components``.

        Returns:
            The combined complexity score, in ``[0, 100]``.
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
        """Linearly scale a raw metric onto [0, 100] against a reference.

        Args:
            raw_value: The metric's raw (unnormalised) value. Must be
                non-negative (every raw component this engine computes
                is a count, a density, or a standard deviation, all of
                which are non-negative by construction).
            reference_value: The value at which the normalised score
                saturates at 100. Must be positive.

        Returns:
            ``100 * raw_value / reference_value``, clamped to [0, 100].
        """
        if reference_value <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * raw_value / reference_value))
