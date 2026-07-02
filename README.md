# ASTRA Prototype

A simplified Python re-implementation of the SESAR ASTRA concept
(**AI-enabled Tactical FMP Hotspot Prediction and Resolution**), built as
an undergraduate thesis project on top of the [BlueSky](https://github.com/TUDelft-CNS-ATM/bluesky)
open-source Air Traffic Simulator.

> **Key constraint:** BlueSky is the traffic simulator only — an external
> process. All ASTRA logic (prediction, detection, complexity, AI resolution,
> visualisation) lives in this repository.

---

## Status

| Phase | Description | Status |
|---|---|---|
| **1** | Data interface (BlueSky adapter, state model, history buffer) | ✅ Complete |
| 2 | Kinematic trajectory prediction (5/10/20/30/60 min horizons) | ⬜ Next |
| 3 | DBSCAN hotspot detection (15 NM / 1 000 ft) | ⬜ Planned |
| 4 | Per-hotspot complexity scoring (density, MTCA, heading diversity …) | ⬜ Planned |
| 5 | Hotspot lifecycle prediction (start/end time, confidence, priority) | ⬜ Planned |
| 6 | AI resolution framework (speed / FL / direct-to clearances, ranked) | ⬜ Planned |
| 7 | Live dashboard (traffic map, heatmap, hotspot table, resolutions) | ⬜ Planned |

---

## Quick start

### Offline demo (no BlueSky needed)

```bash
pip install -r requirements.txt
python demo_phase1.py
```

Creates 5 aircraft in Swiss upper airspace, polls 5 times (= 5 simulated
minutes), and prints a formatted TrafficSnapshot with separations.

### Main loop — mock mode

```bash
python main.py --mock
```

Runs the full polling loop continuously (Ctrl+C to stop). Aircraft positions
update every second.

### Main loop — live mode

```bash
# Terminal 1
python -m bluesky --headless

# Terminal 2
python main.py
```

Then load traffic into BlueSky:

```
IC scenarios/phase1_demo.scn
```

---

## Project layout

```
astra/
    interface/    Phase 1 ✅  BlueSky adapter + simulator-agnostic data model
    trajectory/   Phase 2 ⬜  Kinematic trajectory prediction
    hotspot/      Phase 3 ⬜  DBSCAN clustering
    complexity/   Phase 4 ⬜  Complexity scoring
    prediction/   Phase 5 ⬜  Hotspot lifecycle prediction
    resolution/   Phase 6 ⬜  AI clearance generation
    dashboard/    Phase 7 ⬜  Live visualisation
    utils/              Config, unit conversion, geodesy, logging

docs/architecture.md    System architecture + Mermaid diagrams
demo_phase1.py          Phase 1 offline demonstration
main.py                 Entry point  (python main.py [--mock])
Developer_Handover.md   Full developer guide, design decisions, conventions
PHASE1_CHECKLIST.md     Requirement traceability + verification results
```

---

## Architecture overview

```
BlueSky (external)  →  BlueSkyConnector  →  StateReader  →  [Phase 2–7 pipeline]
                        (or MockConnector)
```

See [`docs/architecture.md`](docs/architecture.md) for full Mermaid diagrams
(data flow, package dependency graph, poll-cycle sequence).

---

## Configuration

All tunable constants live in `astra/utils/config.py` (`ASTRAConfig`).
Defaults:

| Parameter | Default | Description |
|---|---|---|
| `bluesky_host` | `"localhost"` | BlueSky server host |
| `bluesky_recv_port` | `11000` | ZMQ receive port |
| `bluesky_send_port` | `11001` | ZMQ send port |
| `poll_interval_s` | `1.0` | Main loop poll frequency |
| `history_length` | `3600` | Snapshots retained (~1 hour at 1 Hz) |
| `separation_horizontal_nm` | `15.0` | DBSCAN ε / MTCA horizontal threshold |
| `separation_vertical_ft` | `1000.0` | Vertical separation gate |
| `prediction_horizons_min` | `[5,10,20,30,60]` | Trajectory prediction horizons |

---

## Documentation

| Document | Purpose |
|---|---|
| `README.md` | This file — setup and usage |
| `Developer_Handover.md` | Full developer guide, design decisions, conventions |
| `docs/architecture.md` | Mermaid system architecture diagrams |
| `PHASE1_CHECKLIST.md` | Phase 1 requirement traceability and verification results |
