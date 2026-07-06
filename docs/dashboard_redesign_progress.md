# ASTRA Dashboard Redesign — Progress Log

**Read this file first in any new conversation.** It is meant to be a
complete enough record that the redesign can continue without
re-reading the whole repo or re-deriving the feature audit below.

Do not update `README`, `docs/architecture.md`, `docs/PROJECT_STATUS.md`,
or `docs/Developer_Handover.md` until the redesign is fully complete —
per explicit instruction. This file is the only doc that should move
during the redesign.

---

## 0. Where we are right now

- Milestones 1-9 (backend pipeline + Scenario Builder) are stable and
  considered feature-complete. **No backend algorithm may change**
  unless this log explicitly says so and why.
- Phase in progress: **HMI redesign**, driven by
  `ASTRA_report_HMI_pictures.pdf` (EUROCONTROL D2.10 Annex A.8 — Figures
  24-35), treated as the UI/UX spec, not documentation to summarize.
- **Status:**
  1. Feature audit — **done** (§2).
  2. Map architecture (pluggable geo-layers) — **done** (§3).
  3. Operations screen visual pass (radar/aircraft/labels/hotspot/
     urgency/countdown/animation) — **done** (§3a). Per explicit
     instruction, this had to be "essentially complete" before starting
     any additional page, so **nothing in §6's remaining items has been
     started yet** — no two-page IA split, no sector-forecast backend
     extension, no complexity-reference serializer change, no act-by/
     rationale fields.
  4. **Waiting on the user to hand over the Vietnam AIP GeoJSON files**
     (they've said these are already prepared) — the moment they arrive,
     drop them straight into `astra/dashboard/geo/*.json` (§3 tells you
     exactly which file is which layer) and they'll render with zero
     code changes, per the architecture built in this phase.
- **Next action for a new session, in order:** (a) if the geo files
  have arrived, plug them in and screenshot-verify per §3's checklist;
  (b) otherwise, move on to §6 item 3 (two-page IA split), since the
  Operations Workspace visual pass is done and that was the blocker.

---

## 1. Current HMI, as-built (baseline before this redesign)

Single-page app, two tabs, all in `astra/dashboard/`:

- `index.html` — tab shell: **Operations** (map + alerts table + event
  panel + timeline + coordination disclosure) and **Sector Complexity**
  (rolling per-sector history charts).
- `dashboard.js` (~1200 lines now) — polls `/state` every
  `poll_interval_s`; canvas map (see §3a for the current architecture),
  alerts table (`renderTracksTable`), event panel (complexity-reduction
  ring, ranked candidate list, before/after component bars, what-if
  vertical/horizontal SVG profiles), SVG onset/peak/dissipation
  timeline, sector charts tab.
- `dashboard.css` — dark ATC-radar theme (`--bg`, `--accent`, `--amber`,
  `--red` CSS vars already established; reuse these, don't invent a
  second palette).
- `geo_layers.js` + `geo/*.json` — pluggable geo-overlay layer manager,
  see §3.
- Backend surface it reads: **one** endpoint, `GET /state`
  (`astra/dashboard/routes.py` → `serializers.serialize_dashboard_snapshot`).
  Pure read-only consumer of `CycleResult` — computes nothing.
- Scenario Builder (`/scenario`, separate page, done in a prior phase)
  is unaffected by this redesign and out of scope here except that its
  nav link stays working.

This baseline already implements a lot of what the PDF asks for, just
in a different information architecture (one page, not two) and (until
this phase) a plainer visual style. The audit in §2 is about the *gap*,
not a green-field build.

---

## 2. Feature audit — PDF Figures 24-35

Legend for the "Gap classification" column:
- **frontend-only** — data already in `/state`'s payload today; pure UI work.
- **serializer change** — data already exists on a domain object, just
  not exposed by `astra/dashboard/serializers.py` yet. Zero risk to any
  algorithm.
- **small backend extension** — needs a small amount of new
  orchestration code, but *reuses existing pure functions/engines* on
  data they can already accept (no new algorithm, no changed math).
- **genuine new feature** — needs new logic that doesn't exist
  anywhere in the pipeline today. Flagged for an explicit go/no-op
  decision before building, per "do not redesign backend algorithms
  unless strictly necessary."

### Fig 24 / 26 / 28 — Complexity Forecast page

**Screen:** A new top-level page (not currently a page — closest analog
is our "Sector Complexity" tab, which shows the *past*, not the
*future*). Grid of one small bar chart per sector (M1...M6), sector
checkboxes to show/hide charts, a notification bell/panel top-right, a
"Dissipations History" link, current UTC clock.

**Widgets:** per-sector bar chart (20-min buckets, ~2h lookahead,
0-100 y-axis), alert-count badge on bars that contain a hotspot,
sector visibility checkboxes, notification panel (collapsed/expanded),
current-time readout.

**Info displayed:** predicted complexity *per sector, per future time
bucket* — this is a forward-looking time series, distinct from what we
serialize today.

**Backend data existing:**
- `sector_regions` / `sector_history` (`astra/complexity/sector.py`,
  `SectorComplexityEngine`) — but this is the sector's **observed**
  complexity now and a **rolling history of the past** (5-min buckets
  on a deque). It does not look forward.
- `regions_by_horizon` — predicted complexity, but keyed by
  *cluster* (DBSCAN-detected hotspot), not by *named sector*.
- Sectors are circles (`SectorDefinition.center_lat/lon/radius_nm`,
  `astra/utils/config.py`), not the PDF's named polygons — expected;
  tracked separately under the map-architecture work (§3).

**Backend data missing:** a per-sector complexity value *at each
future prediction horizon*, i.e. "M1's predicted complexity in 20
minutes / 40 minutes / ...".

**Gap classification: small backend extension, not a new feature.**
Confirmed by reading the code: `SectorComplexityEngine`'s helper
`_sector_cluster(sector, snapshot)` only calls `snapshot.as_list()`,
and `trajectory.models.PredictedSnapshot.as_list()` has the exact same
signature/return type as `TrafficSnapshot.as_list()`. So the *same*
`_sector_cluster` + `ComplexityEngine.score(...)` call that already
computes a sector's observed complexity can be called once per
horizon on `PredictionResult.at(horizon_min)` with **zero new math**.
Plan (when we get here): add
`SectorComplexityEngine.forecast(prediction: PredictionResult) ->
Dict[str, Dict[int, ComplexityRegion]]` next to the existing
`update()`, wire it into `pipeline.py`'s `CycleResult`, add
`serialize_sector_forecast(...)`. No change to `ComplexityEngine`
itself.

Secondary note: the PDF's chart spans ~2h in 20-min buckets;
`ASTRAConfig.prediction_horizons_min` defaults to `[5, 10, 15, 30, 60]`
(max 60 min). Extending this to cover ~2h is a **config value change**
only (add horizons like 90/120; the trajectory engine is a
parametrized dead-reckoning projector, already horizon-agnostic) — but
accuracy at 2h dead-reckoning range is a product/thesis-scope call, not
mine to make unilaterally. Will flag again at implementation time
rather than silently widening it.

### Fig 26 / 27 — Notification panel

**Screen:** overlay/panel on the Complexity Forecast page.

**Widgets:** collapsed (latest only) / expanded (full list) toggle,
two notification kinds — "New Alert {sector} - Onset: {time}" (pink
dot) and "Sector {X} merged/unmerged to {Y}" (blue dot).

**Info displayed:** a chronological event log mixing two very
different kinds of events.

**Backend data existing:** none — there is no event log anywhere in
the pipeline. `CycleResult` is stateless per cycle; a "new alert
appeared" event only exists implicitly as a `FourDArhac` transitioning
into existence between two polls.

**Backend data missing / classification, split in two:**
- **"New alert" notifications — frontend-only.** The frontend already
  polls every cycle and already has each `track.arhac_id` and
  `track.status`. A new item is just "a `CANDIDATE`/`CONFIRMED` track
  id present this poll that wasn't present last poll" — pure
  client-side diffing against `window.__astraLastCycle`, no backend
  change. (This does mean the log resets on page reload, same
  limitation the rest of the client-only `ui` state already has, e.g.
  the DRAFT/PROPOSED/ACKNOWLEDGED lifecycle buttons — consistent with
  existing precedent.)
- **"Sector merged/unmerged" notifications — genuine new feature, and
  arguably out of scope entirely.** The PDF's own caption says
  sectorisation changes are *"managed outside of ASTRA"* — i.e. this
  is a notice about an external system's action, not something our
  pipeline produces or should simulate. Nothing in `ASTRAConfig`
  represents dynamic sector merge/split at all (sectors are a static
  list read once at startup). **Recommendation: build the alert-only
  half now (frontend-only), stub the sectorisation-change half as
  visually present but sourced from a hardcoded/empty list, and treat
  "simulate sector merges" as a explicit-approval-required feature, not
  an implicit one.** Will not build without confirmation.

### Fig 25 / 29 — Dissipation Workspace (overall layout)

**Screen:** second top-level page. Three coordinated panels: Alerts
Table (left), Event Analysis & Dissipation Panel (below it), Traffic
Projection Display (right, its own dedicated map).

**Gap classification: frontend-only information-architecture change.**
Every panel already exists in some form on our single Operations tab
today (`panel-alerts`, `panel-event`, `panel-map`). The redesign is
mostly: split into two top-level pages matching the PDF's Complexity
Forecast / Dissipation Workspace split instead of Operations / Sector
Complexity, and give the Traffic Projection Display its own map
instance scoped to the selected alert rather than sharing the one
Operations map. No new backend data required for the split itself;
see below for gaps *within* each panel.

### Fig 30 — Alerts table

**Screen:** table within the Dissipation Workspace.

**Widgets:** columns ID / Onset in (min) / Act by (UTC range) / N.
flights involved / Event sector (removable filter chip) / Complexity
(`/100`); sector-filtered rows shown faded, not hidden.

**Backend data existing:** `arhac_id`, `predicted_onset_s`,
`member_aircraft` (count is frontend-derivable), `current_complexity_score`.
We already render an equivalent table today (`renderTracksTable`).

**Backend data missing:**
- **"Act by" as a *range*, not an instant.** We only have
  `predicted_onset_s` (one instant). The PDF's "Act by: 14:28-14:43" is
  a window — presumably [start of the window pilots must act within,
  latest safe time]. **Genuine new (small) concept**, not currently
  modelled by `ForecastEngine`. Needs a product decision: is this
  `[onset - lead_time, onset]`, or `[onset, dissipation]`, or something
  ATC-procedural we're inventing for the thesis? Flagging for explicit
  confirmation before adding a config knob + field.
- **Authoritative event sector.** We currently derive "nearest sector"
  client-side via a rough centroid distance (`nearestSectorName()` in
  `dashboard.js`) against circular `sector_regions` — a heuristic, not
  an authoritative membership test. Correctness improves once real
  sector polygons exist (§3); until then, **frontend-only** (keep the
  heuristic) is the pragmatic choice, with a note that the "faded rows
  on filter" interaction is pure frontend filtering over the same
  already-fetched track list.
- Everything else in this table (ID/onset/flights/complexity):
  **frontend-only**, we already have the data.

### Fig 31 — Event Analysis & Dissipation panel ("solution proposal")

**Screen:** detail panel within the Dissipation Workspace.

**Widgets:** lifecycle stepper (Draft → Proposed → Acknowledged) +
Reject/Proceed buttons (**we already have an equivalent** —
`lifecycleButtons()`/`LIFECYCLE_STAGES`, client-side only, same
limitation as today); numbered solution-proposal pager (1-5); two
complexity rings (before=red, after=green) instead of our current
text-based before→after; confidence as a horizontal progress bar
instead of our ring; per-flight table with FL (predicted-target),
groundspeed, vertical rate, Act by, Action sector, plus inline
annotation rows ("Horizontal trajectory change", "Level capping at FL
330").

**Backend data existing:** `ResolutionCandidate` already carries
`complexity_before`/`complexity_after`/`resolution_score`/`delta_value`/
`clearance_type`/`target_callsign`, and `ResolutionSet.candidates` is
already the ranked list the "1 2 3 4 5" pager would page through — we
render this today as a plain list (`renderCandidateList`), a pager is
a pure layout change.

**Backend data missing:**
- **Two rings + horizontal confidence bar instead of one ring for
  confidence and plain before→after numbers.** Purely presentational —
  **frontend-only**; `confidenceRingSvg()` already exists and is
  generic enough to reuse for the complexity rings too (it takes any
  0-1 value and a colour function).
- **Per-flight table showing *every* member aircraft, most of them
  unaffected ("-" for Act by/Action sector) with two of them
  highlighted as directly modified.** This is where the PDF's design
  and our M7 scope genuinely diverge: **`ResolutionCandidate` is
  single-lever, single-aircraft by explicit design** (see
  `docs/milestone_7_resolution.md` / the model's own docstring, OQ-2).
  The PDF shows a candidate that clears *two* aircraft (SWR002 *and*
  SWR004) in one proposal. Reproducing that literally is a **genuine
  new feature** (multi-aircraft joint resolution) and out of scope per
  "do not redesign backend algorithms." **Recommendation: keep
  single-aircraft candidates, but design the table to show every
  `track.member_aircraft`, with real values (from the current
  snapshot) for the untouched ones and Act-by/Action-sector only on
  `target_callsign`'s row — visually equivalent to the PDF's "mostly
  dashes" rows without pretending we generate multi-aircraft
  solutions.** This is **frontend-only** given that framing.
- **"Action sector"** (e.g. "LSAZ") **per candidate** — same
  authoritative-sector gap as the alerts table; same interim
  workaround (frontend heuristic against circular sectors now, real
  polygon test later).
- **"Level capping at FL 330" / "Horizontal trajectory change"
  annotation rows** — human-readable rationale strings. Nothing in
  `ResolutionCandidate` carries free text today. Two ways to get this
  and neither touches the resolution *scoring* algorithm:
  (a) **frontend-only heuristic** — derive the label purely from
  `clearance_type` + sign of `delta_value` (e.g. `FLIGHT_LEVEL` +
  negative delta → "Level capping at FL {target}"); cheap, but the
  label is then UI guesswork, not something the engine actually
  asserts; or
  (b) **serializer change** — have `astra.resolution.candidates`
  (which already knows exactly which lever it built and why) attach an
  existing-data-only description string when constructing the
  candidate, and just pass it through in `serialize_resolution_candidate`.
  **Recommend (b)** — more honest, and it's a one-field addition to an
  already-frozen dataclass plus a one-line serializer change, not an
  algorithm change. Flagging for confirmation since it does touch
  `astra/resolution/candidates.py`, technically backend code, even
  though it changes no scoring math.

### Fig 32 — Traffic Projection Display

**Screen:** the Dissipation Workspace's own dedicated radar, scoped to
the selected alert (distinct from a general traffic map).

**Widgets:** radar-styled map with sector polygon + waypoint stars,
aircraft position dots + labels, dissipation on/off toggle, trajectory
filter chips (Changes / Involved / All), zoom +/- controls, time
slider with the hotspot's onset-dissipation window highlighted on the
track.

**Backend data existing:** `snapshot`, `prediction.paths` (per-horizon
points, all aircraft), each candidate's `hypothetical_path`,
`predicted_onset_s`/`predicted_dissipation_s` per track — we already
draw an equivalent map on the Operations tab (`renderMap` +
`drawFaintPredictedPaths`/`drawScrubbedTraffic`) plus the what-if
horizontal profile SVG.

**Backend data missing:**
- Sector polygons / waypoint markers — **explicitly deferred**, this
  is exactly what the pluggable-geo-layer work (§3) is for. Not
  duplicating that effort here.
- **Zoom, dissipation on/off toggle, trajectory filter chips:
  frontend-only** — all pure client-side view state over data we
  already have (`hypothetical_path` for "with dissipation",
  `prediction.paths` for "without"; filter chips just choose which
  callsigns to draw).
- **Time slider smoothness.** Our horizons are coarse
  (`[5, 10, 15, 30, 60]` by default) so a slider dragged continuously
  would jump between 5 sparse points. Two options, neither touching
  the trajectory *algorithm*: (a) **frontend-only** — linearly
  interpolate between adjacent horizon points for display (cheap,
  approximate, fine for a smooth-looking slider since the underlying
  model is itself dead-reckoning/near-linear); or (b) add more entries
  to `prediction_horizons_min` (config-only, more compute per cycle,
  exact rather than interpolated). **Recommend (a)** to avoid a
  performance/config tradeoff; flagging in case there's a reason to
  prefer (b).

### Fig 33 — Complexity Reduction (XAI) modal

**Screen:** modal, reachable from the Event Analysis panel.

**Widgets:** before/after complexity rings (red/green, `/100`),
confidence percentage, 4 "Factor" bars each showing `value / max` for
before and after.

**Backend data existing:** `complexity_before_components` /
`complexity_after_components` (raw per-component values, e.g.
`density_ac_per_nm2`, `mtca_count`, ...) — we already render an
equivalent bar chart today (`renderComponentBars`), just without the
`/max` denominator or human labels.

**Backend data missing:** the **reference/saturation constant each
component is normalized against** — these already exist as plain
config values (`ASTRAConfig.complexity_density_reference_ac_per_nm2`,
`complexity_mtca_reference_count`, `complexity_ltca_reference_count`,
`complexity_heading_div_reference_deg`, `complexity_alt_div_reference_ft`,
`complexity_type_mix_reference_count`) but are never surfaced past the
engine that consumes them internally.

**Gap classification: serializer change only.**
`serialize_cycle_result` already receives `config` as a parameter for
an unrelated reason (`dashboard_max_resolution_candidates_shown`) — so
threading these six existing numbers through
`serialize_resolution_candidate`/a small new
`serialize_complexity_reference(config)` helper is a same-shaped,
low-risk change. No new computation, no changed weights — just
exposing constants that already exist and are already used, unchanged,
by `ComplexityEngine`.

### Fig 34 — Vertical Profile modal

**Screen:** modal, reachable per selected flight.

**Widgets:** altitude-vs-time chart, one colored pill/band per aircraft
occupying each flight level over each time window, a blue "no-go zone"
rectangle (a FL × time window where crossing it would create a
proximity event with another aircraft), sector-transition tick marks
on both the original and modified trajectory, dashed magenta = modified
trajectory, solid = original/reference level.

**Backend data existing:** `cycle.prediction.paths` already has every
aircraft's predicted `(horizon_min, lat, lon, altitude_ft)`, not just
the selected one's — we only ever *plot* the selected candidate's
target aircraft today (`renderWhatIfVertical`), but the data for every
other aircraft in the same window is already sitting in the same
payload, unused by this chart.

**Backend data missing, split in three:**
- **Every aircraft's altitude band alongside the selected one** —
  **frontend-only.** The data is already there (`cycle.prediction.paths`
  for every callsign); today's chart just doesn't loop over the other
  callsigns. No backend change.
- **"No-go zone" (a FL × time window where crossing it enters a
  proximity event with another aircraft).** This is a genuine
  pairwise-separation check that no module computes today —
  `astra.hotspot` clusters by spatial proximity at a single instant, it
  doesn't forecast a time-window of predicted minimum separation
  between a specific pair. **Genuine new feature.** It *can* be built
  as a pure function over data we already have (every aircraft's
  predicted horizon points), using the same `mtca_distance_nm`/
  `mtca_time_min` style thresholds already in `ASTRAConfig` — so it
  doesn't need a new pipeline stage, just a new pure computation,
  candidate location `astra/dashboard/profile_analytics.py` (dashboard-
  local, like `serializers.py`, not touching `astra.hotspot`) or,
  if we want it testable/reusable beyond the HMI, a small new function
  in `astra.hotspot`. **Flagging for a build/defer decision** — this is
  the single largest "new logic" item in the whole audit.
- **Sector-crossing markers on the predicted path.** Testable today
  only against circular sectors (approximate); becomes accurate once
  real polygons exist (§3). **Frontend-only for now** (test predicted
  points against `sector_regions` circles), revisit precision after
  the AIP lands.

### Fig 35 — Coordination steps

**Screen:** full-screen step-by-step disclosure, reachable after
accepting a solution.

**Widgets:** actor legend (FMP-E/Sup-E/FMP-A/Planner-A/Executive-A),
numbered action-item checklist, some items shown as already
"accepted", Next button.

**Backend data existing/needed: none.** The PDF's own text says this
coordination happens *"outside ASTRA using conventional communication
methods (e.g. telephone)"* — same conclusion as our existing
`panel-coordination` disclosure, which already states this. We already
have a static version of this (4 hardcoded steps, no per-step state).

**Gap classification: frontend-only.** Rebuild as an interactive
checklist (actor legend + numbered items + local-only per-step
"acknowledged" state), following the exact client-side-state pattern
already used for the DRAFT/PROPOSED/ACKNOWLEDGED lifecycle buttons
(`ui.lifecycle`). No persistence beyond the current page session,
consistent with existing precedent — unless multi-user persistence is
explicitly wanted, which would be a small new backend endpoint (not
built unless asked).

---

## 3. Map architecture (pluggable geographic layers) — DONE

Requirement (repeated across three messages): geographic overlays (FIRs,
sectors, airways, waypoints, airports, coastlines) must load from
external JSON files; the renderer must not hardcode Vietnam (or any
other) geometry; when the AIP is supplied later, it gets converted into
these data files and just plugs in — no architecture change at that
point.

**Built as:**
- `astra/dashboard/geo_layers.js` — `GeoLayerManager` class. Has zero
  geographic knowledge: no coordinates, no place names, nothing. It
  only knows how to fetch a manifest + each layer's GeoJSON file and
  draw `Polygon`/`MultiPolygon`/`LineString`/`MultiLineString`/
  `Point`/`MultiPoint` generically given any `project(lat, lon) ->
  [x, y]` function. One shared instance (constructed once in
  `dashboard.js`) is intended to be reused by every map the dashboard
  ever has — today that's the Operations map; when the Dissipation
  Workspace's Traffic Projection Display (§6 item 3 / Fig 32) is built,
  it must construct its `project()` the same way and call
  `geoLayers.draw(ctx, project)` on the *same* `geoLayers` instance —
  **do not instantiate a second `GeoLayerManager`**, that would be
  exactly the "duplicate widget" the last two messages said to avoid.
- `astra/dashboard/geo/manifest.json` — the layer registry: for each
  layer, its `id`, `label` (used by the toggle checkboxes), `kind`
  (`polygon`/`line`/`point` — this is the only vocabulary the renderer
  understands, by design), `file`, `default_visible`, `z_index`,
  `label_field` (which GeoJSON `properties` key to draw as a text
  label, if any), and a `style` dict (stroke/fill/width/dash for
  polygon+line, marker/fill/size for point). **This manifest is the
  entire "how do I add a layer" interface** — adding a new overlay type
  is "add one entry here + one new GeoJSON file," never a renderer
  change, as long as it's a polygon/line/point (it always will be for
  FIR/sector/airway/waypoint/airport/coastline).
- `astra/dashboard/geo/{firs,sectors,airways,waypoints,airports,coastlines}.json`
  — one empty (`"features": []`) `FeatureCollection` per layer today,
  each with a `_meta` block describing its expected schema. **These six
  files are exactly where the Vietnam AIP conversion output goes.**
  Standard GeoJSON, coordinates `[lon, lat]` (not `[lat, lon]` — the
  renderer assumes this and flips it internally when calling `project`).
- Served as plain static files — `fetch()`, no new Flask route. One
  gotcha already hit and fixed: this app's Flask static prefix is
  `/dashboard/...`, **not** the Flask default `/static/...` (because
  `create_app()` passes `static_folder=<astra/dashboard dir>` without an
  explicit `static_url_path`, so Flask derives the prefix from the
  folder's basename). `index.html` injects the *real* URL as
  `window.ASTRA_GEO_MANIFEST_URL` via `url_for('static', ...)` at
  render time — `geo_layers.js` never hardcodes `/static/`. If a future
  page (e.g. the Dissipation Workspace) needs the manifest URL too, use
  the same `url_for` injection pattern, don't hardcode a path there either.
- Wired into `dashboard.js`: `geoLayers.draw(ctx, project)` is called
  from `renderMap()` between the background grid and the sector/hotspot
  overlays (so basemap layers sit visually underneath traffic and
  complexity rings). `computeBounds()` also walks every visible layer's
  feature coordinates via a small `forEachCoordinate()` GeoJSON-geometry
  walker, so once real FIR/sector polygons load, the map auto-fits them
  — this was *not* optional: without it, a real FIR polygon bigger than
  the current traffic extent would render partially off-screen. A
  layer-toggle checkbox row (`#map-layer-toggles`, built from
  `geoLayers.getToggleList()`) lets the operator show/hide each layer;
  built generically off the manifest, so it needed zero changes to
  support any future 7th layer type.

**How to plug in the Vietnam AIP once the files arrive:**
1. Convert AIP FIR polygons → `astra/dashboard/geo/firs.json` (GeoJSON
   `Polygon`/`MultiPolygon` `Feature`s, `properties.name` = FIR name).
2. Convert sectors → `geo/sectors.json` the same way,
   `properties.name` should match `ASTRAConfig.sectors[].name` so the
   existing sector-complexity overlay (`drawSectorBoundaries`, which
   still draws the *circular* `sector_regions` from the pipeline) and
   the new polygon layer visually correspond to the same named sectors.
   (Migrating `SectorDefinition` itself from circles to polygons is a
   separate, not-yet-scoped decision — see §9 — this layer can render
   real polygons *visually* before that migration happens; they just
   won't yet be the thing the complexity engine's clustering tests
   against.)
3. Waypoints/airports/airways/coastlines → their same-named files, same
   pattern.
4. Reload the page. That's the entire integration step — no JS, no
   manifest change, no Python change, unless a genuinely new geometry
   kind or style is needed (unlikely for these six layer types).
5. Verify with the same check used to build this (see §3a's testing
   notes): open `/`, confirm the layer toggles list the right 6 names,
   toggle each on/off and confirm the canvas changes, and confirm
   `computeBounds()` widened to include the new geometry (the whole FIR
   should be visible, not cropped).

---

## 3a. Operations screen visual pass — DONE

Scope: everything in the original "improve every visual component" list
that applies to the *Operations* tab specifically (radar rendering,
aircraft symbols/labels, hotspot visualization, confidence/urgency
indicators, countdown timers, smoother animations, responsive layout).
Per instruction, this had to be "essentially complete" before starting
the two-page IA split or any new page — it is; see the checklist below.
Everything here is **frontend-only**, reusing data already in `/state`;
no backend/serializer change was needed for any of it.

- **Radar rendering** (`drawGrid`) — kept the existing faint square
  lat/lon grid (dimmed further) and added concentric range rings +
  crosshair centred on the canvas, the actual visual cue that reads as
  "radar" rather than "map." Purely cosmetic, no data dependency.
- **Aircraft symbols/labels** — unified into one function,
  `drawAircraftMarker(ctx, project, ac, opts)`, used by *every* place an
  aircraft gets drawn (observed/interpolated traffic at horizon 0,
  predicted-position dots at other horizons) — **this is the "don't
  duplicate widgets" principle applied to the marker itself**: one
  drawing routine, callers only vary `{color, showHeading}`. Adds: a
  heading-triangle + speed leader line (length scales with
  `ground_speed_kt`, standard ATC "velocity vector") when heading is
  known; a plain dot when it isn't (predicted-horizon points carry no
  heading); a boxed, semi-opaque label background so the callsign/FL
  text stays legible over the radar background/hotspot rings instead of
  floating as bare text.
- **Hotspot visualization / urgency indicators** — `drawComplexityRegions`
  now derives its ring styling from **onset urgency**, not just the
  complexity score: a new shared `urgencyBucket(onsetInS)` /
  `urgencyColor(bucket)` pair (soon ≤5 min → red solid + a second outer
  "target lock" ring, near ≤15 min → amber dashed, far → blue dashed,
  no linked track → falls back to the old complexity-score colour).
  `onsetClass()` (used by the alerts table's row styling) now calls the
  *same* `urgencyBucket()` instead of duplicating its own thresholds —
  one urgency definition, two renderers, per "reuse wherever
  appropriate." A hotspot ring finds "its" track via a new
  `nearestTrack(lat, lon, tracks, maxNm)` centroid match (same
  distance-heuristic pattern as the existing `nearestSectorName`), and
  if that track has a `forecast_urgency_rank`, draws it as a small
  numbered badge on the ring — reusing data that already existed in the
  payload but wasn't shown on the map before.
- **Aircraft-level urgency highlight** — `buildAircraftHighlightMap(cycle)`
  builds a one-time-per-poll `{callsign: {color, bucket}}` map from every
  open track's `member_aircraft`, so an aircraft that's part of an
  urgent hotspot is drawn in that hotspot's urgency colour instead of
  the flat default teal — ties the traffic layer and the hotspot ring
  together visually without new data.
- **Countdown timers** — the alerts table's "Onset in" column changed
  from a once-a-minute `"N min"` label to a live `mm:ss` via a new
  `countdownFmt(seconds)`. Since `poll_interval_s` defaults to 1s, this
  already ticks smoothly once per second with no extra timer/animation
  loop needed — deliberately the cheapest correct fix rather than adding
  a second, sub-poll-interval ticking mechanism for no visible benefit.
- **Smoother animations** — the map is now **two stacked canvases**
  (`#map-canvas` static base layer, `#map-traffic-canvas` transparent
  overlay, same pixel dimensions, absolutely positioned via a new
  `.map-stack` wrapper). `renderMap()` draws the static
  background/geo-layers/sector+hotspot rings/faint predicted paths
  **once per poll** and caches its `project()` function + the aircraft
  highlight map on `ui.mapProject`/`ui.aircraftHighlight`. A separate
  `requestAnimationFrame` loop (`animateTrafficOverlay` →
  `renderTrafficOverlay`) redraws *only* the traffic-marker canvas every
  frame, linearly interpolating each observed aircraft's position
  between the previous and current poll (`interpolatedObservedAircraft()`,
  driven by wall-clock fraction elapsed via `ui.prevCycleAtMs`/
  `ui.curCycleAtMs`) — so aircraft glide instead of visibly jumping once
  a second. Bounds/projection are deliberately *not* recomputed per
  animation frame (that would make the view "breathe" as interpolated
  positions shift slightly) — confirmed by test that the base canvas's
  pixels are byte-identical between animation frames within the same
  poll, while the traffic canvas's pixels differ frame-to-frame.
- **Confidence visualization** — left as-is (`confidenceRingSvg`);
  already generic/reused per the original audit, no gap to close here.
- **Responsive layout** — already had a `@media (max-width: 1100px)`
  single-column collapse; the new `.map-stack` uses percentage
  width/fixed height so it degrades the same way without any new
  breakpoint.

**Verification performed** (headless Chromium via Playwright, run
against `python3 main.py --mock`):
- Zero console/page errors on load, on horizon-scrubbing, and on
  toggling a geo layer.
- `#map-layer-toggles` renders exactly the 6 manifest layers with
  correct labels.
- `#map-canvas.toDataURL()` identical across two animation frames
  within one poll (static layer not needlessly redrawn).
- `#map-traffic-canvas.toDataURL()` *differs* across animation frames
  400ms apart (interpolation loop actually running).
- `tests/test_dashboard.py` (81/81) and the other milestone regression
  scripts still pass unmodified — nothing here touched backend code.
- `node --check` on both `dashboard.js` and `geo_layers.js`.

---

## 4. Decisions needed before/while coding — resolved (recommended option used unless noted)

Per instruction: use the recommended option for each, unless doing so
would require a substantial architectural change (in which case: defer,
noted below). **None of these are implemented yet** — they're decided,
not built; building them is §6 items 4-6, after the two-page IA split.

1. **"Act by" window** (Fig 30/31/32) — **decided:**
   `[predicted_onset_s - lead_time, predicted_onset_s]`, a new
   `ASTRAConfig` knob (e.g. `resolution_act_by_lead_min`, default TBD at
   implementation time). Computable entirely in the serializer from
   data the track already has — no new pipeline stage.
2. **Sectorisation-change notifications** (Fig 26/27) — **decided:**
   build only the "new alert appeared" half (frontend-only diffing of
   `track.arhac_id` across polls). Sector-merge/unmerge events are *not*
   simulated — the PDF's own caption says that's external to ASTRA.
3. **Level-capping / trajectory-change rationale strings** (Fig 31) —
   **decided:** small backend field. Add one descriptive string to
   `ResolutionCandidate` (populated in `astra/resolution/candidates.py`
   from data the candidate already has — `clearance_type` + sign of
   `delta_value` — at construction time), threaded through
   `serialize_resolution_candidate`. One field on an already-frozen
   dataclass + one serializer line; not a scoring/algorithm change.
4. **"No-go zone" pairwise proximity forecast** (Fig 34) — **deferred,
   not building in this pass.** This is the one item that *would* be a
   substantial architectural change (a new pairwise-separation-forecast
   computation across every aircraft pair and horizon exists nowhere
   today) — per "use recommended unless substantial," this is the
   explicit exception. The vertical profile modal will still gain the
   *other* two Fig-34 improvements (every aircraft's altitude band,
   approximate sector-crossing markers) since those are frontend-only;
   just not the no-go rectangle. Revisit only on explicit request.
5. **Multi-aircraft single-solution candidates** (Fig 31) — **decided:
   not building.** Outside Milestone 7's explicit single-aircraft-lever
   scope; noted so it doesn't get re-litigated as a "missing feature."
6. **2-hour forecast horizon** (Fig 24) — **decided:** widen
   `prediction_horizons_min`/`max_prediction_horizon_min` via config
   when building the sector-forecast feature (§6 item 4); dead-reckoning
   accuracy at that range is accepted as a known limitation, not a
   blocker, consistent with "config-only change."

## 5. Completed features

1. **Map architecture** — pluggable `GeoLayerManager` + manifest + 6
   empty layer files. See §3.
2. **Operations screen visual pass** — radar background, unified
   aircraft marker (heading triangle/leader line/label box or dot),
   urgency-driven hotspot rings + aircraft highlighting, live mm:ss
   countdown, two-canvas animated traffic interpolation. See §3a.

## 6. Pending features (in rough build order)

1. ~~Map architecture~~ — done, §3.
2. ~~Visual pass on Operations screen~~ — done, §3a.
3. **Next up:** Information-architecture split into two pages
   (Complexity Forecast / Dissipation Workspace) per Figs 24-32, reusing
   existing panels (alerts table, event panel, map) rather than
   rebuilding them — this is a layout/routing change, not a new-widget
   change. The Traffic Projection Display for the Dissipation Workspace
   must reuse the *same* `geoLayers` instance and the *same*
   `drawAircraftMarker`/`drawComplexityRegions` functions as the
   Operations map — extract them out of the current single-page
   `dashboard.js` into a shared module if the two-page split needs them
   in two different JS files; do not fork copies.
4. Sector complexity **forecast** (small backend extension, §2 Fig 24,
   decision §4.6) — `SectorComplexityEngine.forecast()`, reusing
   `_sector_cluster` on each horizon's `PredictedSnapshot`.
5. Complexity-reference constants through the serializer (§2 Fig 33).
6. Act-by window field (§4.1) and resolution rationale string (§4.3).
7. Notification panel (new-alert half only, §4.2) and coordination-steps
   checklist restyle (Fig 35) — both frontend-only, can happen alongside
   the IA split in item 3 rather than as a separate pass.

## 7. Design decisions log

- Reuse `dashboard.css`'s existing CSS variables/palette; do not
  introduce a second theme.
- Prefer exposing existing config constants / calling existing pure
  functions on already-available data over inventing new pipeline
  stages — see the "small backend extension" items in §2, all of which
  reuse code that already exists for a different purpose.
- Client-side-only state (lifecycle buttons, notification diffing,
  coordination checklist) follows the precedent already set by
  `ui.lifecycle` in `dashboard.js` — no backend persistence added for
  session-local UI state unless a specific reason to persist emerges.
- **One drawing function per visual concept, regardless of how many
  figures/screens reference it.** Concretely: one `drawAircraftMarker`
  for every aircraft drawn anywhere; one `GeoLayerManager` instance for
  every map; one `urgencyBucket`/`urgencyColor` pair used by both the
  map and the alerts table. When the two-page IA split (§6 item 3)
  creates a second map (Traffic Projection Display), it must import/
  reuse these, not re-implement them — this was an explicit instruction,
  not just a style preference.
- Static geometry (bounds, base layers) is redrawn once per poll cycle;
  only genuinely time-varying display state (interpolated aircraft
  position) is redrawn per animation frame. Don't recompute anything
  per-frame that doesn't need sub-poll-interval freshness — it's wasted
  cycles at best and visually jittery ("breathing" bounds) at worst.
- Flask's static URL prefix in this app is `/dashboard/...`, not the
  default `/static/...` (derived from `static_folder`'s basename, since
  `create_app()` doesn't set an explicit `static_url_path`). Any new
  frontend code that needs a static asset URL must get it from the
  server via `url_for(...)` injected into the page, never hardcode
  `/static/`.

## 8. Backend additions made so far

*(none yet — everything through §3a was frontend/static-data-file only.
§6 items 4-6 will be the first genuine backend touches of this phase,
each already scoped in §2/§4 as either a serializer change or a small,
non-algorithmic extension.)*

## 9. Remaining work / open questions

- Execute §6 items 3-7 in order.
- New, not-yet-scoped question surfaced while building §3: once the
  Vietnam AIP's *sectors* land as real polygons (`geo/sectors.json`),
  should `ASTRAConfig.sectors` (currently circles, used by the
  complexity-scoring pipeline) eventually migrate to polygons too, so
  the visual sector layer and the scoring engine's sector membership
  test are the same geometry? Not needed for this redesign (the visual
  layer and the scoring circles can coexist, as they do today for the
  existing circular sectors), but flag it rather than silently letting
  the two permanently diverge. No action needed unless/until asked.
- Otherwise see §4 for already-resolved decisions and their rationale.
