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
| 7 | Phase 7 | AI resolution framework (speed / FL / heading, ranked) | ✅ Complete |
| 8 | Phase 8 | Dashboard / HMI (Flask, live map + hotspot table + timeline + resolutions) | ✅ Complete |

> **Reorganized by architecture review, July 2026.** The original Phase 3
> ("hotspot detection") conflated stateless spatial clustering with the
> stateful problem of tracking a 4DARHAC's identity across prediction
> horizons and poll cycles. It has been split into Phases 3–6. See
> `docs/architecture.md §6` for the domain model
> (`Cluster` / `ComplexityRegion` / `FourDArhac`) and full rationale.

> **Note on `main.py`.** The "`main.py` deliberately left as a Phase 1
> demonstration only" bullets under Milestones 5–7 below describe
> `main.py`'s status *at the time those milestones were built* — that
> was a deliberate, reviewed choice to keep each milestone's demo
> isolated (`demo_tracking.py`, `demo_forecast.py`, `demo_resolution.py`)
> rather than repeatedly reworking the shared entry point. Milestone 8
> below is where `main.py` was finally wired to the full pipeline via
> `astra.pipeline.Pipeline`, becoming ASTRA's real application entry
> point.

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

## Milestone 7 — AI Resolution Framework ✅ Complete

- `astra/resolution/models.py` — `ResolutionCandidate`,
  `ResolutionSet` (composes `FourDArhac`, not a field on it)
- `astra/resolution/candidates.py` — pure: `select_target_aircraft()`,
  `heading_lever_applicable()`, `generate_candidates()` (speed / FL
  always; heading only when a conflict component is present); builds
  hypothetical `TrafficSnapshot`s, never mutates the live one
- `astra/resolution/engine.py` — `ResolutionEngine.resolve()` /
  `.resolve_many()`, stateless; evaluates each candidate by replaying
  `TrajectoryEngine` → `ClusterEngine` → `ComplexityEngine` on the
  hypothetical snapshot at the track's single closest horizon to
  `predicted_onset_s`, then re-associates via
  `astra.tracking.association.best_cluster_match`
- `ASTRAConfig` — `resolution_speed_step_kt`,
  `resolution_altitude_step_ft`, `resolution_heading_step_deg`,
  `resolution_weight_complexity/deviation/fuel` (validated to sum to
  `1.0`), `resolution_max_tracks_per_cycle` (all validated)
- Design rationale, including the five design decisions resolved from
  the original design review and the pre-formal-test smoke test that
  confirmed pipeline wiring: `docs/milestone_7_resolution.md`
- Verification: `tests/test_resolution.py` — 39/39 checks pass
- `demo_resolution.py` — extends `demo_forecast.py`'s scripted scenario
  with a converging 3-aircraft geometry that crosses the forecast onset
  threshold on its 5-minute predicted horizon, printing
  `ResolutionEngine`'s ranked candidate clearances each cycle
- `main.py` deliberately left as a Phase 1 demonstration only, matching
  the precedent set by Milestones 2–6

## Milestone 8 — Dashboard / HMI ✅ Complete

- `astra/pipeline.py` — small, in-scope fix found while verifying the
  Milestone 8 design review's assumptions: `CycleResult` now also
  carries the raw `PredictionResult` (not just the derived
  `ComplexityRegion`s), computed once and threaded through, so a
  presentation layer can render predicted aircraft positions without
  recomputing anything.
- `astra/dashboard/models.py` — `DashboardSnapshot`, the dashboard's own
  tiny read-model (latest `CycleResult`, cycle count, staleness)
- `astra/dashboard/store.py` — `CycleStore`, the one new concurrency
  primitive this milestone introduces: a lock around "the last
  `CycleResult`", written by `main.py`'s poll loop and read by Flask's
  request-handling thread(s)
- `astra/dashboard/serializers.py` — pure functions turning Milestone
  1–7 domain objects into JSON; the only new "logic" module
- `astra/dashboard/routes.py` / `server.py` — a minimal Flask app
  (`/` HMI shell, `/state` JSON) that never imports an engine or the
  `Pipeline` directly, only `CycleStore` + `serializers`
- `astra/dashboard/templates/index.html`, `static/css/dashboard.css`,
  `static/js/dashboard.js` — the HMI screen itself: a canvas plan-view
  (observed traffic, dashed predicted trajectories, complexity-heatmap
  circles), a 4DARHAC hotspot table, an onset/peak/dissipation timeline
  per track, and a ranked-resolution-candidates panel. Auto-updates by
  polling `/state` every `poll_interval_s` (served by the backend, not
  hard-coded in the frontend)
- `ASTRAConfig` — `dashboard_host`, `dashboard_port`,
  `dashboard_max_resolution_candidates_shown` (all validated)
- `main.py` — now the real application entry point: runs
  `Pipeline.run_cycle()` every poll cycle, publishes each `CycleResult`
  into a `CycleStore`, and starts the dashboard's Flask server in a
  background thread (`--no-dashboard` to opt out and keep the
  console-only loop)
- Design decisions resolved from the design review (module layout,
  `main.py` as the live-loop owner, the resolution-candidate display
  cap, the live-only heatmap, polling as the update mechanism) and the
  clean API boundary that lets a future BlueSky live run or RL-based
  `ResolutionEngine` plug in without touching dashboard code:
  `docs/milestone_8_dashboard.md`
- Verification: `tests/test_dashboard.py` — 70/70 checks pass (pure
  serializer unit tests + `Pipeline`/`CycleStore`/Flask `test_client`
  integration checks); full regression suite (Milestones 3–8 combined,
  `test_hotspot.py` + `test_complexity.py` + `test_tracking.py` +
  `test_forecast.py` + `test_resolution.py` + `test_dashboard.py`):
  266/266 checks pass
- Live demonstration: `python main.py --mock` — no separate
  `demo_dashboard.py` was added, since `main.py` *is* Milestone 8's
  live, browser-visible demonstration (open `http://127.0.0.1:8050/`
  while it runs)

---

## Remaining work

**All 8 milestones are complete.** No further work is currently
planned for this prototype. Natural next steps, out of scope for this
prototype and explicitly not started (see `docs/milestone_8_dashboard.md`
"Explicit non-goals" and `docs/architecture.md` §6.8): a live-BlueSky
demonstration run (vs. today's mock-mode demonstrations), and an
RL-based `ResolutionEngine` replacement or supplement. Both were
designed for from Milestone 8's API boundary but neither is built.
