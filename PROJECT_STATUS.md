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
| 3 | Phase 3 | DBSCAN hotspot detection (15 NM / 1 000 ft) | ⬜ Next |
| 4 | Phase 4 | Per-hotspot complexity scoring | ⬜ Planned |
| 5 | Phase 5 | Hotspot lifecycle prediction | ⬜ Planned |
| 6 | Phase 6 | AI resolution framework | ⬜ Planned |
| 7 | Phase 7 | Live dashboard | ⬜ Planned |

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

Milestone 3 — DBSCAN hotspot detection (`astra/hotspot/`), operating on both
observed `TrafficSnapshot` and predicted `PredictedSnapshot` objects
produced by `TrajectoryEngine`. Not started.
