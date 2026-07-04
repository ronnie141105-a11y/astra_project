# ASTRA Project Status

Overall milestone status for the ASTRA prototype. See `Developer_Handover.md`
for design decisions and conventions.

---

## Milestone summary

| Milestone | Phase | Description | Status |
|---|---|---|---|
| 1 | Phase 1 | Data interface — BlueSky adapter, state model, history buffer, DI, verification | ✅ Complete |
| 2 | Phase 2 | Trajectory prediction — constant-velocity kinematic engine | ✅ Complete |
| 3 | Phase 3 | Cluster detection (DBSCAN, 15 NM / 1 000 ft, stateless) | ✅ Complete |
| 4 | Phase 4 | Complexity assessment (density, MTCA/LTCA, heading/altitude diversity, type mix) | ✅ Complete |
| 5 | Phase 5 | 4DARHAC detection — tracking (stateful, persists across cycles) | ✅ Complete |
| 6 | Phase 6 | 4DARHAC forecast (onset/peak/dissipation, confidence, urgency rank) | ✅ Complete |
| 7 | Phase 7 | AI resolution framework | ⬜ Design review pending |
| 8 | Phase 8 | Live dashboard | ⬜ Planned |

> **Reorganized by architecture review, July 2026.** The original Phase 3
> ("hotspot detection") conflated stateless spatial clustering with the
> stateful problem of tracking a 4DARHAC's identity across prediction
> horizons and poll cycles. It has been split into Phases 3–6. See
> `docs/architecture.md §6` for the domain model
> (`Cluster` / `ComplexityRegion` / `FourDArhac`) and full rationale.

---

## Milestone 1 — Infrastructure ✅ Complete

- `BlueSkyConnector` — live BlueSky ZMQ adapter
- `MockConnector` — offline dead-reckoning simulator
- `StateReader` — history buffer + `for_bluesky()` / `for_mock()` factories
- `TrafficSnapshot`, `AircraftState` — simulator-agnostic data model
- `astra/utils/geodesy.py` — haversine distance, bearing, dead-reckoning
- Dependency injection via `ConnectorProtocol`
- `demo_phase1.py` — offline Phase 1 demonstration

## Milestone 2 — Trajectory Prediction ✅ Complete

- `astra/trajectory/models.py` — `PredictedSnapshot`, `PredictionResult`
- `astra/trajectory/engine.py` — `TrajectoryEngine`, deterministic
  constant-velocity trajectory prediction
- Prediction horizons: 5, 10, 15, 30, 60 minutes
- `demo_trajectory.py` — offline Phase 2 demonstration

## Milestone 3 — Cluster Detection ✅ Complete

- `astra/hotspot/distance.py` — precomputed horizontal+vertical-gated
  distance matrix for DBSCAN
- `astra/hotspot/models.py` — `Cluster` (frozen, per-instant, no
  cross-cycle identity)
- `astra/hotspot/engine.py` — `ClusterEngine.detect()` /`.detect_all()`
- Design rationale: `docs/milestone_3_hotspot.md`
- Verification: `tests/test_hotspot.py` — 24/24 checks pass
- `demo_hotspot.py` — persistent + transient cluster scenarios across
  the observed snapshot and every predicted horizon

## Milestone 4 — Complexity Assessment ✅ Complete

- `astra/complexity/stats.py` — circular/linear standard deviation
- `astra/complexity/conflict.py` — CPA-based MTCA/LTCA pairwise counting
- `astra/complexity/models.py` — `ComplexityRegion` (composes `Cluster`)
- `astra/complexity/engine.py` — `ComplexityEngine.assess()` /
  `.assess_many()`, 0–100 weighted-combination scoring
- `astra.utils.geodesy.local_tangent_plane_nm` — CPA projection helper
- `ASTRAConfig` — MTCA/LTCA thresholds, normalisation references,
  combination weights (validated to sum to 1.0)
- Design rationale: `docs/milestone_4_complexity.md`
- Verification: `tests/test_complexity.py` — 42/42 checks pass
- `demo_complexity.py` — high- vs. low-complexity scenario across the
  observed snapshot and every predicted horizon

