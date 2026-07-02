# ASTRA Project Status

Overall milestone status for the ASTRA prototype. See `PHASE1_CHECKLIST.md`
for detailed Phase 1 requirement traceability, and `Developer_Handover.md`
for design decisions and conventions.

---

## Milestone summary

| Milestone | Phase | Description | Status |
|---|---|---|---|
| 1 | Phase 1 | Data interface — BlueSky adapter, state model, history buffer, DI, verification | ✅ Complete |
| 2 | Phase 2 | Trajectory prediction — constant-velocity kinematic engine | ✅ Complete |
| 3 | Phase 3 | Cluster detection (DBSCAN, 15 NM / 1 000 ft, stateless) | ⬜ Next |
| 4 | Phase 4 | Complexity assessment (density, MTCA, heading/altitude diversity) | ⬜ Planned |
| 5 | Phase 5 | 4DARHAC detection — tracking (stateful, persists across cycles) | ⬜ Planned |
| 6 | Phase 6 | 4DARHAC forecast (onset/peak/dissipation, confidence, priority) | ⬜ Planned |
| 7 | Phase 7 | AI resolution framework | ⬜ Planned |
| 8 | Phase 8 | Live dashboard | ⬜ Planned |

> **Reorganized by architecture review, July 2026.** The original Phase 3
> ("hotspot detection") conflated stateless spatial clustering with the
> stateful problem of tracking a 4DARHAC's identity across prediction
> horizons and poll cycles. It has been split into Phases 3–6. See
> `docs/architecture.md §6` for the domain model
> (`Cluster` / `ComplexityRegion` / `FourDArhac`) and full rationale. This
> is a documentation-only change — no code has been restructured yet.

---

## Milestone 1 — Infrastructure ✅ Complete

- `BlueSkyConnector` — live BlueSky ZMQ adapter
- `MockConnector` — offline dead-reckoning simulator
- `StateReader` — history buffer + `for_bluesky()` / `for_mock()` factories
- `TrafficSnapshot`, `AircraftState` — simulator-agnostic data model
- `astra/utils/geodesy.py` — haversine distance, bearing, dead-reckoning
- Dependency injection via `ConnectorProtocol`
- Verification: syntax, imports, dependency graph, BlueSky compatibility,
  MockConnector/StateReader/BlueSkyConnector functional tests, geodesy unit
  tests (see `PHASE1_CHECKLIST.md` for the full breakdown)
- `demo_phase1.py` — offline Phase 1 demonstration

## Milestone 2 — Trajectory Prediction ✅ Complete

- `astra/trajectory/models.py` — `PredictedSnapshot`, `PredictionResult`
  (frozen dataclasses; `PredictedSnapshot` mirrors the `TrafficSnapshot`
  accessor API for drop-in compatibility with later phases)
- `astra/trajectory/engine.py` — `TrajectoryEngine`, deterministic
  constant-velocity trajectory prediction (great-circle dead-reckoning
  horizontally via `move_position()`, linear extrapolation vertically)
- Prediction horizons: 5, 10, 15, 30, 60 minutes
  (`ASTRAConfig.prediction_horizons_min = [5, 10, 15, 30, 60]`)
- Verification: syntax, imports, dependency graph, numerical verification
  (predictions cross-checked against equivalent `MockConnector.poll()`
  sequences)
- `demo_trajectory.py` — offline Phase 2 demonstration: five aircraft, one
  observed `TrafficSnapshot`, predicted-position tables at every configured
  horizon

---

## Remaining work (this milestone)

- [x] `demo_trajectory.py`
- [x] Update `README.md`
- [x] Update `Developer_Handover.md`
- [x] Update `PROJECT_STATUS.md` (this file)
- [x] Final verification

## Next milestone

Milestone 3 — Cluster detection (proposed home: `astra/hotspot/`, pending
the rename/split discussed in the architecture review). A stateless DBSCAN
pass over each `TrafficSnapshot` / `PredictedSnapshot` (observed + all five
horizons), producing `Cluster` objects as defined in
`docs/architecture.md §6`. Deliberately excludes cross-horizon /
cross-cycle tracking, which is scoped as its own milestone (5 — 4DARHAC
detection) given its different (stateful) nature. Not started.
