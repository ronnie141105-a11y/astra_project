# Developer Handover — ASTRA Prototype

## Project summary

ASTRA is a simplified Python re-implementation of the SESAR ASTRA concept
(AI-enabled Tactical FMP Hotspot Prediction and Resolution), built as a
university undergraduate thesis project.

**Core constraint:** BlueSky is **only** the traffic simulator — an external
process. All prediction, detection, complexity, AI and visualisation logic
lives in this repository. The two processes communicate over ZeroMQ using
BlueSky's built-in network API.

**Current status:** Milestones 1–8 — data interface, trajectory
prediction, cluster detection, complexity assessment, 4DARHAC tracking,
4DARHAC forecast, AI resolution, and the dashboard/HMI — are **complete
and verified** (130/130 checks from Milestones 1–2, plus 24/24, 42/42,
44/44, 47/47, 39/39, and 70/70 from Milestones 3–8 — 396/396 total).
See `docs/milestone_8_dashboard.md` for the dashboard's as-built
rationale.

---

## Repository layout

```
astra_project/
│
├── astra/                     Core Python package
│   ├── interface/             Phase 1 ✅ — BlueSky adapter + data model
│   │   ├── traffic_state.py       AircraftState (frozen), TrafficSnapshot
│   │   ├── connector_base.py      ConnectorProtocol (typing.Protocol)
│   │   ├── bluesky_connector.py   Live connector (BlueSky ZMQ)
│   │   ├── mock_connector.py      Offline connector (dead-reckoning)
│   │   ├── state_reader.py        History buffer + factory methods
│   │   └── type_registry.py       Callsign→type cache (BlueSky workaround)
│   │
│   ├── utils/                 Zero-dependency foundation layer
│   │   ├── config.py              ASTRAConfig frozen dataclass
│   │   ├── units.py               SI ↔ ATM unit converters
│   │   ├── geodesy.py             Haversine, bearing, dead-reckoning
│   │   └── logger.py              Shared logging setup
│   │
│   ├── trajectory/            Milestone 2 ✅ — kinematic trajectory prediction
│   │   ├── models.py              PredictedSnapshot, PredictionResult
│   │   └── engine.py              TrajectoryEngine (constant-velocity)
│   │
│   ├── hotspot/                Milestone 3 ✅ — cluster detection
│   │   ├── distance.py            Precomputed horiz+vert-gated distance matrix
│   │   ├── models.py              Cluster (frozen, per-instant)
│   │   └── engine.py              ClusterEngine (detect() / detect_all())
│   │
│   ├── complexity/            Milestone 4 ✅ — per-cluster complexity assessment
│   │   ├── stats.py               Circular/linear standard deviation
│   │   ├── conflict.py            CPA-based MTCA/LTCA pairwise counting
│   │   ├── models.py              ComplexityRegion (composes Cluster)
│   │   └── engine.py              ComplexityEngine (assess() / assess_many())
│   │
│   ├── tracking/               Milestone 5 ✅ — 4DARHAC detection (tracking); stateful
│   │   ├── models.py              FourDArhac (mutable), ArhacStatus lifecycle literal
│   │   ├── association.py         Jaccard / centroid-extent match heuristics (pure)
│   │   └── engine.py              TrackerEngine (update() — stateful across poll cycles)
│   │
│   ├── forecast/                Milestone 6 ✅ — 4DARHAC forecast; stateless, reads tracks
│   │   ├── horizon_series.py      Per-track predicted-horizon series (pure)
│   │   ├── projection.py          Threshold-crossing / peak math (pure)
│   │   └── engine.py              ForecastEngine (forecast() / forecast_many())
│   │
│   ├── resolution/              Milestone 7 ✅ — clearance generation + ranking; stateless
│   │   ├── models.py              ResolutionCandidate, ResolutionSet (composes FourDArhac)
│   │   ├── candidates.py          Candidate generation + hypothetical snapshots (pure)
│   │   └── engine.py              ResolutionEngine (resolve() / resolve_many())
│   ├── dashboard/                Milestone 8 ✅ — Flask dashboard / HMI; read-only
│   │   ├── models.py              DashboardSnapshot (dashboard's own tiny read-model)
│   │   ├── store.py               CycleStore (thread-safe latest-CycleResult bridge)
│   │   ├── serializers.py         Pure functions: pipeline domain objects -> JSON
│   │   ├── routes.py              Flask Blueprint ("/" HMI shell + "/state" JSON)
│   │   ├── server.py              Flask app factory + run_dashboard_in_background()
│   │   ├── templates/index.html   HMI page shell
│   │   └── static/{css,js}/       dashboard.css, dashboard.js (polls /state)
│   │
│   └── pipeline.py             Pipeline.run_cycle() -> CycleResult (single pipeline entry point)
│
├── docs/
│   ├── architecture.md            Mermaid diagrams (full system + dep graph + domain model)
│   ├── PROJECT_STATUS.md          Overall milestone status
│   └── Developer_Handover.md      This file
│
├── scenarios/
│   └── phase1_demo.scn        BlueSky scenario file (4 aircraft, live mode)
│
├── tests/
│   ├── test_hotspot.py            Milestone 3 regression suite (24 checks)
│   ├── test_complexity.py         Milestone 4 regression suite (42 checks)
│   ├── test_tracking.py           Milestone 5 regression suite (44 checks)
│   ├── test_forecast.py           Milestone 6 regression suite (47 checks)
│   ├── test_resolution.py         Milestone 7 regression suite (39 checks)
│   └── test_dashboard.py          Milestone 8 regression suite (70 checks)
│
├── main.py                    Real application entry point — runs Pipeline.run_cycle()
│                               every poll cycle and starts the dashboard (--mock / --no-dashboard)
├── requirements.txt           pip install -r requirements.txt
└── README.md                  User-facing setup and usage guide
```

