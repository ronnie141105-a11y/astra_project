# Milestone 8 — Live Dashboard (`astra.dashboard`) — Engineering Design Review

**Status: DRAFT — pending approval. No implementation exists yet.**

## Scope

Milestone 8 is the pipeline's presentation layer: a live view onto
everything Milestones 1–7 already compute. Per the reference FRD's HMI
requirements and `docs/architecture.md`'s `DASH` node, it must show:

- the current traffic picture (live positions from `StateReader`);
- predicted trajectories (`TrajectoryEngine`'s `PredictionResult`);
- 4DARHAC state — a heatmap/table/timeline over open `FourDArhac`
  tracks (`TrackerEngine`) with their forecast fields
  (`ForecastEngine`: onset, peak, dissipation, confidence, urgency
  rank);
- `ResolutionEngine`'s ranked candidate clearances for the most urgent
  open tracks.

This review does not propose any change to Milestones 1–7's public
APIs. The dashboard is a **read-only consumer** of `TrafficSnapshot`,
`PredictionResult`, `Cluster`/`ComplexityRegion` lists,
`FourDArhac` tracks, and `ResolutionSet`s — it does not own state, does
not issue clearances back to `MockConnector`/BlueSky, and does not
recompute anything the pipeline already computes.

## Constraint inherited from Milestones 1–7

Every milestone so far is demonstrated through a standalone
`demo_*.py` script driving `MockConnector` through scripted `poll()`
cycles — `main.py` has deliberately stayed a Phase-1-only reference
point (see `docs/milestone_5_tracking.md` / `milestone_6_forecast.md` /
`milestone_7_resolution.md`, "`main.py` — deliberately not
integrated"). Milestone 8 is the first milestone where this pattern
must end: a dashboard is inherently a live-loop consumer, not a
single-shot demonstration script. This review must therefore also
decide how `main.py` finally gets wired to the full Milestone 2–7
pipeline, which no prior milestone needed to resolve.

## Open Design Questions

### OQ-1 — Dashboard technology

**Options:**
- (A) A local web app (Flask/FastAPI + a JS charting library served
  over `localhost`), polling or websocket-pushed.
- (B) A `matplotlib`/`plotly` live-refresh window inside the same
  Python process as `main.py` (e.g. `plotly.graph_objects.FigureWidget`
  or a `matplotlib.animation.FuncAnimation` loop).
- (C) A terminal-based dashboard (`rich`/`textual`), consistent with
  the project's existing console-only demo style.

**Recommendation:** (A), a minimal local Flask/FastAPI app serving a
single page that polls a `/state` JSON endpoint on an interval matching
`poll_interval_s`. Rationale: the reference ASTRA HMI (per the FRD) is
explicitly a rich, multi-panel workspace (alerts table, traffic
projection map, vertical/horizontal profile windows) that a terminal or
single static plot cannot represent; a small web app is the standard,
low-effort way to get a real map + table + timeline in one place
without a heavyweight GUI toolkit dependency. This is the one place in
the project where a new third-party dependency (a web framework) is
justified, matching `Developer_Handover.md`'s existing note that
"Milestone 8 (dashboard) will add a dashboard framework."

### OQ-2 — Where does the live loop run?

**Options:**
- (A) `main.py` runs the full Milestone 2–7 pipeline every
  `poll_interval_s` (as it already does for Milestone 1's
  `StateReader.poll()` alone) and pushes the latest results into a
  small in-memory store the dashboard process reads.
- (B) The dashboard process itself owns the poll loop and imports the
  engines directly (no separate `main.py` process).
- (C) Two independent processes (`main.py` for the pipeline, a
  dashboard process) communicating over a lightweight IPC channel
  (file, socket, or queue).

**Recommendation:** (A). `main.py` becomes the single live-loop owner —
exactly the role it already has for Milestone 1 — and is finally
extended to run the full `TrajectoryEngine → ClusterEngine →
ComplexityEngine → TrackerEngine → ForecastEngine → ResolutionEngine`
sequence every cycle (the same sequence every `demo_*.py` script
already runs manually). The dashboard's web server runs in the same
process, reading the latest cycle's results from a small in-memory
object `main.py` updates each poll — no IPC, no second process, no new
concurrency primitive beyond what a single-threaded Flask dev server
plus one background poll loop already needs.

### OQ-3 — What resolution output does the dashboard show?

**Options:**
- (A) Only `ResolutionSet.best()` per track — the single recommended
  clearance.
- (B) The full ranked candidate list per track, letting the FMP
  compare options (matching FRD requirement
  `REQ-SOLASTRA-FRD-HM01.0006`, "evaluate and compare multiple
  dissipation solutions").
- (C) Nothing — defer resolution display to a later milestone.

**Recommendation:** (B). `ResolutionSet` was deliberately designed in
Milestone 7 to carry a ranked list, not just a single winner
(`docs/milestone_7_resolution.md`, OQ-1), specifically so a
consuming layer could show alternatives. Showing only `best()` would
waste that design. The dashboard table shows each candidate's
clearance type, target aircraft, delta, before/after complexity, and
score — directly serialisable from `ResolutionCandidate` with no new
computation.

### OQ-4 — Historical / heatmap view

**Options:**
- (A) A live heatmap built purely from the current cycle's
  `ComplexityRegion`s (no history needed).
- (B) A rolling heatmap accumulated over recent cycles, requiring a new
  bounded history buffer beyond `StateReader`'s existing
  `TrafficSnapshot` deque.
- (C) Full historical replay/scrubbing (out of scope for a TRL-2
  prototype).

**Recommendation:** (A) for the initial implementation, with (B) as an
explicit stretch goal only if time permits. `StateReader`'s
`history_length` deque already exists for `TrafficSnapshot`s
(Milestone 1); reusing the same bounded-deque pattern for a short
rolling window of `ComplexityRegion` lists is a small, low-risk
addition if pursued, but a live-only heatmap is sufficient to satisfy
the FRD's core HMI requirements (`REQ-SOLASTRA-FRD-HM01.0009`) without
adding new state-retention design.

### OQ-5 — Update cadence and staleness

**Options:**
- (A) Dashboard polls its backend on a fixed interval independent of
  `poll_interval_s` (e.g. always every 2s, regardless of simulation
  speed).
- (B) Dashboard polls at exactly `poll_interval_s`, staying
  synchronised with the pipeline's own cadence.
- (C) Push-based (websocket), backend notifies the dashboard the
  instant a new cycle completes.

**Recommendation:** (B) for the initial implementation — the frontend
polls `/state` at `poll_interval_s` (already a configured, known
value), avoiding both wasted requests (A) and the added complexity of
a websocket/push channel (C) for a thesis-scope prototype. Revisit (C)
only if polling-induced UI lag becomes a real, observed problem.

## Proposed module layout (subject to change at implementation time)

```
astra/dashboard/
    server.py       Flask/FastAPI app: /state JSON endpoint + static page
    serializers.py  Pure functions: TrafficSnapshot / PredictionResult /
                     FourDArhac / ResolutionSet -> JSON-safe dicts
    static/          HTML/JS/CSS for the map, table, and timeline panels
```

`serializers.py` is the only new "logic" module — everything else is
either a thin Flask/FastAPI wrapper or static frontend assets. No new
prediction, clustering, complexity, tracking, forecasting, or
resolution math is introduced by this milestone.

## Proposed config additions (`ASTRAConfig`, Phase 8 section)

| Field | Proposed default | Meaning |
|---|---|---|
| `dashboard_host` | `"127.0.0.1"` | Bind address for the dashboard's local web server. |
| `dashboard_port` | `8050` | Bind port. |
| `dashboard_max_resolution_candidates_shown` | `3` | Cap on candidates displayed per track (OQ-3), independent of `resolution_max_tracks_per_cycle`. |

Exact fields, defaults, and validation rules to be finalised once
implementation begins and the frontend's actual needs are known.

## Proposed verification plan

- `tests/test_dashboard.py` — following the Milestone 3–7 pattern
  (no third-party test framework): unit tests on `serializers.py`'s
  pure functions against hand-built `TrafficSnapshot`/`FourDArhac`/
  `ResolutionSet` objects, and an integration check that `server.py`'s
  `/state` endpoint returns valid JSON for a scripted scenario.
- `demo_dashboard.py` and/or the newly-wired `main.py` — a
  live, browser-visible demonstration is the natural "demo" for this
  milestone, replacing the console-print pattern every prior
  `demo_*.py` used.

## Explicit non-goals

No clearance issuance back to `MockConnector`/BlueSky from the
dashboard (read-only, advisory display only — same boundary Milestone 7
already drew for `ResolutionEngine` itself). No user authentication or
multi-user support. No mobile/responsive layout requirement. No
real-time collaborative coordination features (the FRD's "coordination
steps... outside ASTRA" — telephone/radio between FMPs — remain
explicitly out of scope, matching the reference ASTRA D2.10
availability note). No historical replay/scrubbing beyond the OQ-4
stretch goal.

## Approval

Pending. Implementation must not begin until this review is approved
and the five open design questions above are resolved (following the
same review-then-build process used for Milestones 6 and 7).
