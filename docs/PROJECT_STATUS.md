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
| 5 | Phase 5 | 4DARHAC detection — tracking (stateful, persists across cycles) | ⬜ Next (design ready, not built) |
| 6 | Phase 6 | 4DARHAC forecast (onset/peak/dissipation, confidence, priority) | ⬜ Planned |
| 7 | Phase 7 | AI resolution framework | ⬜ Planned |
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

---

## Remaining work (this milestone) (need update)

- [x] `demo_hotspot.py`, `demo_complexity.py`
- [x] `tests/test_hotspot.py`, `tests/test_complexity.py`
- [x] `docs/milestone_3_hotspot.md`, `docs/milestone_4_complexity.md`
- [x] Update `README.md`, `docs/architecture.md`, `Developer_Handover.md`,
      `PROJECT_STATUS.md` (this file)
- [x] Final verification (66/66 checks across both suites)

## Next milestone

**Milestone 5 — 4DARHAC detection (tracking).** Design scoped in
`docs/architecture.md §6` and `Developer_Handover.md`; not yet built.
Links `ComplexityRegion`s across poll cycles into a persistent `FourDArhac`
using centroid-overlap association, assigns a stable ID, and tracks
onset/peak/dissipation state. Explicitly stateful — the first stateful
component in the pipeline, isolated behind its own module
(`astra/prediction/` or `astra/tracking/`, naming TBD) so Milestones 1–4
remain pure and independently testable.