---

## Environment setup

```bash
# Clone / enter the project
cd astra_project

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
```

`requirements.txt` lists `bluesky-simulator` plus `numpy` and
`scikit-learn` (added for Milestone 3's DBSCAN clustering; Milestones
5–7's tracking/forecast/resolution modules reuse `numpy`/stdlib only, no
new dependency) and `flask` (added for Milestone 8's dashboard — the
one new third-party dependency it introduces).

---

## Running the project

### Option A — Offline mock mode (no BlueSky needed)

```bash
python main.py --mock
```

The `MockConnector` generates synthetic traffic. Aircraft move via
great-circle dead-reckoning. Useful for developing Phases 2–7 on a laptop
with no BlueSky installation. Since Milestone 8, this also opens the
dashboard at `http://127.0.0.1:8050/` (add `--no-dashboard` for the
console-only loop every prior milestone used).

### Option B — Live mode (requires BlueSky)

```bash
# Terminal 1 — start BlueSky headless
python -m bluesky --headless

# Terminal 2 — start ASTRA
python main.py
```

BlueSky will print its port numbers (default 11000 / 11001). ASTRA
connects automatically using the defaults in `ASTRAConfig`. As with
mock mode, the dashboard opens automatically at
`http://127.0.0.1:8050/` unless `--no-dashboard` is passed.

To load the bundled demo scenario into the running simulation, from the
BlueSky console or a third terminal:

```
IC scenarios/phase1_demo.scn
```

---

## Running the verification suite

The suite is split by section to avoid shell timeouts:

```bash
# V1+V2: syntax and imports
python3 -c "
import ast, sys, importlib
from pathlib import Path
sys.path.insert(0,'.')
py_files = [p for p in Path('.').rglob('*.py') if '__pycache__' not in str(p)]
errors = [(f, ast.parse(f.read_text())) for f in py_files if True]
print('V1 OK — all files parse')
for m in ['astra.utils.config','astra.utils.units','astra.utils.geodesy',
          'astra.utils.logger','astra.interface.traffic_state',
          'astra.interface.type_registry','astra.interface.connector_base',
          'astra.interface.mock_connector','astra.interface.state_reader',
          'astra.interface.bluesky_connector']:
    importlib.import_module(m)
    print(f'  import {m}  OK')
print('V2 OK')
"

# V3: dependency graph (see docs/architecture.md for expected edges)
# V5a: MockConnector functional
# V5b: StateReader
# V5c: BlueSkyConnector ACDATA decoding
# V6: geodesy

# Quick combined run (all currently pass):
python tests/demo_phase1.py   # implicit V5a+V5b integration test
```

