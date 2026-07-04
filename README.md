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

| Milestone | Description | Status |
|---|---|---|
| **1** | Data interface (BlueSky adapter, state model, history buffer) | ✅ Complete |
| **2** | Kinematic trajectory prediction (5/10/15/30/60 min horizons) | ✅ Complete |
| **3** | Cluster detection (DBSCAN, 15 NM / 1 000 ft, stateless) | ✅ Complete |
| **4** | Complexity assessment (density, MTCA/LTCA, heading/altitude diversity, type mix) | ✅ Complete |
| **5** | 4DARHAC detection — tracking (stateful, persists across cycles) | ✅ Complete |
| **6** | 4DARHAC forecast (onset/peak/dissipation, confidence, urgency rank) | ✅ Complete |
| **7** | AI resolution framework (speed / FL / heading clearances, ranked) | ✅ Complete |
| **8** | Dashboard / HMI (Flask; live map, heatmap, hotspot table, timeline, resolutions) | ✅ Complete |

> Milestones 3–8 were reorganized by an architecture review (July 2026): the
> original single "hotspot detection" phase conflated stateless spatial
> clustering with the stateful problem of tracking a 4DARHAC's identity
> across prediction horizons and poll cycles. See
> [`docs/architecture.md §6`](docs/architecture.md#6-4darhac-domain-model-and-revised-pipeline)
> for the domain model and rationale, and `docs/milestone_3_hotspot.md` /
> `docs/milestone_4_complexity.md` / `docs/milestone_5_tracking.md` /
> `docs/milestone_6_forecast.md` / `docs/milestone_7_resolution.md` /
> `docs/milestone_8_dashboard.md` for the as-built design of Milestones 3–8.

---

## Quick start

### Offline demos (no BlueSky needed)

```bash
pip install -r requirements.txt
python tests/demo_phase1.py         # Milestone 1 — state interface
python tests/demo_trajectory.py     # Milestone 2 — trajectory prediction
python tests/demo_hotspot.py        # Milestone 3 — cluster detection
python tests/demo_complexity.py     # Milestone 4 — complexity assessment
python tests/demo_tracking.py       # Milestone 5 — 4DARHAC tracking
python tests/demo_forecast.py       # Milestone 6 — 4DARHAC forecast
python tests/demo_resolution.py     # Milestone 7 — AI resolution framework
```

Run each from the project root (all demo scripts add the project root to
`sys.path` themselves).

Each script is self-contained: it creates a small synthetic traffic
scenario with `MockConnector`, runs that milestone's pipeline stage (and
every stage before it), and prints formatted results to the console.
Milestone 8 has no `demo_dashboard.py` — its live demonstration is
`main.py` itself (see "Main loop — mock mode" below).

### Regression tests

```bash
python tests/test_hotspot.py      # Milestone 3 — 24 checks
python tests/test_complexity.py   # Milestone 4 — 42 checks
python tests/test_tracking.py     # Milestone 5 — 44 checks
python tests/test_forecast.py     # Milestone 6 — 47 checks
python tests/test_resolution.py   # Milestone 7 — 39 checks
python tests/test_dashboard.py    # Milestone 8 — 70 checks
```

No BlueSky process or third-party test framework required.

### Main loop — mock mode

```bash
python main.py --mock
```

Runs the full polling loop continuously (Ctrl+C to stop). Aircraft positions
update every second. This also opens the dashboard at
`http://127.0.0.1:8050/` — open it in a browser to see the live traffic
map, predicted trajectories, 4DARHAC hotspot table/timeline, and ranked
resolution candidates update every `poll_interval_s`. Pass
`--no-dashboard` to run the console-only loop instead.

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

As with mock mode, the dashboard opens automatically at
`http://127.0.0.1:8050/` unless `--no-dashboard` is passed.

---

## Project layout

