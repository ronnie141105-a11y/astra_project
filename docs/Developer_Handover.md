# Developer Handover — ASTRA Prototype

## Project summary

ASTRA is a simplified Python re-implementation of the SESAR ASTRA concept
(AI-enabled Tactical FMP Hotspot Prediction and Resolution), built as a
university undergraduate thesis project.

**Core constraint:** BlueSky is **only** the traffic simulator — an external
process. All prediction, detection, complexity, AI and visualisation logic
lives in this repository. The two processes communicate over ZeroMQ using
BlueSky's built-in network API.

**Current status:** Milestones 1–7 — data interface, trajectory
prediction, cluster detection, complexity assessment, 4DARHAC tracking,
4DARHAC forecast, and AI resolution — are **complete and verified**
(130/130 checks from Milestones 1–2, plus 24/24, 42/42, 44/44, 47/47,
and 39/39 from Milestones 3–7 — 326/326 total). Milestone 8 (live
dashboard) has an engineering design review pending approval; see
`docs/milestone_8_dashboard_design_review.md`.

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
│   └── dashboard/                Milestone 8 ⬜ — live visualisation; design review pending
│
├── docs/
│   ├── architecture.md            Mermaid diagrams (full system + dep graph + domain model)
│   ├── milestone_3_hotspot.md     Milestone 3 design rationale
│   ├── milestone_4_complexity.md  Milestone 4 design rationale
│   ├── milestone_5_tracking.md    Milestone 5 design rationale
│   ├── milestone_6_forecast.md    Milestone 6 design rationale
│   ├── milestone_6_forecast_design_review.md  Milestone 6 original design review (approved)
│   ├── milestone_7_resolution.md  Milestone 7 design rationale (as built)
│   ├── milestone_8_dashboard_design_review.md  Milestone 8 design review (pending approval)
│   ├── PROJECT_STATUS.md          Overall milestone status
│   └── Developer_Handover.md      This file
│
├── scenarios/
│   └── phase1_demo.scn        BlueSky scenario file (4 aircraft, live mode)
│
├── tests/
│   ├── demo_phase1.py             Offline demo: 5 aircraft, full snapshot print
│   ├── demo_trajectory.py         Offline demo: trajectory prediction tables
│   ├── demo_hotspot.py            Offline demo: cluster detection
│   ├── demo_complexity.py         Offline demo: complexity assessment
│   ├── demo_tracking.py           Offline demo: 4DARHAC tracking lifecycle
│   ├── demo_forecast.py           Offline demo: 4DARHAC forecast (onset/peak/dissipation)
│   ├── demo_resolution.py         Offline demo: AI resolution candidate ranking
│   ├── test_hotspot.py            Milestone 3 regression suite (24 checks)
│   ├── test_complexity.py         Milestone 4 regression suite (42 checks)
│   ├── test_tracking.py           Milestone 5 regression suite (44 checks)
│   ├── test_forecast.py           Milestone 6 regression suite (47 checks)
│   └── test_resolution.py         Milestone 7 regression suite (39 checks)
│
├── main.py                    Entry point (--mock flag or live BlueSky) — Phase 1 only
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
new dependency). Milestone 8 (dashboard) will add a dashboard framework.

---

## Running the project

### Option A — Offline mock mode (no BlueSky needed)

```bash
python main.py --mock
```

The `MockConnector` generates synthetic traffic. Aircraft move via
great-circle dead-reckoning. Useful for developing Phases 2–7 on a laptop
with no BlueSky installation.

### Option B — Live mode (requires BlueSky)

```bash
# Terminal 1 — start BlueSky headless
python -m bluesky --headless

# Terminal 2 — start ASTRA
python main.py
```

BlueSky will print its port numbers (default 11000 / 11001). ASTRA
connects automatically using the defaults in `ASTRAConfig`.

To load the bundled demo scenario into the running simulation, from the
BlueSky console or a third terminal:

```
IC scenarios/phase1_demo.scn
```

### Option C — offline demonstration scripts