## Milestone 5 — 4DARHAC Detection / Tracking ✅ Complete

- `astra/tracking/models.py` — `FourDArhac` (mutable, stateful),
  `ArhacStatus` lifecycle literal
- `astra/tracking/association.py` — pure Jaccard-similarity and
  centroid/extent-overlap match heuristics
- `astra/tracking/engine.py` — `TrackerEngine.update()`, the pipeline's
  first stateful component: holds open tracks across poll cycles,
  associates new observations, derives lifecycle status from the
  `complexity_score` trend, closes stale tracks
- `ASTRAConfig` — `tracking_jaccard_threshold`, `tracking_stale_cycles`,
  `tracking_confirm_cycles`, `tracking_trend_tolerance` (all validated)
- Design rationale, including the three concrete decisions made beyond
  the original build plan (horizon-0-only identity, greedy one-to-one
  association, trend-based status FSM): `docs/milestone_5_tracking.md`
- Verification: `tests/test_tracking.py` — 44/44 checks pass
- `demo_tracking.py` — scripted multi-poll-cycle scenario walking a
  `FourDArhac` through its full lifecycle (`CANDIDATE → CONFIRMED →
  GROWING → PEAK → DISSIPATING → CLOSED`)
- `main.py` deliberately left as a Phase 1 demonstration only, matching
  the precedent set by Milestones 2–4 (see `docs/milestone_5_tracking.md`
  "`main.py` — deliberately not integrated")

## Milestone 6 — 4DARHAC Forecast ✅ Complete

- `astra/forecast/horizon_series.py` — pure: builds a track's per-cycle
  `(time_s, complexity_score)` series (observed anchor + matched
  predicted horizons), reusing `astra.tracking.association.best_cluster_match`
- `astra/forecast/projection.py` — pure: `linear_crossing_time()`,
  `predicted_peak()`
- `astra/forecast/engine.py` — `ForecastEngine.forecast()` /
  `.forecast_many()`, stateless; mutates the `FourDArhac` objects
  `TrackerEngine` owns after `TrackerEngine.update()` runs each cycle
- `astra/tracking/models.py` — `FourDArhac` gains two fields beyond the
  Milestone 5 schema: `predicted_peak_time_s` and
  `forecast_urgency_rank` (kept separate from `priority`, see
  `docs/milestone_6_forecast.md` OQ-4)
- `ASTRAConfig` — `forecast_onset_threshold`,
  `forecast_dissipation_threshold`, `forecast_min_matched_horizons`,
  `forecast_confidence_decay_s` (all validated)
- Design rationale, including the five design decisions resolved from
  the original design review and one real defect found while
  integrating `demo_forecast.py`: `docs/milestone_6_forecast.md`
- Verification: `tests/test_forecast.py` — 47/47 checks pass
- `demo_forecast.py` — extends `demo_tracking.py`'s scripted scenario
  with onset/dissipation/peak-time/confidence/urgency-rank output
  alongside the existing trend status
- `main.py` deliberately left as a Phase 1 demonstration only, matching
  the precedent set by Milestones 2–5

---

## Remaining work (this milestone)

- [x] `demo_forecast.py`
- [x] `tests/test_forecast.py`
- [x] `docs/milestone_6_forecast.md`
- [x] Update `README.md`, `docs/architecture.md`, `Developer_Handover.md`,
      `PROJECT_STATUS.md` (this file)
- [x] Final verification (157/157 checks across Milestones 3–6;
      287/287 across all six milestones)

**Milestone 6 is complete.** No further work remains for this phase of
the project.

## Next milestone

**Milestone 7 — AI resolution framework.** An engineering design review
has been prepared and is pending approval before implementation begins —
see `docs/milestone_7_resolution_design_review.md`. It covers candidate
clearance generation (speed / FL / direct-to / heading) and
multi-objective ranking, consuming the `FourDArhac` forecasts Milestone 6
now produces. Implementation is explicitly on hold until the review is
approved.