```
astra/
    interface/    Milestone 1 ✅  BlueSky adapter + simulator-agnostic data model
    trajectory/   Milestone 2 ✅  Kinematic trajectory prediction
    hotspot/      Milestone 3 ✅  Cluster detection (DBSCAN)
    complexity/   Milestone 4 ✅  Complexity assessment (density, conflicts, diversity)
    tracking/     Milestone 5 ✅  4DARHAC detection (tracking) — stateful
    forecast/     Milestone 6 ✅  4DARHAC forecast — onset/peak/dissipation, confidence
    resolution/   Milestone 7 ✅  AI clearance generation — speed/FL/heading, ranked
    dashboard/    Milestone 8 ✅  Flask dashboard / HMI — read-only, map/table/timeline
    pipeline.py         Pipeline.run_cycle() -> CycleResult, the shared entry point
    utils/              Config, unit conversion, geodesy, logging

docs/architecture.md            System architecture + Mermaid diagrams
docs/milestone_3_hotspot.md     Milestone 3 design rationale
docs/milestone_4_complexity.md  Milestone 4 design rationale
docs/milestone_5_tracking.md    Milestone 5 design rationale
docs/milestone_6_forecast.md    Milestone 6 design rationale
docs/milestone_7_resolution.md  Milestone 7 design rationale (as built)
docs/milestone_8_dashboard_design_review.md  Milestone 8 original design review (superseded)
docs/milestone_8_dashboard.md   Milestone 8 design rationale (as built)
tests/                          Regression tests + offline demos (Milestones 1–8)
tests/demo_phase1.py            Milestone 1 offline demonstration
tests/demo_trajectory.py        Milestone 2 offline demonstration
tests/demo_hotspot.py           Milestone 3 offline demonstration
tests/demo_complexity.py        Milestone 4 offline demonstration
tests/demo_tracking.py          Milestone 5 offline demonstration
tests/demo_forecast.py          Milestone 6 offline demonstration
tests/demo_resolution.py        Milestone 7 offline demonstration
tests/test_dashboard.py         Milestone 8 regression suite (70 checks)
main.py                         Real application entry point (python main.py [--mock] [--no-dashboard])
docs/Developer_Handover.md      Full developer guide, design decisions, conventions
docs/PROJECT_STATUS.md          Overall milestone status
```

---

## Architecture overview

```
BlueSky (external)  →  BlueSkyConnector  →  StateReader  →  [Milestone 2–8 pipeline]
                        (or MockConnector)
```

See [`docs/architecture.md`](docs/architecture.md) for full Mermaid diagrams
(data flow, package dependency graph, poll-cycle sequence, domain model).

---

## Configuration

All tunable constants live in `astra/utils/config.py` (`ASTRAConfig`).
Selected defaults:

| Parameter | Default | Description |
|---|---|---|
| `bluesky_host` | `"localhost"` | BlueSky server host |
| `poll_interval_s` | `1.0` | Main loop poll frequency |
| `history_length` | `3600` | Snapshots retained (~1 hour at 1 Hz) |
| `separation_horizontal_nm` | `15.0` | DBSCAN ε / MTCA horizontal threshold |
| `separation_vertical_ft` | `1000.0` | Vertical separation gate |
| `prediction_horizons_min` | `[5,10,15,30,60]` | Trajectory prediction horizons |
| `mtca_distance_nm` / `mtca_time_min` | `5.5` / `2.5` | MTCA conflict thresholds |
| `ltca_distance_nm` / `ltca_time_min` | `7.9` / `15.0` | LTCA conflict thresholds |
| `complexity_weight_*` | sums to `1.0` | Complexity sub-score combination weights |
| `tracking_jaccard_threshold` | `0.5` | Min. member-callsign overlap to associate a track |
| `tracking_stale_cycles` | `3` | Poll cycles a track may go un-refreshed before closing |
| `tracking_confirm_cycles` | `2` | Consecutive detections before CANDIDATE → CONFIRMED |
| `forecast_onset_threshold` | `50.0` | `complexity_score` above which an ARHAC counts as "active" for onset purposes |
| `forecast_dissipation_threshold` | `30.0` | `complexity_score` below which an ARHAC counts as dissipated (hysteresis vs. onset threshold) |
| `forecast_min_matched_horizons` | `2` | Minimum matched predicted horizons before attempting interpolation |
| `dashboard_host` | `"127.0.0.1"` | Bind address for the dashboard's local Flask server |
| `dashboard_port` | `8050` | Bind port for the dashboard's local Flask server |
| `dashboard_max_resolution_candidates_shown` | `3` | Cap on ranked resolution candidates displayed per track |

See `astra/utils/config.py` for the full field list (validated in
`ASTRAConfig.__post_init__`).

---

## Documentation

| Document | Purpose |
|---|---|
| `README.md` | This file — setup and usage |
| `docs/Developer_Handover.md` | Full developer guide, design decisions, conventions |
| `docs/architecture.md` | Mermaid system architecture diagrams + domain model |
| `docs/milestone_3_hotspot.md` | Milestone 3 (cluster detection) design rationale |
| `docs/milestone_4_complexity.md` | Milestone 4 (complexity assessment) design rationale |
| `docs/milestone_5_tracking.md` | Milestone 5 (4DARHAC tracking) design rationale |
| `docs/milestone_6_forecast.md` | Milestone 6 (4DARHAC forecast) design rationale |
| `docs/milestone_7_resolution.md` | Milestone 7 (AI resolution) design rationale (as built) |
| `docs/milestone_8_dashboard.md` | Milestone 8 (dashboard / HMI) design rationale (as built) |
| `docs/PROJECT_STATUS.md` | Overall milestone status |