Full automated suite results as of Phase 1 completion:
- V1 Syntax: **20/20 files PASS**
- V2 Imports: **11/11 modules PASS**
- V3 Dep graph: **7/7 checks PASS**  (no cycles, layering clean, BlueSky confined)
- V4 BlueSky compat: **10/10 checks PASS** (verified against installed source)
- V5a MockConnector: **39/39 checks PASS**
- V5b StateReader: **17/17 checks PASS**
- V5c BlueSkyConnector: **16/16 checks PASS**
- V6 Geodesy: **10/10 checks PASS**

**Total: 130/130 checks pass.**

Milestones 3–8 have their own self-contained regression suites (no
shell-timeout splitting needed — each runs in well under a second):

```bash
python tests/test_hotspot.py      # Milestone 3 — 24/24 checks PASS
python tests/test_complexity.py   # Milestone 4 — 42/42 checks PASS
python tests/test_tracking.py     # Milestone 5 — 44/44 checks PASS
python tests/test_forecast.py     # Milestone 6 — 47/47 checks PASS
python tests/test_resolution.py   # Milestone 7 — 39/39 checks PASS
python tests/test_dashboard.py    # Milestone 8 — 70/70 checks PASS
```

**Grand total across all eight milestones: 396/396 checks pass.**


## Architecture review (July 2026) — 4DARHAC domain model

A design review determined that the original "Phase 3 — hotspot detection"
conflated two operations of different natures: **spatial clustering**
(DBSCAN over one snapshot — stateless, pure) and **temporal linkage**
(deciding whether a cluster at one horizon/poll-cycle is the same physical
area as a cluster seen earlier — stateful, an association/tracking
problem). A 4DARHAC (4D Area of Relatively High ATC Complexity — ASTRA's
core detection target) is by definition a *persistent* spatiotemporal
object, not an independent snapshot re-derived from scratch every horizon
and every poll cycle. Running DBSCAN independently at each horizon produces
disconnected 3D clusters, not one 4D area.