```bash
python tests/demo_phase1.py       # Milestone 1 — state interface
python tests/demo_trajectory.py   # Milestone 2 — trajectory prediction
python tests/demo_hotspot.py      # Milestone 3 — cluster detection
python tests/demo_complexity.py   # Milestone 4 — complexity assessment
python tests/demo_tracking.py     # Milestone 5 — 4DARHAC tracking
python tests/demo_forecast.py     # Milestone 6 — 4DARHAC forecast
python tests/demo_resolution.py   # Milestone 7 — AI resolution framework
```

`demo_phase1.py` creates 5 aircraft across Swiss/German upper airspace,
polls five times (each tick = 60 simulated seconds), and prints a
formatted `TrafficSnapshot` with inter-aircraft separations. The
Milestone 3/4 demos each run a high- and low-complexity scenario across
the observed snapshot and every predicted horizon. `demo_tracking.py`
drives several manual `poll()` cycles with scripted stack commands to
show a `FourDArhac` moving through its full lifecycle (`CANDIDATE →
CONFIRMED → GROWING → PEAK → DISSIPATING → CLOSED`). `demo_forecast.py`
extends that same scripted scenario, running `ForecastEngine` alongside
`TrackerEngine` each cycle to print predicted onset/peak/dissipation
times, confidence, and urgency rank. `demo_resolution.py` uses a
converging 3-aircraft geometry (observed complexity below the forecast
onset threshold, 5-minute predicted horizon above it) so
`ResolutionEngine` has an eligible track from the first confirmed cycle,
printing ranked candidate clearances (speed / FL / heading) each cycle.
None require BlueSky; each completes in under one second.

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

Milestones 3–7 have their own self-contained regression suites (no
shell-timeout splitting needed — each runs in well under a second):

```bash
python tests/test_hotspot.py      # Milestone 3 — 24/24 checks PASS
python tests/test_complexity.py   # Milestone 4 — 42/42 checks PASS
python tests/test_tracking.py     # Milestone 5 — 44/44 checks PASS
python tests/test_forecast.py     # Milestone 6 — 47/47 checks PASS
python tests/test_resolution.py   # Milestone 7 — 39/39 checks PASS
```

**Grand total across all seven milestones: 326/326 checks pass.**

---

## Key design decisions

### 1. Anti-corruption layer (`astra/interface`)

`BlueSkyConnector` is the ONLY file allowed to import from `bluesky.*`.
Everything outside `interface/` receives `TrafficSnapshot` / `AircraftState`
objects — plain frozen dataclasses with zero BlueSky dependency. This is
enforced by V3 and means swapping BlueSky for another simulator requires
changing exactly one file.

### 2. `typing.Protocol` for connector interface

`BlueSkyConnector` already inherits from BlueSky's `Client`. Adding another
explicit base class (ABC) would risk MRO collisions with BlueSky's own
metaclass machinery. `ConnectorProtocol` uses structural subtyping instead:
both connectors satisfy it automatically through duck typing.

### 3. Dependency injection in `StateReader`

`StateReader.__init__` accepts any `ConnectorProtocol`. Factory classmethods
(`for_bluesky()`, `for_mock()`) provide convenient one-liners for the two
common cases. This makes every downstream phase (trajectory, hotspot, ...)
testable without a running BlueSky process.

### 4. `AircraftState` is frozen

An aircraft state is a historical fact. Making it immutable prevents any
later phase from accidentally modifying a snapshot that is also stored in
the history buffer. Predicted future states (Phase 2) are new `AircraftState`
objects rather than mutations of current ones.

### 5. Aircraft type workaround

BlueSky's `ACDATA` publish function (`bluesky.simulation.screenio`) does not
include the aircraft type string. This was verified by reading the installed
package source. `TypeRegistry` caches `callsign → type` at `create_aircraft()`
time. Aircraft created by hand-written scenario files (not through ASTRA)
receive `aircraft_type = "UNKNOWN"`.

### 6. `geodesy.py` in Phase 1

`MockConnector.poll()` propagates positions via `move_position()` — so the
geodesy module is needed in Phase 1, not Phase 3. Placing it in `utils/`
also means Phase 3's DBSCAN implementation can import it without creating a
dependency from `hotspot` on `trajectory`.

