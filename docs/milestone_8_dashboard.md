# Milestone 8 — Dashboard / HMI (`astra.dashboard`)

## Scope

Milestone 8 is the pipeline's presentation layer: a live view onto
everything Milestones 1–7 already compute. It shows the current
traffic picture (`StateReader`'s live positions), predicted
trajectories (`TrajectoryEngine`'s `PredictionResult`), 4DARHAC state
(a heatmap/table/timeline over open `FourDArhac` tracks with their
forecast fields), and `ResolutionEngine`'s ranked candidate clearances
for the most urgent open tracks. It changes nothing about Milestones
1–7's public APIs: the dashboard is a **read-only consumer** of
`TrafficSnapshot`, `PredictionResult`, `Cluster`/`ComplexityRegion`
lists, `FourDArhac` tracks, and `ResolutionSet`s — it does not own
state, does not issue clearances back to `MockConnector`/BlueSky, and
does not recompute anything the pipeline already computes.

This document supersedes the original Milestone 8 engineering design
review (`docs/milestone_8_dashboard_design_review.md`, marked "DRAFT —
pending approval" when written) — its five recommended options (OQ-1
through OQ-5, referenced throughout below) were all adopted as
recommended, and it is now an as-built record.

## Pipeline fix found while implementing (not a redesign)

Verifying the design review's assumption that "predicted trajectories
(`TrajectoryEngine`'s `PredictionResult`)" would be available to serve
turned up a real gap: `astra.pipeline.Pipeline` (added just before this
milestone to give `main.py` and the dashboard one shared `run_cycle()`
entry point) computed a `PredictionResult` internally but discarded it
after deriving `regions_by_horizon` from it — `CycleResult` only
exposed the derived `ComplexityRegion`s, never the raw predicted
aircraft positions the map panel needs. Fixed with the smallest
possible change: `run_cycle()` now computes `prediction` once and
passes it both into `_build_regions_by_horizon()` (as a parameter,
instead of that method re-predicting it) and onto the returned
`CycleResult`. Same computations, same order, one new field. No other
part of `Pipeline`, and no Milestone 1–7 engine, changed.

## Design decision — Flask, polling a `/state` JSON endpoint (OQ-1)

A minimal local Flask app serves a single HMI page that polls a
`/state` JSON endpoint. Rationale carried over unchanged from the
review: the reference ASTRA HMI is a rich, multi-panel workspace (map +
table + timeline) that a terminal or single static plot cannot
represent, and a small web app is the standard, low-effort way to get
that without a heavyweight GUI toolkit dependency. This is the one
place in the project where a new third-party dependency (`Flask`, added
to `requirements.txt`) is justified — every other package (Milestones
1–7) has none beyond `numpy`/`scikit-learn`.

## Design decision — `main.py` is the live-loop owner (OQ-2)

`main.py` now runs `Pipeline.run_cycle()` every `poll_interval_s` (the
same cadence it already used for Milestone 1's `StateReader.poll()`
alone) and publishes each `CycleResult` into `astra.dashboard.store.
CycleStore`. The dashboard's Flask server runs in the same process, in
a background thread started by `astra.dashboard.server.
run_dashboard_in_background()`; Flask's request-handling thread(s) read
the latest cycle from the same `CycleStore`. No IPC, no second process.
`CycleStore` is the one new concurrency primitive this milestone
introduces — a `threading.Lock` around "the last `CycleResult`" — exactly
the scope the review anticipated ("no new concurrency primitive beyond
what a single-threaded Flask dev server plus one background poll loop
already needs"). `main.py`'s existing `Ctrl+C` behaviour is unchanged:
the dashboard thread is a daemon thread, never joined, and exits with
the process.

This is also where the "`main.py` — deliberately not integrated" note
carried by Milestones 5, 6, and 7 finally ends: `main.py` is now
ASTRA's real application entry point, run either live
(`python main.py`) or offline (`python main.py --mock`), with
`--no-dashboard` available to fall back to the console-only loop every
prior milestone used.

## Design decision — show the full ranked candidate list, capped for display (OQ-3)

The resolution panel shows each eligible track's full ranked candidate
list (clearance type, target aircraft, delta, before/after complexity,
score) rather than only `ResolutionSet.best()`, matching why
`ResolutionSet` was designed to carry a ranked list in the first place
(`docs/milestone_7_resolution.md`, OQ-1). The list is capped at
`dashboard_max_resolution_candidates_shown` (default `3`) purely as a
*display* cap — `serializers.serialize_resolution_set()` slices the
already-ranked list; it never changes how many candidates
`ResolutionEngine` generated or ranked, and is independent of
`resolution_max_tracks_per_cycle` (which caps how many *tracks* get
resolved per cycle, a `ResolutionEngine`-side concern untouched by this
milestone).

## Design decision — live-only heatmap, no new history buffer (OQ-4)

The heatmap panel is built purely from the current cycle's observed
(`horizon 0`) `ComplexityRegion`s — no accumulated history buffer was
added. `serializers.serialize_regions_by_horizon()` does serialize
every horizon (not just `0`), since that costs nothing extra given
`CycleResult` already has all of them, leaving room for a future
predicted-hotspot horizon selector without any backend change — but no
frontend control was built to use anything but horizon `0` this
milestone. The rolling-heatmap stretch goal the review allowed for was
not pursued.

## Design decision — frontend polls at `poll_interval_s` (OQ-5)

`static/js/dashboard.js` polls `/state` on a `setTimeout` chain at
`window.ASTRA_POLL_INTERVAL_S * 1000` ms — a value the backend injects
into `templates/index.html` via Jinja2 (`config.poll_interval_s`), not
a value the frontend hard-codes or guesses. No websocket/push channel
was built.

## Module layout (as built)

```
astra/dashboard/
    models.py        DashboardSnapshot -- the dashboard's own tiny read-model
    store.py          CycleStore -- thread-safe latest-CycleResult bridge
    serializers.py    Pure functions: pipeline domain objects -> JSON
    routes.py         Flask Blueprint: "/" (HMI shell) + "/state" (JSON)
    server.py         Flask app factory + run_dashboard_in_background()
    templates/
        index.html    HMI page shell (map / tracks / timeline / resolutions)
    static/
        css/dashboard.css
        js/dashboard.js   Polls /state, renders all four panels
```

This is slightly more modular than the design review's proposed
`server.py` + `serializers.py` + `static/` layout (which the review
flagged as "subject to change at implementation time"), splitting HTTP
routing (`routes.py`) from app assembly (`server.py`) and giving the
dashboard its own tiny read-model (`models.py`) separate from the
mutable store that produces it (`store.py`) — kept separate so
`tests/test_dashboard.py` can construct an isolated `CycleStore` per
test rather than sharing global Flask state. `serializers.py` remains,
as the review anticipated, the only new "logic" module: every function
in it is a pure, side-effect-free transform from an existing Milestone
1–7 domain object to a JSON-safe `dict`. No new prediction, clustering,
complexity, tracking, forecasting, or resolution math was introduced.

## Clean API boundary for BlueSky live mode / RL

`astra.dashboard.routes` imports only `astra.dashboard.store.
CycleStore` and `astra.dashboard.serializers` — never an engine, never
`Pipeline`, never `StateReader`. Two consequences, both already true
without further work:

- **BlueSky live mode.** `main.py` already supports both
  `StateReader.for_bluesky()` (live) and `StateReader.for_mock()`
  (offline) — Milestone 1's existing boundary. Whichever `StateReader`
  is used, `main.py` calls the same `pipeline.run_cycle(snapshot)` and
  publishes the same `CycleResult` shape into `CycleStore`. The
  dashboard cannot tell which connector produced the traffic it is
  displaying, and does not need to.
- **A future RL-based `ResolutionEngine`.** The dashboard reads
  `CycleResult.resolution_sets` — a list of `ResolutionSet` objects —
  through `serializers.serialize_resolution_set()`, regardless of
  whether `Pipeline` was constructed with today's heuristic
  `ResolutionEngine` or a future RL-based replacement/supplement, as
  long as it produces the same `ResolutionSet`/`ResolutionCandidate`
  contract Milestone 7 defined. No dashboard code would need to change.

## Config additions (`ASTRAConfig`, Phase 8 section)

| Field | Default | Meaning |
|---|---|---|
| `dashboard_host` | `"127.0.0.1"` | Bind address for the dashboard's local Flask server. Validated as part of `ASTRAConfig`'s existing pattern (kept to localhost by default — see non-goals). |
| `dashboard_port` | `8050` | Bind port. Validated `0 < port <= 65535`. |
| `dashboard_max_resolution_candidates_shown` | `3` | Cap on candidates displayed per track (OQ-3), independent of `resolution_max_tracks_per_cycle`. Validated `>= 1`. |

## Verification

`tests/test_dashboard.py` (70 checks): pure `serializers.py` unit tests
against hand-built `AircraftState`/`TrafficSnapshot`/`Cluster`/
`ComplexityRegion`/`FourDArhac`/`ResolutionCandidate`/`ResolutionSet`/
`PredictionResult` objects (aircraft/snapshot serialization,
prediction reshaping from per-horizon to per-callsign grouping
including an aircraft missing from a horizon, region/cluster
serialization, track history/centroid including the empty-track edge
case, resolution-candidate-list capping, and the full `CycleResult` /
`DashboardSnapshot` payload shapes); `CycleStore` starting empty and
incrementing `cycle_count` on each `update()`; a `Pipeline.run_cycle()`
integration check confirming `CycleResult.prediction` is present and
consistent with `regions_by_horizon`'s keys; and Flask `test_client()`
integration checks for `/state` before and after real pipeline cycles
(reusing `demo_resolution.py`'s converging 3-aircraft geometry to
guarantee a track, a forecast urgency rank, and ranked resolution
candidates all appear in one JSON payload) and for `/` serving the HMI
shell with the configured poll interval injected. Combined with
Milestones 3–7 (24/24, 42/42, 44/44, 47/47, 39/39), the full suite is
266/266.

Live demonstration: `python main.py --mock`, then open
`http://127.0.0.1:8050/` while it runs. No separate `demo_dashboard.py`
was added — per the design review's "`demo_dashboard.py` and/or the
newly-wired `main.py`," the now-wired `main.py` already *is* the live,
browser-visible demonstration this milestone needed, and a second
script driving the same scenario through the same `Pipeline` would only
duplicate it.

## Explicit non-goals (carried over from the design review)

No clearance issuance back to `MockConnector`/BlueSky from the
dashboard (read-only, advisory display only — same boundary Milestone 7
already drew for `ResolutionEngine` itself). No user authentication or
multi-user support — `dashboard_host` defaults to `127.0.0.1`
(localhost-only). No mobile/responsive layout requirement. No
real-time collaborative coordination features. No historical
replay/scrubbing beyond today's live-only heatmap (OQ-4's stretch goal
was not pursued). No websocket/push channel (OQ-5's stretch option was
not pursued). No change to any Milestone 1–7 package's public API.
