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
class SectorDefinition:
    """A named circular airspace region for sector-level complexity.

    Defined here (not in `astra.complexity.sector`) so `ASTRAConfig` has
    no dependency on that module -- `astra.complexity.sector` imports
    this type instead, avoiding a circular import.

    Attributes:
        name: Short identifier shown in the HMI (e.g. "GVA-UPPER").
        center_lat: Circle centre latitude, decimal degrees.
        center_lon: Circle centre longitude, decimal degrees.
        radius_nm: Circle radius, nautical miles.
    """

    name: str
    center_lat: float
    center_lon: float
    radius_nm: float


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

    # ------------------------------------------------------------------
    # Phase 5 - 4DARHAC detection / tracking (astra.tracking)
    # See docs/architecture.md §6.5 and docs/milestone_5_tracking.md for
    # the association heuristic and lifecycle these thresholds drive.
    # ------------------------------------------------------------------
    #: Minimum Jaccard similarity of member_callsigns (new Cluster vs. a
    #: track's most recent entry) to accept a primary association match.
    tracking_jaccard_threshold: float = 0.5

    #: Consecutive poll cycles a track may go un-refreshed before it is
    #: closed (status -> "CLOSED") and evicted from the open-track set.
    tracking_stale_cycles: int = 3

    #: Consecutive detections required before a "CANDIDATE" track is
    #: promoted to "CONFIRMED", damping single-cycle DBSCAN noise from
    #: generating spurious tracks.
    tracking_confirm_cycles: int = 2

    #: Minimum change (0-100 scale) in complexity_score between
    #: consecutive track entries to count as "rising"/"falling" rather
    #: than flat, for GROWING/PEAK/DISSIPATING trend classification.
    tracking_trend_tolerance: float = 1.0

    # ------------------------------------------------------------------
    # Phase 6 - 4DARHAC forecast (astra.forecast)
    # See docs/milestone_6_forecast.md for the design review these
    # thresholds were approved from.
    # ------------------------------------------------------------------
    #: complexity_score above which an ARHAC counts as "active" for
    #: onset-forecasting purposes.
    forecast_onset_threshold: float = 50.0

    #: complexity_score below which an ARHAC counts as dissipated.
    #: Deliberately lower than forecast_onset_threshold (hysteresis),
    #: avoiding onset/dissipation flapping right at one boundary value.
    forecast_dissipation_threshold: float = 30.0

    #: Minimum number of matched predicted horizons this cycle before
    #: ForecastEngine attempts onset/dissipation/peak interpolation;
    #: below this, forecast fields stay None rather than guessing from
    #: too little data.
    forecast_min_matched_horizons: int = 2

    #: Time constant (seconds) for the confidence decay term -- longer
    #: estimated lead times are discounted more, reflecting the
    #: constant-velocity trajectory model's known accuracy degradation
    #: over longer horizons.
    forecast_confidence_decay_s: float = 1800.0

    # ------------------------------------------------------------------
    # Phase 7 - AI resolution framework (astra.resolution)
    # See docs/milestone_7_resolution_design_review.md (OQ-2, OQ-4, OQ-5)
    # for the approved rationale behind these values.
    # ------------------------------------------------------------------
    #: Magnitude (knots) of the speed candidate's +/- adjustment.
    resolution_speed_step_kt: float = 20.0

    #: Magnitude (feet) of the flight-level candidate's +/- adjustment.
    resolution_altitude_step_ft: float = 1000.0

    #: Magnitude (degrees) of the heading candidate's +/- adjustment.
    #: Only applied when the track's conflict components (MTCA/LTCA)
    #: are non-zero -- see docs/milestone_7_resolution_design_review.md OQ-2.
    resolution_heading_step_deg: float = 15.0

    #: Weight on complexity-delta in `resolution_score`. Sums to 1.0
    #: with `resolution_weight_deviation` / `resolution_weight_fuel`.
    resolution_weight_complexity: float = 0.6

    #: Weight (penalty) on the clearance-deviation-magnitude cost term.
    resolution_weight_deviation: float = 0.25

    #: Weight (penalty) on the fuel-cost proxy term.
    resolution_weight_fuel: float = 0.15

    #: Safety cap on how many urgency-ranked tracks are resolved per poll
    #: cycle (OQ-5) -- bounds the per-cycle cost of re-running the
    #: trajectory/cluster/complexity pipeline per candidate per track.
    resolution_max_tracks_per_cycle: int = 5

    # ------------------------------------------------------------------
    # Phase 8 - dashboard / HMI (astra.dashboard)
    # See docs/milestone_8_dashboard_design_review.md (proposed config
    # additions table) and docs/milestone_8_dashboard.md for rationale.
    # ------------------------------------------------------------------
    #: Bind address for the dashboard's local Flask web server. Never
    #: exposed beyond localhost by default -- this is a single-FMP,
    #: single-machine prototype (see Milestone 8 non-goals).
    dashboard_host: str = "127.0.0.1"

    #: Bind port for the dashboard's local Flask web server.
    dashboard_port: int = 8050

    #: Cap on ranked `ResolutionCandidate`s shown per track in the
    #: dashboard's resolution table (OQ-3) -- independent of
    #: `resolution_max_tracks_per_cycle`, which caps how many *tracks*
    #: are resolved, not how many candidates are displayed per track.
    dashboard_max_resolution_candidates_shown: int = 3

    # ------------------------------------------------------------------
    # Phase 9 - sector complexity (astra.complexity.sector)
    # Opt-in: empty by default, so existing configs/tests are unaffected.
    # ------------------------------------------------------------------
    #: Fixed circular sectors to assess each cycle for the HMI's
    #: complexity-charts page. Empty by default (feature is a no-op
    #: until sectors are defined).
    sectors: List[SectorDefinition] = field(default_factory=list)

    #: Width (seconds) of each rolling-history bucket. 300s = 5 min,
    #: matching the reference ASTRA complexity charts page.
    sector_bucket_s: float = 300.0

    #: Number of rolling buckets retained per sector (24 * 5min = 2h).
    sector_history_buckets: int = 24

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

        if not (0.0 < self.tracking_jaccard_threshold <= 1.0):
            raise ValueError(
                "tracking_jaccard_threshold must be in (0.0, 1.0], got "
                f"{self.tracking_jaccard_threshold}"
            )
        if self.tracking_stale_cycles < 1:
            raise ValueError("tracking_stale_cycles must be >= 1")
        if self.tracking_confirm_cycles < 1:
            raise ValueError("tracking_confirm_cycles must be >= 1")
        if self.tracking_trend_tolerance < 0:
            raise ValueError("tracking_trend_tolerance must be >= 0")

        if not (0.0 <= self.forecast_dissipation_threshold <= 100.0):
            raise ValueError(
                "forecast_dissipation_threshold must be in [0, 100], got "
                f"{self.forecast_dissipation_threshold}"
            )
        if not (0.0 <= self.forecast_onset_threshold <= 100.0):
            raise ValueError(
                "forecast_onset_threshold must be in [0, 100], got "
                f"{self.forecast_onset_threshold}"
            )
        if self.forecast_dissipation_threshold >= self.forecast_onset_threshold:
            raise ValueError(
                "forecast_dissipation_threshold must be strictly less than "
                "forecast_onset_threshold (hysteresis), got "
                f"{self.forecast_dissipation_threshold} >= "
                f"{self.forecast_onset_threshold}"
            )
        if self.forecast_min_matched_horizons < 1:
            raise ValueError("forecast_min_matched_horizons must be >= 1")
        if self.forecast_confidence_decay_s <= 0:
            raise ValueError("forecast_confidence_decay_s must be > 0")

        if self.resolution_speed_step_kt <= 0:
            raise ValueError("resolution_speed_step_kt must be > 0")
        if self.resolution_altitude_step_ft <= 0:
            raise ValueError("resolution_altitude_step_ft must be > 0")
        if self.resolution_heading_step_deg <= 0:
            raise ValueError("resolution_heading_step_deg must be > 0")
        resolution_weights = (
            self.resolution_weight_complexity,
            self.resolution_weight_deviation,
            self.resolution_weight_fuel,
        )
        if abs(sum(resolution_weights) - 1.0) > 1e-6:
            raise ValueError(
                "resolution_weight_* fields must sum to 1.0, got "
                f"{sum(resolution_weights):.6f}"
            )
        if self.resolution_max_tracks_per_cycle < 1:
            raise ValueError("resolution_max_tracks_per_cycle must be >= 1")

        if not (0 < self.dashboard_port <= 65535):
            raise ValueError("dashboard_port must be in (0, 65535], got " f"{self.dashboard_port}")
        if self.dashboard_max_resolution_candidates_shown < 1:
            raise ValueError("dashboard_max_resolution_candidates_shown must be >= 1")

        if self.sector_bucket_s <= 0:
            raise ValueError("sector_bucket_s must be > 0")
        if self.sector_history_buckets < 1:
            raise ValueError("sector_history_buckets must be >= 1")
        names = [s.name for s in self.sectors]
        if len(names) != len(set(names)):
            raise ValueError("sectors must have unique names")
        for sector in self.sectors:
            if sector.radius_nm <= 0:
                raise ValueError(f"sector {sector.name!r} radius_nm must be > 0")


#: Module-level default configuration instance. Most entry points (main.py,
#: tests) can simply import this rather than constructing their own, while
#: still being free to build a custom ASTRAConfig() for experiments.
DEFAULT_CONFIG = ASTRAConfig()
