"""
Complexity assessment data model (Milestone 4).

Defines ``ComplexityRegion``: composition, not inheritance -- a
``ComplexityRegion`` *has* a ``Cluster`` rather than extending it, keeping
spatial clustering (Milestone 3) decoupled from complexity scoring
(this milestone). See docs/milestone_4_complexity.md.
"""

from dataclasses import dataclass
from typing import Dict

from astra.hotspot.models import Cluster


@dataclass(frozen=True)
class ComplexityRegion:
    """A spatial cluster together with its complexity assessment.

    Attributes:
        cluster: The underlying spatial grouping this was computed from.
        complexity_score: Combined score in ``[0, 100]`` (weighted
            combination of ``components``; see ``ComplexityEngine``).
        components: Raw diagnostic values behind the score, for
            explainability. Keys: ``"density_ac_per_nm2"``,
            ``"mtca_count"``, ``"ltca_count"``, ``"heading_div_deg"``,
            ``"alt_div_ft"``, ``"type_mix_count"``.
        computed_at_s: Absolute simulation time this assessment is for
            (equals ``cluster.valid_at_s``).
    """

    cluster: Cluster
    complexity_score: float
    components: Dict[str, float]
    computed_at_s: float