The remaining milestones were reorganized accordingly. Full rationale,
domain model (`Cluster`, `ComplexityRegion`, `FourDArhac`), and revised
pipeline diagram live in
[`docs/architecture.md §6`](docs/architecture.md#6-4darhac-domain-model-and-revised-pipeline).
`Cluster` (Milestone 3), `ComplexityRegion` (Milestone 4),
`FourDArhac` (Milestone 5), `ForecastEngine` (Milestone 6), and
`ResolutionEngine` (Milestone 7) are now all implemented.

| # | Milestone | Nature | Depends on | Status |
|---|---|---|---|---|
| 3 | Cluster detection | pure / stateless | Trajectory prediction (Milestone 2) | ✅ Complete |
| 4 | Complexity assessment | pure / stateless | Cluster detection | ✅ Complete |
| 5 | 4DARHAC detection (tracking) | **stateful** | Cluster detection (+ complexity) | ✅ Complete |
| 6 | 4DARHAC forecast | stateless, layered on 5 | 4DARHAC detection | ✅ Complete |
| 7 | Resolution | stateless given a 4DARHAC | 4DARHAC forecast | ✅ Complete |
| 8 | Dashboard | presentation, read-only | everything above | ✅ Complete |

## Milestone 3 (Cluster detection) — as built

Implemented in `astra/hotspot/`:

```python
from astra.hotspot.engine import ClusterEngine
from astra.hotspot.models import Cluster
```

`ClusterEngine.detect()` / `.detect_all()` run DBSCAN independently over
each `PredictedSnapshot` returned by `TrajectoryEngine.predict()` (and
the current observed `TrafficSnapshot`), using `separation_horizontal_nm`
(15 NM) as the ε parameter and `separation_vertical_ft` (1 000 ft) as an
additional vertical gate via a precomputed distance matrix. Full design
rationale: `docs/milestone_3_hotspot.md`. Verification:
`tests/test_hotspot.py`, 24/24 checks pass.

Deliberately **out of scope** for Milestone 3: linking clusters across
horizons or poll cycles into a persistent identity. That was Milestone 5
(4DARHAC detection / tracking), scoped separately so the two problems —
one mechanical, one genuinely novel — got independent design attention
instead of being silently bundled together.

## Milestone 4 (Complexity assessment) — as built

Implemented in `astra/complexity/`:

```python
from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
```

`ComplexityEngine.assess()` / `.assess_many()` take each `Cluster` from
Milestone 3 and compute a 0–100 `complexity_score` from density,
CPA-based MTCA/LTCA conflict counts, circular heading diversity,
altitude diversity, and aircraft-type mixture, combined with
configurable weights (`ASTRAConfig.complexity_weight_*`, validated to
sum to 1.0). Full design rationale: `docs/milestone_4_complexity.md`.
Verification: `tests/test_complexity.py`, 42/42 checks pass.

## Milestone 5 (4DARHAC detection / tracking) — as built

Implemented in `astra/tracking/`:

```python
from astra.tracking.engine import TrackerEngine
from astra.tracking.models import FourDArhac
```

`TrackerEngine` is the pipeline's first stateful component: one instance
holds the current set of open `FourDArhac` tracks across `update()`
calls, one call per poll cycle. Each call associates this cycle's
observed (`horizon_min == 0`) `ComplexityRegion`s against open tracks —
primary signal Jaccard similarity of `member_callsigns`
(`tracking_jaccard_threshold`), fallback centroid/extent circle overlap
— extends matches, opens new `CANDIDATE` tracks for the rest, and
mechanically derives lifecycle status (`CANDIDATE → CONFIRMED → GROWING
→ PEAK → DISSIPATING → CLOSED`) from the `complexity_score` trend plus a
staleness check (`tracking_stale_cycles`). Full design rationale,
including why only horizon 0 drives identity in this milestone:
`docs/milestone_5_tracking.md`. Verification: `tests/test_tracking.py`,
44/44 checks pass. Demonstration: `tests/demo_tracking.py`.

`main.py` was deliberately **not** wired to the tracker (or to any of
Milestones 2–4's engines) at the time this milestone was built — see
"`main.py` — deliberately not integrated" in `docs/milestone_5_tracking.md`
for why that stayed a demo-script concern until the Dashboard milestone.
**Resolved in Milestone 8** below: `main.py` now runs the full pipeline
via `astra.pipeline.Pipeline` every poll cycle.

## Milestone 6 (4DARHAC forecast) — as built

Implemented in `astra/forecast/`:

```python
from astra.forecast.engine import ForecastEngine
from astra.tracking.models import FourDArhac  # forecast fields live here
```

`ForecastEngine` is stateless — it does not own tracks the way
`TrackerEngine` does. Called once per track, per poll cycle, after
`TrackerEngine.update()` has already run: for every track with `status`
in `{CONFIRMED, GROWING, PEAK, DISSIPATING}`, it builds a per-track
`(time_s, complexity_score)` series (`astra.forecast.horizon_series`,
reusing `astra.tracking.association.best_cluster_match` against this
cycle's predicted-horizon `ComplexityRegion`s) and linearly interpolates
onset/dissipation threshold crossings and any higher future peak
(`astra.forecast.projection`). Populates `predicted_onset_s`,
`predicted_dissipation_s`, `predicted_peak_time_s`,
`forecast_urgency_rank`, and a composite `confidence` directly on the
same `FourDArhac` objects `TrackerEngine` owns — no separate forecast
dataclass. `predicted_peak_time_s` and `forecast_urgency_rank` are new
fields added to `FourDArhac` beyond the Milestone 5 schema (purely
additive; `forecast_urgency_rank` is deliberately kept separate from
`priority`, which keeps Milestone 5's severity-only meaning unchanged).
Full design rationale, including the resolution of all five open design
questions from the original review and the one real defect found while
integrating `demo_forecast.py`: `docs/milestone_6_forecast.md`.
Verification: `tests/test_forecast.py`, 47/47 checks pass.
Demonstration: `tests/demo_forecast.py`.

## Milestone 7 (AI resolution) — as built

Implemented in `astra/resolution/`:

```python
from astra.resolution.engine import ResolutionEngine
from astra.resolution.models import ResolutionCandidate, ResolutionSet
```

`ResolutionEngine` is stateless — it does not own tracks or snapshots.
Called once per eligible track, per poll cycle, after
`ForecastEngine.forecast_many()` has already run: a track is eligible
only if its `status` is one of `{CONFIRMED, GROWING, PEAK,
DISSIPATING}` and it has both a `forecast_urgency_rank` and a
`predicted_onset_s`. For each eligible track, `generate_candidates()`
(`astra.resolution.candidates`) builds speed and flight-level candidates
always, plus a heading candidate when the matched region's complexity
has a nonzero MTCA/LTCA component — each candidate carries a
hypothetical `TrafficSnapshot` (built via `dataclasses.replace`, the
live snapshot is never mutated). `ResolutionEngine._evaluate()` re-runs
the existing `TrajectoryEngine.predict()` → `ClusterEngine.detect()` on
that hypothetical snapshot at the single configured horizon closest to
the track's `predicted_onset_s`, re-associates the result to the
track's cluster via `astra.tracking.association.best_cluster_match`,
and scores the outcome with a weighted `resolution_score` (complexity
reduction minus deviation and fuel-proxy cost). Results are returned as
a `ResolutionSet` (composes the `FourDArhac`, not a field on it) with
candidates ranked descending by score; `ResolutionSet.best()` returns
the top candidate. No new fields were added to `FourDArhac` — this
milestone only reads it. Full design rationale, including the
resolution of all five open design questions from the original review
and the smoke test performed before writing the formal test suite:
`docs/milestone_7_resolution.md`. Verification: `tests/test_resolution.py`,
39/39 checks pass. Demonstration: `tests/demo_resolution.py`.

`main.py` was deliberately **not** wired to the forecaster either at
the time this milestone was built, for the same reasons as Milestone 5
— see "`main.py` — deliberately not integrated" in
`docs/milestone_5_tracking.md`. **Resolved in Milestone 8** below.

## Milestone 8 (Dashboard / HMI) — as built

Implemented in `astra/dashboard/`:

```python
from astra.dashboard.server import create_app, run_dashboard_in_background
from astra.dashboard.store import CycleStore
```

The dashboard is a **read-only** presentation layer over
`astra.pipeline.Pipeline`'s `CycleResult` — it does not own state, does
not issue clearances back to `MockConnector`/BlueSky, and introduces no
new prediction/clustering/complexity/tracking/forecast/resolution math.
`main.py` runs `Pipeline.run_cycle()` every `poll_interval_s` (finally
ending the "deliberately not integrated" status noted under Milestones
5 and 7 above) and publishes each `CycleResult` into a `CycleStore` — a
small `threading.Lock`-protected object, the one new concurrency
primitive this milestone introduces, written by `main.py`'s poll loop
and read by the dashboard's Flask server, which runs in a background
daemon thread of the same process. `astra.dashboard.serializers` is the
only new "logic" module: pure functions turning `TrafficSnapshot`,
`PredictionResult`, `Cluster`/`ComplexityRegion`, `FourDArhac`, and
`ResolutionSet` objects into JSON; `astra.dashboard.routes` imports only
`CycleStore` and `serializers`, never an engine or `Pipeline` directly —
the clean boundary that lets a future live-BlueSky run or an RL-based
`ResolutionEngine` plug in without any dashboard code changing. The HMI
page (`templates/index.html` + `static/{css,js}/dashboard.js`) polls
`/state` at the server-supplied `poll_interval_s` and renders a
plan-view traffic/predicted-trajectory map, a complexity heatmap, a
4DARHAC hotspot table, an onset/peak/dissipation timeline, and a ranked
resolution-candidates panel (capped at
`dashboard_max_resolution_candidates_shown` per track).

One real gap was found and fixed while verifying the design review's
assumptions (not a redesign): `Pipeline.run_cycle()` computed a
`PredictionResult` internally but only exposed the `ComplexityRegion`s
derived from it — the map panel had no predicted aircraft positions to
draw. Fixed by hoisting the existing `TrajectoryEngine.predict()` call
up one level so `CycleResult` carries `prediction` alongside
`regions_by_horizon`; no new computation, no change to any Milestone
1–7 engine. Full design rationale, including the resolution of all
five open design questions from the original review:
`docs/milestone_8_dashboard.md`. Verification: `tests/test_dashboard.py`,
70/70 checks pass. Demonstration: `python main.py --mock`, then open
`http://127.0.0.1:8050/` — no separate `demo_dashboard.py` was needed
since the now-wired `main.py` already is the live demonstration.

---

## Coding conventions

| Convention | Rule |
|---|---|
| Type hints | Required on every public function and method |
| Docstrings | Required on every public class and function |
| Immutability | Use `frozen=True` on dataclasses that represent facts |
| Unit naming | Always suffix field names with their unit: `altitude_ft`, `speed_kt` |
| Logging | Use `get_logger(__name__)` — never `print()` in library code |
| BlueSky imports | **Only** in `astra/interface/bluesky_connector.py` |
| State mutation | Never mutate a `TrafficSnapshot` or `AircraftState` in place |
| Thread safety | Acquire `self._lock` for any shared-state access in `MockConnector` |

---

## Known limitations (Milestones 1–8)

| Limitation | Impact | Mitigation path |
|---|---|---|
| `trk` used as heading (no wind correction) | Heading accuracy degrades with strong crosswinds; carried into Milestone 2 predictions | Future work — wind-corrected model noted as an extension |
| Aircraft type `"UNKNOWN"` for scenario-loaded aircraft | `type_mix_count` (Milestone 4) is approximate for such aircraft | A manual type table loaded from config would resolve this |
| No ADS-C / EPP data | Trajectory prediction (Milestone 2) uses constant-velocity dead-reckoning from flight plan only, not intent data | Out of scope for TRL-2 per reference FRD |
| Constant-velocity assumption (Milestone 2) | No acceleration/turn modelling; accuracy degrades for manoeuvring aircraft over longer horizons, propagating into Milestones 3–7 | Documented simplifying assumption; intent-based model is future work |
| `history_length=3600` in default config | ~1 hour at 1 Hz; no persistence across restarts | A file-backed replay store is future work |
| `sim_step_s` fixed per session | Cannot accelerate only specific segments | Acceptable for thesis prototype |
| Complexity score combination is linear-weighted, not PCA/quadratic-mean | Documented simplification vs. the reference ASTRA literature — see `docs/milestone_4_complexity.md` "Score combination" | Would need a historical reference dataset to calibrate a PCA-based model |
| `TrackerEngine` uses greedy one-to-one association, not globally-optimal assignment | A rare multi-candidate cycle could pick a locally-good but not globally-best match | Acceptable at DBSCAN cluster counts in the tens; see `docs/milestone_5_tracking.md` |
| `FourDArhac.confidence` was a detection-count placeholder through Milestone 5 | Not meaningful as a forecast confidence on its own | Resolved in Milestone 6 — `ForecastEngine` now multiplies it by horizon coverage and a horizon-distance decay term; see `docs/milestone_6_forecast.md` |
| Only horizon 0 (observed) drives `TrackerEngine` track identity/lifecycle -- **confirmed still true as of the July 2026 HMI-parity pass, not resolved by Milestone 6** | A cluster/conflict that only exists in a *predicted* horizon (e.g. two aircraft currently >15 NM apart but on a converging track that will breach separation in 5-40 min) is computed by `Pipeline._build_regions_by_horizon()` every cycle but silently discarded -- no `FourDArhac` ever opens for it, so it never reaches the dashboard as an alert, even though the underlying detection already ran. Reproduced in `docs/plan.md` "Known limitation: predicted-only hotspots are dropped" with a runnable repro. `ForecastEngine` (Milestone 6) only forecasts tracks that *already* opened from a horizon-0 cluster; it does not open new ones from predicted horizons -- the "Resolved in Milestone 6" note on the neighbouring `confidence` row applies to *forecasting an existing track*, not to *detecting one from prediction alone* | See `docs/plan.md` for a scoped fix design (let `TrackerEngine` open/extend candidate tracks from the nearest non-empty predicted horizon when horizon 0 has no match, with per-horizon dedup against already-open tracks) -- deferred as a follow-up because it touches `TrackerEngine`/`ForecastEngine` identity semantics that `tests/test_tracking.py` and `tests/test_forecast.py` (~1,250 lines combined) currently assert against |
| `ForecastEngine.confidence` is a documented heuristic (`detection_ramp * horizon_coverage * (1 - decay)`), not a statistically calibrated probability | Same "no historical reference dataset" constraint already documented for complexity scoring | Would need a historical reference dataset to calibrate against; see `docs/milestone_6_forecast.md` "Confidence formula" |
| Onset/dissipation/peak estimates reuse the constant-velocity predicted horizons from Milestone 2 | Accuracy degrades for manoeuvring aircraft over longer horizons, same as Milestones 3–5 | Already mitigated by the confidence decay term; an intent-based trajectory model remains future work |
| No direct-to candidate in `ResolutionEngine` | Only speed/FL/heading levers are ranked | `MockConnector` has no direct-to-equivalent stack command to demonstrate it offline; see `docs/milestone_7_resolution.md` OQ-2 |
| `deviation_cost_norm` / `fuel_cost_proxy_norm` are documented proxies, not real cost models | Same "no ADS-C/EPP flight-plan-leg data" constraint already documented for trajectory prediction | Would need real flight-plan leg geometry and a fuel-burn model; see `docs/milestone_7_resolution.md` OQ-4 |
| `select_target_aircraft()` picks the highest-conflict-pair member (or alphabetical fallback), not a true per-aircraft complexity contribution | `ComplexityRegion` has no per-aircraft score breakdown to select against | Would need `ComplexityEngine` to expose per-aircraft contribution scores; see `docs/milestone_7_resolution.md` OQ-2 |
| `ResolutionEngine` is advisory only — no clearance issuance to BlueSky/`MockConnector` | Candidates are computed, ranked, and now displayed (Milestone 8) but never acted on | Deliberate non-goal, carried through Milestone 8; see `docs/milestone_8_dashboard.md` non-goals |
| Dashboard binds to `127.0.0.1` only, no authentication | Single-user, single-machine use only — not a multi-FMP or networked deployment | Deliberate non-goal for a TRL-2 prototype; see `docs/milestone_8_dashboard.md` non-goals |
| Dashboard updates by polling `/state`, not push (websocket) | Up to one `poll_interval_s` of staleness between a cycle completing and the browser showing it | Acceptable at the default 1 Hz cadence; see `docs/milestone_8_dashboard.md` OQ-5 |
| Dashboard heatmap is live-only (current cycle's horizon-0 regions), no accumulated history | No rolling/historical hotspot view | Deliberate scope cut (OQ-4's stretch goal); see `docs/milestone_8_dashboard.md` |
| Flask's built-in development server (`app.run()`), not a production WSGI server | Not hardened for concurrent multi-user load | Acceptable for a single-FMP thesis prototype; a production deployment would use gunicorn/uWSGI behind a real web server |

---

## Troubleshooting

**`"Waiting for a BlueSky simulation node…"` hangs**
BlueSky must be started in a separate terminal with `python -m bluesky --headless`
before ASTRA connects. Check that ports 11000/11001 are not in use.

**Aircraft type shows `UNKNOWN`**
The aircraft was created by a `.scn` file rather than `reader.create_aircraft()`.
This is expected — see the TypeRegistry limitation above.

**`MockConnector` positions don't move**
`OP` must be sent after creating aircraft: `reader.send_command("OP")` or
call `reader._connector.set_running(True)` directly in tests.

**BlueSky `--headless` shows RTree warning**
`Warning: RTree could not be loaded. areafilter … won't work.`
This is a BlueSky dependency warning and does not affect ASTRA. It can be
suppressed by installing `rtree`: `pip install rtree`.
