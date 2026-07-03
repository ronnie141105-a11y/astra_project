"""
Centralised configuration for the ASTRA prototype.

Design decision
----------------
Every tunable constant used anywhere in the pipeline lives in a single
dataclass, `ASTRAConfig`. This mirrors how the reference SESAR ASTRA
documents treat these values: fixed, documented, ANSP-level parameters
(e.g. the 15 NM / 1000 ft separation thresholds used by both the DBSCAN
clustering step and the MTCA/LTCA conflict definitions) rather than
per-module magic numbers.

Concretely this gives us three benefits relevant to a thesis-scale system:

1. Traceability: every number in the dissertation's methodology section
   can point at one named field here.
2. No duplication: Phase 3 (hotspot clustering) and Phase 4 (complexity)
   both need the 15 NM / 1000 ft separation criteria; they read the same
   field instead of redefining it.
3. Testability: unit tests can construct an `ASTRAConfig` with extreme or
   trivial values without touching any other module.

Fields are grouped by the pipeline phase that first consumes them. Fields
for phases that are not implemented yet are included now (with their
literature-sourced default values) so the schema does not change shape as
later phases are built -- but nothing reads them until that phase exists.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ASTRAConfig:
    """Immutable configuration object passed explicitly through the pipeline.

    The object is frozen (read-only after construction) because the same
    configuration instance is shared by every module in a running ASTRA
    process; accidental mutation from one module silently changing another
    module's behaviour is a common and hard-to-debug source of bugs in
    pipeline-style systems, so we close that door entirely.
    """

    # ------------------------------------------------------------------
    # Phase 1 - BlueSky connectivity (astra.interface)
    # ------------------------------------------------------------------
    #: Hostname / IP of the machine running the BlueSky simulation server.
    #: BlueSky must be started separately, headless, e.g.:
    #:     python -m bluesky --headless
    bluesky_host: str = "localhost"

    #: ZeroMQ port BlueSky's server/node listens on for incoming traffic
    #: (commands, subscriptions). Matches BlueSky's default `recv_port`.
    bluesky_recv_port: int = 11000

    #: ZeroMQ port BlueSky's server/node publishes outgoing data on
    #: (e.g. the ACDATA aircraft-state stream). Matches BlueSky's default
    #: `send_port`.
    bluesky_send_port: int = 11001

    #: How often (seconds) the main loop is expected to call
    #: `StateReader.poll()`. BlueSky's own ACDATA publish rate is 5 Hz
    #: (see bluesky.simulation.screenio.ACUPDATE_RATE), so polling faster
    #: than ~0.2 s does not yield new data; polling slower trades latency
    #: for lower CPU usage. 1.0 s is a reasonable default for a tactical
    #: (minutes-scale) decision-support tool.
    poll_interval_s: float = 1.0

    #: Number of past TrafficSnapshots retained in memory by StateReader.
    #: At poll_interval_s = 1.0 s, 3600 entries means a 1-hour rolling
    #: history, which matches the system's 1-hour prediction horizon.
    history_length: int = 3600

    # ------------------------------------------------------------------
    # Phase 3 - hotspot clustering (astra.hotspot)
    # Source: both reference documents define a Swiss-ATCO conflict
    # notification threshold of 15 NM horizontal / 1000 ft vertical, used
    # directly as the DBSCAN neighbourhood definition.
    # ------------------------------------------------------------------
    separation_horizontal_nm: float = 15.0
    separation_vertical_ft: float = 1000.0

    #: Minimum number of aircraft for a DBSCAN neighbourhood to "count":
    #: a hotspot requires at least 2 aircraft to interact.
    dbscan_min_samples: int = 2

    # ------------------------------------------------------------------
    # Phase 2 - trajectory prediction (astra.trajectory)
    # ------------------------------------------------------------------
    #: Look-ahead horizons (minutes) at which predicted positions / hotspot
    #: states are evaluated. Values: 5, 10, 15, 30, 60 minutes.
    #: (The original scaffold had 20 min; 15 min is correct per Phase 2 spec.)
    prediction_horizons_min: List[int] = field(
        default_factory=lambda: [5, 10, 15, 30, 60]
    )

    #: Overall prediction horizon (minutes). Individual horizons above must
    #: not exceed this value.
    max_prediction_horizon_min: int = 60

    # ------------------------------------------------------------------
    # Phase 4 - complexity assessment (astra.complexity)
    # MTCA/LTCA thresholds from the reference documents (5.5NM/2.5min,
    # 7.9NM/15min). Reference/weight values below are documented
    # simplifications in place of the reference system's historical
    # percentile calibration -- see docs/milestone_4_complexity.md.
    # ------------------------------------------------------------------
    mtca_distance_nm: float = 5.5
    mtca_time_min: float = 2.5
    ltca_distance_nm: float = 7.9
    ltca_time_min: float = 15.0

    #: Saturation values normalising each raw component to [0, 100].
    complexity_density_reference_ac_per_nm2: float = 0.05
    complexity_mtca_reference_count: int = 3
    complexity_ltca_reference_count: int = 5
    complexity_heading_div_reference_deg: float = 60.0
    complexity_alt_div_reference_ft: float = 2000.0
    complexity_type_mix_reference_count: int = 4

    #: Density-area floor (NM), avoids div-by-zero for coincident aircraft.
    complexity_min_extent_nm: float = 0.5

    #: Sub-score combination weights; must sum to 1.0 (see __post_init__).
    complexity_weight_density: float = 0.30
    complexity_weight_conflict: float = 0.30
    complexity_weight_heading_div: float = 0.15
    complexity_weight_alt_div: float = 0.15
    complexity_weight_type_mix: float = 0.10

    #: MTCA vs. LTCA contribution to the conflict sub-score; sums to 1.0.
    complexity_mtca_weight_in_conflict: float = 0.7
    complexity_ltca_weight_in_conflict: float = 0.3

    def __post_init__(self) -> None:
        """Fail fast on internally-inconsistent configuration.

        Raises:
            ValueError: if any cross-field invariant is violated.
        """
        if max(self.prediction_horizons_min, default=0) > self.max_prediction_horizon_min:
            raise ValueError(
                "prediction_horizons_min contains a horizon larger than "
                "max_prediction_horizon_min"
            )
        if self.poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if self.history_length <= 0:
            raise ValueError("history_length must be positive")

        complexity_weights = (
            self.complexity_weight_density,
            self.complexity_weight_conflict,
            self.complexity_weight_heading_div,
            self.complexity_weight_alt_div,
            self.complexity_weight_type_mix,
        )
        if abs(sum(complexity_weights) - 1.0) > 1e-6:
            raise ValueError(
                "complexity_weight_* fields must sum to 1.0, got "
                f"{sum(complexity_weights):.6f}"
            )
        conflict_weights_sum = (
            self.complexity_mtca_weight_in_conflict
            + self.complexity_ltca_weight_in_conflict
        )
        if abs(conflict_weights_sum - 1.0) > 1e-6:
            raise ValueError(
                "complexity_mtca_weight_in_conflict + "
                "complexity_ltca_weight_in_conflict must sum to 1.0, got "
                f"{conflict_weights_sum:.6f}"
            )


#: Module-level default configuration instance. Most entry points (main.py,
#: tests) can simply import this rather than constructing their own, while
#: still being free to build a custom ASTRAConfig() for experiments.
DEFAULT_CONFIG = ASTRAConfig()