---

## Phase 2 — trajectory prediction (complete)

Phase 2 is implemented in `astra/trajectory/`:

```python
from astra.trajectory.engine import TrajectoryEngine
from astra.trajectory.models import PredictedSnapshot, PredictionResult
```

`TrajectoryEngine(config)` takes a `TrafficSnapshot` (from
`StateReader.current()` / `.poll()`) and returns a `PredictionResult`
containing one `PredictedSnapshot` per horizon in
`ASTRAConfig.prediction_horizons_min` (default: 5, 10, 15, 30, 60 minutes).

The model is deterministic constant-velocity dead-reckoning: horizontal
displacement reuses `astra.utils.geodesy.move_position()` — the same
function `MockConnector.poll()` uses — and vertical displacement is linear
extrapolation from `vertical_speed_fpm`. This means a prediction at horizon
H minutes is mathematically reproducible against H×60/`sim_step_s`
`MockConnector.poll()` calls, which is how the engine was numerically
verified.

`PredictedSnapshot` mirrors the `TrafficSnapshot` accessor API (`get()`,
`as_list()`, `callsigns()`, `__len__`, `__iter__`) by design, so Milestone 3
(DBSCAN clustering) consumes predicted and observed snapshots through
identical code paths — confirmed by `test_hotspot.py`'s API-parity check.

Run `python tests/demo_trajectory.py` for a worked example.

---

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
| 8 | Dashboard | presentation | everything above | ⬜ Design review pending |

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
Milestones 2–4's engines) — see "`main.py` — deliberately not
integrated" in `docs/milestone_5_tracking.md` for why that stays a
demo-script concern until the Dashboard milestone.

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

`main.py` was deliberately **not** wired to the forecaster either, for
the same reasons as Milestone 5 — see "`main.py` — deliberately not
integrated" in `docs/milestone_5_tracking.md`.

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

## Known limitations (Milestones 1–7)

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
| Only horizon 0 drove track identity/lifecycle through Milestone 5 | Predicted-horizon regions computed each cycle went unused beyond `demo_hotspot.py`/`demo_complexity.py` | Resolved in Milestone 6 — `ForecastEngine` is the first consumer of non-zero-horizon regions, for onset/peak/dissipation estimation only (identity/lifecycle in `TrackerEngine` is unchanged) |
| `ForecastEngine.confidence` is a documented heuristic (`detection_ramp * horizon_coverage * (1 - decay)`), not a statistically calibrated probability | Same "no historical reference dataset" constraint already documented for complexity scoring | Would need a historical reference dataset to calibrate against; see `docs/milestone_6_forecast.md` "Confidence formula" |
| Onset/dissipation/peak estimates reuse the constant-velocity predicted horizons from Milestone 2 | Accuracy degrades for manoeuvring aircraft over longer horizons, same as Milestones 3–5 | Already mitigated by the confidence decay term; an intent-based trajectory model remains future work |
| No direct-to candidate in `ResolutionEngine` | Only speed/FL/heading levers are ranked | `MockConnector` has no direct-to-equivalent stack command to demonstrate it offline; see `docs/milestone_7_resolution.md` OQ-2 |
| `deviation_cost_norm` / `fuel_cost_proxy_norm` are documented proxies, not real cost models | Same "no ADS-C/EPP flight-plan-leg data" constraint already documented for trajectory prediction | Would need real flight-plan leg geometry and a fuel-burn model; see `docs/milestone_7_resolution.md` OQ-4 |
| `select_target_aircraft()` picks the highest-conflict-pair member (or alphabetical fallback), not a true per-aircraft complexity contribution | `ComplexityRegion` has no per-aircraft score breakdown to select against | Would need `ComplexityEngine` to expose per-aircraft contribution scores; see `docs/milestone_7_resolution.md` OQ-2 |
| `ResolutionEngine` is advisory only — no clearance issuance to BlueSky/`MockConnector` | Candidates are computed and ranked but not acted on | Deferred to Milestone 8 (dashboard) or later; see `docs/milestone_7_resolution.md` non-goals |

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
