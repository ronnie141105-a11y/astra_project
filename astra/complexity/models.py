"""
Complexity assessment data model (Milestone 4).

Defines ``ComplexityRegion``: a ``Cluster`` (Milestone 3) plus its
instantaneous complexity assessment. Per the July 2026 architecture
review (``docs/architecture.md`` Sec 6), this is composition, not
inheritance -- a ``ComplexityRegion`` *has* a ``Cluster`` rather than
*being* an extended one, keeping the pure/stateless spatial-clustering
concern (Milestone 3) fully decoupled from the complexity-scoring concern
(this milestone). Both remain per-instant and stateless: neither knows
anything about the cluster/region seen at the previous horizon or poll
cycle. Persistent identity across time is Milestone 5's job
(4DARHAC detection / tracking), which will wrap a *sequence* of
``ComplexityRegion`` objects.
"""

from dataclasses import dataclass
from typing import Dict

from astra.hotspot.models import Cluster


@dataclass(frozen=True)
class ComplexityRegion:
    """A spatial cluster together with its complexity assessment.

    Immutable (frozen), matching every other per-instant data object in
    the pipeline (``AircraftState``, ``PredictedSnapshot``, ``Cluster``).

    Attributes:
        cluster: The underlying spatial grouping this assessment was
            computed from (see ``astra.hotspot.models.Cluster``).
        complexity_score: The combined complexity score, in ``[0, 100]``.
            A weighted combination of the five normalised components
            below -- see ``astra.complexity.engine.ComplexityEngine`` for
            the combination formula and ``ASTRAConfig.complexity_weight_*``
            for the weights.
        components: Raw (not normalised, not weighted) diagnostic values
            behind the score, keyed by name, for explainability -- this
            mirrors ASTRA's XAI (explainable AI) design goal, letting an
            FMP or a later HMI phase see *why* a region scored the way it
            did rather than only the final number. Keys present:

            * ``"density_ac_per_nm2"`` -- aircraft count divided by the
              cluster's approximate area (pi * horizontal_extent_nm^2).
            * ``"mtca_count"`` -- number of member-aircraft pairs meeting
              the Medium-Term Conflict Alert definition.
            * ``"ltca_count"`` -- number of member-aircraft pairs meeting
              the Long-Term Conflict Alert definition (excluding pairs
              already counted as MTCA).
            * ``"heading_div_deg"`` -- circular standard deviation of
              member headings, in degrees.
            * ``"alt_div_ft"`` -- population standard deviation of member
              altitudes, in feet.
            * ``"type_mix_count"`` -- number of distinct aircraft types
              among the cluster's members.
        computed_at_s: Absolute simulation time (seconds) this assessment
            was computed for -- always equal to ``cluster.valid_at_s``,
            duplicated here so a ``ComplexityRegion`` is self-describing
            without needing to reach into ``cluster`` for its own
            provenance.
    """

    cluster: Cluster
    complexity_score: float
    components: Dict[str, float]
    computed_at_s: float
