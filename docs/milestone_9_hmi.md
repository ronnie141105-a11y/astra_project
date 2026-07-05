# Milestone 9 — Sector Complexity & HMI Redesign

Builds on Milestone 8 (`docs/milestone_8_dashboard.md`), which is
unchanged and still accurate for everything it describes. This
milestone (a) exposes data the pipeline already computed but discarded,
(b) adds one new opt-in engine, and (c) replaces the HMI frontend with
a richer, tabbed operational view. No existing engine's *behaviour*
changed; every addition is additive and defaults to a no-op.

## What changed, and why

### 1. `ResolutionCandidate` now keeps what it used to throw away

`ResolutionEngine._evaluate()` already computed a full hypothetical
`ComplexityRegion` and a full hypothetical `PredictionResult` per
candidate, to score it, then discarded both. Milestone 9 keeps them:

- `complexity_before_components` / `complexity_after_components`
  (`Dict[str, float]`, same keys as `ComplexityRegion.components`) —
  the before/after breakdown behind the HMI's component bar chart
  (reference D2.10 Fig. 6).
- `hypothetical_prediction` (`PredictionResult`) — the clearance's
  full re-predicted trajectory, every configured horizon. The
  dashboard serializer reduces this to `hypothetical_path` (just the
  target aircraft's points), used for the what-if vertical/horizontal
  profile plots (reference Figs. 8–9).

All three fields default to `None` under the same conditions
`complexity_after` already did (no cluster match at the evaluated
horizon). Existing positional-arg test construction
(`ResolutionCandidate("SPEED", "A1", ...)`) is unaffected since the new
fields are appended last with defaults.

### 2. Sector complexity (`astra.complexity.sector`, new module)

The reference documents (D2.10 §3.4, FRD) score airspace complexity
two ways: per-hotspot-cluster (already Milestone 4) and per-fixed-
sector, over rolling 5-minute buckets, for the "complexity charts"
page. This was previously an explicit deferred stretch goal.

- `SectorDefinition` (in `astra.utils.config`, not `astra.complexity.sector`
  — avoids a circular import): a **circular** region (`center_lat/lon`,
  `radius_nm`). Simplification vs. a real ANSP's polygon sectors,
  chosen because it turns membership into one `haversine_distance_nm`
  check; swapping in real polygons later would only touch
  `_sector_cluster()`.
- `SectorComplexityEngine`: builds one synthetic `Cluster` per sector
  (every aircraft inside the circle) each cycle and runs it through the
  *existing, unmodified* `ComplexityEngine` — exactly the "treat the
  sector as one big cluster" approach the reference docs describe.
  Owns a rolling per-sector `deque` of `SectorComplexitySample`
  (bucketed by `sector_bucket_s`, capped at `sector_history_buckets`);
  same-bucket cycles overwrite rather than append, keeping the time
  axis regular regardless of poll rate.
- `ASTRAConfig.sectors: List[SectorDefinition] = []` (opt-in, empty by
  default — zero behaviour change for any existing config/test).
- Wired into `Pipeline.run_cycle()`: one `SectorComplexityEngine`
  instance owned by the pipeline (like `TrackerEngine`), called once
  per cycle. `CycleResult` gained `sector_regions` and
  `sector_history`, both `default_factory=dict`.

No sectors are pre-configured by default (including in `main.py`'s
`DEFAULT_CONFIG`) — the sector complexity page renders a "no sectors
configured" placeholder until a config defines some. This was a
deliberate choice to keep the default demo's behaviour identical to
Milestone 8; a demo config with example sectors can be added later
without touching this module.

### 3. Dashboard serializers

- `serialize_resolution_candidate` now includes
  `complexity_before_components`, `complexity_after_components`, and
  `hypothetical_path`.
- New `serialize_sector_regions` / `serialize_sector_history`.
- `serialize_cycle_result`'s top-level payload gained `sector_regions`
  and `sector_history` keys (verified via `/state` end to end, not just
  unit tests — see Verification below).

### 4. Frontend — a modern, tabbed operational HMI

Full rewrite of `astra/dashboard/{index.html,dashboard.css,dashboard.js}`
(flat files, no `templates/`/`static/` subfolders — matches the actual
`server.py` wiring, not Milestone 8's doc prose). Still a single Flask
route (`/`) polling `/state`; still purely a read model, never issues a
clearance. Two tabs:

**Operations** (default):
- **Traffic Projection Display** — the plan-view map, now with a
  **time-horizon scrubber** (slider over whatever horizons
  `regions_by_horizon` actually has that cycle — never hard-coded).
  Scrubbing to `+N min` swaps the complexity heatmap to that horizon's
  regions and draws traffic *at* that horizon (plain markers; heading
  is only known for the observed horizon, drawn as an
  oriented triangle). The full predicted path is still drawn, faint,
  for context. Sector boundaries (dashed circles, from
  `sector_regions`) are drawn too, distinct from hotspot shading.
- **Alerts** table — replaces the old "4DARHAC Hotspots" table.
  Adds **Onset in** (minutes to `predicted_onset_s`, colour-ramped by
  urgency) and **Act by** (`predicted_onset_s` as a sim clock time) —
  `predicted_onset_s` was already computed (Milestone 6) but never
  rendered. **Confidence** is now a coloured percentage (was a bare
  0–1 number before). Added a **Sector** column (nearest configured
  sector by centroid distance, `-` if none configured). Rows are
  clickable to drive the Event panel below. Fixed a pre-existing label
  bug: the old "Peak" column actually displayed `peak_complexity`, not
  a time — now labelled **Complexity** and shows the *current* score
  (`current_complexity_score`), which is what a live alerts view should
  lead with.
- **Event & Dissipation Analysis** panel (new) — the reference HMI's
  Fig. 5/6 equivalent, for the selected alert:
  - Confidence ring (SVG) + before → after complexity, for the
    candidate currently previewed.
  - Ranked candidate list, clickable to preview each one's what-if data.
  - A client-side-only **solution lifecycle**
    (Draft → Proposed → Acknowledged → Canceled), one stage per track,
    stored in a plain JS object keyed by `arhac_id`. **Not persisted**
    anywhere (resets on page reload, not synced across clients) — this
    is tracking what the *FMP* has done with a suggestion, not new
    backend state, matching the design review's "purely a dashboard-side
    state machine" framing. If this needs to survive a reload or be
    shared, it belongs in `astra.dashboard.store`, not here.
  - Vertical (altitude-vs-horizon) and horizontal (lat/lon) what-if
    mini-plots, original vs. hypothetical, built from
    `hypothetical_path` plus the aircraft's real predicted path — no
    new backend computation, per the design review's Tier 2 framing.
  - Before/after component bars, from `complexity_*_components`, one
    row per component with the raw before/after values labelled
    (bars are normalised **per row**, not globally, since the
    components have unrelated units — a density in ac/NM² is not
    comparable to an MTCA count).
- **Timeline** panel — unchanged behaviour, narrower default width to
  fit the new grid.
- **Coordination steps** panel (new) — a collapsed-by-default, static
  disclosure listing the FMP-E → SUP-E → FMP-A → PLC-A → EC-A chain
  (reference D2.10 Fig. 10 / FRD §3.2.2). Deliberately inert: reinforces
  the existing "advisory-only, no clearance issuance" non-goal rather
  than half-implementing coordination.

**Sector Complexity** (new tab): one card per configured sector,
showing its live score and a small history sparkline built from
`sector_history`. Renders a plain-language placeholder if no sectors
are configured, rather than an empty grid.

## Verification

- Full regression suite: **288/288 checks pass**
  (`test_hotspot.py` 24, `test_complexity.py` 42, `test_tracking.py` 44,
  `test_forecast.py` 47, `test_resolution.py` 39, `test_sector.py` 11
  [new], `test_dashboard.py` 81 [was 70; +11 for the new serializer
  fields/sector endpoints]).
- `tests/test_sector.py` (new): sector-membership-by-radius, rolling
  history accumulation/overwrite-on-same-bucket, history window
  capping, empty-sector scoring, and the "no sectors configured" no-op
  path.
- End-to-end smoke test (not just unit tests): built a real `Pipeline`
  with a configured sector and the existing converging-traffic demo
  geometry, ran it through `CycleStore` + the real Flask `test_client`,
  and inspected the actual `/state` JSON — confirmed
  `sector_regions`/`sector_history` populate correctly and a real
  resolution candidate's `hypothetical_path` (5 points, one per
  horizon) and before/after component dicts serialize correctly.
- Frontend: syntax-checked with `node --check`; functionally exercised
  headlessly with `jsdom` against the *real* `/state` fixture above —
  simulated a poll cycle, a candidate-row click, a lifecycle-button
  click, dragging the horizon scrubber to its max, a tab switch, and
  the coordination-panel toggle, with zero uncaught DOM/JS errors and
  the expected DOM state after each interaction (candidate/component
  row counts, sector card count, scrubber label, active tab).

## Explicit simplifications / non-goals (documented, not hidden)

- Sectors are circles, not polygons (see `SectorDefinition`'s docstring).
- The solution lifecycle is client-side and ephemeral — not a new
  backend concept, not persisted, not multi-user-consistent.
- No sectors are pre-configured anywhere; the sector tab is empty until
  a config defines some.
- "Act by" is approximated as `predicted_onset_s` itself (no separate
  "time window to act" concept exists in the backend) — the reference
  HMI's `Act By` is a range within which the pilot must start
  executing; this is the closest single instant available without new
  backend modelling.
