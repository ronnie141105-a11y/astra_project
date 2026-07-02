# Developer Handover — ASTRA Prototype

## Project summary

ASTRA is a simplified Python re-implementation of the SESAR ASTRA concept
(AI-enabled Tactical FMP Hotspot Prediction and Resolution), built as a
university undergraduate thesis project.

**Core constraint:** BlueSky is **only** the traffic simulator — an external
process. All prediction, detection, complexity, AI and visualisation logic
lives in this repository. The two processes communicate over ZeroMQ using
BlueSky's built-in network API.

**Current status:** Phase 1 (data interface) and Phase 2 (trajectory
prediction) are **complete and verified**. Phases 3–7 are scaffolded as
documented placeholder packages.

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
│   ├── trajectory/            Phase 2 ✅ — kinematic trajectory prediction
│   │   ├── models.py              PredictedSnapshot, PredictionResult
│   │   └── engine.py              TrajectoryEngine (constant-velocity)
│   │
│   ├── hotspot/               Phase 3 TODO — cluster detection (proposed rename; see §"Architecture review")
│   ├── complexity/            Phase 4 TODO — per-cluster complexity assessment
│   ├── prediction/            Phase 5–6 TODO — 4DARHAC detection (tracking) + forecast
│   ├── resolution/            Phase 7 TODO — clearance generation + ranking
│   └── dashboard/             Phase 8 TODO — live visualisation
│
├── docs/
│   └── architecture.md        Mermaid diagrams (full system + dep graph)
│
├── scenarios/
│   └── phase1_demo.scn        BlueSky scenario file (4 aircraft, live mode)
│
├── demo_phase1.py             Offline demo: 5 aircraft, full snapshot print
├── demo_trajectory.py         Offline demo: trajectory prediction tables
├── main.py                    Entry point (--mock flag or live BlueSky)
├── requirements.txt           pip install -r requirements.txt
├── README.md                  User-facing setup and usage guide
├── PHASE1_CHECKLIST.md        Phase 1 requirement traceability + verification
├── PROJECT_STATUS.md          Overall milestone status (Phase 1 & 2)
└── Developer_Handover.md      This file
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

`requirements.txt` currently lists only `bluesky-simulator`. Later phases
will add `numpy`, `scikit-learn` (Phase 3 cluster detection), and a
dashboard framework (Phase 8).

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

### Option C — Phase 1 demonstration script

```bash
python demo_phase1.py
```

Creates 5 aircraft across Swiss/German upper airspace, polls five times
(each tick = 60 simulated seconds), and prints a formatted TrafficSnapshot
with inter-aircraft separations. No BlueSky required. Expected to complete
in under one second.

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
python demo_phase1.py   # implicit V5a+V5b integration test
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
`as_list()`, `callsigns()`, `__len__`, `__iter__`) by design, so Phase 3
(DBSCAN clustering) can consume predicted and observed snapshots through
identical code paths.

Run `python demo_trajectory.py` for a worked example.

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
[`docs/architecture.md §6`](docs/architecture.md#6-4darhac-domain-model-and-revised-pipeline-proposed--pending-approval).
This section is **design only** — nothing below has been implemented yet.

| # | Milestone | Nature | Depends on |
|---|---|---|---|
| 3 | Cluster detection | pure / stateless | Trajectory prediction (Phase 2) |
| 4 | Complexity assessment | pure / stateless | Cluster detection |
| 5 | 4DARHAC detection (tracking) | **stateful** | Cluster detection (+ complexity) |
| 6 | 4DARHAC forecast | stateful, layered on 5 | 4DARHAC detection |
| 7 | Resolution | stateless given a 4DARHAC | 4DARHAC forecast |
| 8 | Dashboard | presentation | everything above |

## What Milestone 3 (Cluster detection) needs from Phase 2

Milestone 3 will import:

```python
from astra.trajectory.engine import TrajectoryEngine
from astra.trajectory.models import PredictedSnapshot, PredictionResult
from astra.utils.geodesy import haversine_distance_nm
```

It will run DBSCAN independently over each `PredictedSnapshot` returned by
`TrajectoryEngine.predict()` (as well as the current observed
`TrafficSnapshot`), using `separation_horizontal_nm` (15 NM) as the ε
parameter and `separation_vertical_ft` (1 000 ft) as an additional vertical
gate. Because `PredictedSnapshot` exposes the same iteration/accessor API as
`TrafficSnapshot`, the clustering code should not need to special-case
predicted vs. observed input.

Deliberately **out of scope** for Milestone 3: linking clusters across
horizons or poll cycles into a persistent identity. That is Milestone 5
(4DARHAC detection / tracking), scoped separately so the two problems —
one mechanical, one genuinely novel — get independent design attention
instead of being silently bundled together.

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

## Known limitations (Phase 1 & 2)

| Limitation | Impact | Mitigation path |
|---|---|---|
| `trk` used as heading (no wind correction) | Heading accuracy degrades with strong crosswinds; carried into Phase 2 predictions | Future work — wind-corrected model noted as an extension |
| Aircraft type `"UNKNOWN"` for scenario-loaded aircraft | Complexity metrics relying on type mix will be approximate | Phase 3/4 can use a manual type table loaded from config |
| No ADS-C / EPP data | Trajectory prediction (Phase 2) uses constant-velocity dead-reckoning from flight plan only, not intent data | Out of scope for TRL-2 per reference FRD |
| Constant-velocity assumption (Phase 2) | No acceleration/turn modelling; accuracy degrades for manoeuvring aircraft over longer horizons | Documented simplifying assumption; intent-based model is future work |
| `history_length=3600` in default config | ~1 hour at 1 Hz; no persistence across restarts | Phase 7 can add a file-backed replay store |
| `sim_step_s` fixed per session | Cannot accelerate only specific segments | Acceptable for thesis prototype |

---

## Troubleshooting

**`ImportError: bluesky-simulator`**
Install it: `pip install bluesky-simulator`

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
