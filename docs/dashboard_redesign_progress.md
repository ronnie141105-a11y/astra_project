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
- **Status: feature audit complete (this document, §2). No redesign
  code has been written yet** — per instruction, coding does not start
  until the audit is reviewed.
- A separate, already-started thread of work: making the map's
  geographic layers (FIR/sector/airway/waypoint/airport) pluggable and
  JSON-file-driven, with **no Vietnam geometry hardcoded**, so the AIP
  can be dropped in later without an architecture change. Track that
  work in §3 below; it is scoped independently of the figure audit but
  the Traffic Projection Display (Fig 32) is its main consumer.

---

## 1. Current HMI, as-built (baseline before this redesign)

Single-page app, two tabs, all in `astra/dashboard/`:

- `index.html` — tab shell: **Operations** (map + alerts table + event
  panel + timeline + coordination disclosure) and **Sector Complexity**
  (rolling per-sector history charts).
- `dashboard.js` (867 lines) — polls `/state` every `poll_interval_s`;
  canvas map (`renderMap`), alerts table (`renderTracksTable`), event
  panel (complexity-reduction ring, ranked candidate list, before/after
  component bars, what-if vertical/horizontal SVG profiles), SVG
  onset/peak/dissipation timeline, sector charts tab.
- `dashboard.css` — dark ATC-radar theme (`--bg`, `--accent`, `--amber`,
  `--red` CSS vars already established; reuse these, don't invent a
  second palette).
- Backend surface it reads: **one** endpoint, `GET /state`
  (`astra/dashboard/routes.py` → `serializers.serialize_dashboard_snapshot`).
  Pure read-only consumer of `CycleResult` — computes nothing.
- Scenario Builder (`/scenario`, separate page, done in the prior phase)
  is unaffected by this redesign and out of scope here except that its
  nav link stays working.

This baseline already implements a lot of what the PDF asks for, just
in a different information architecture (one page, not two) and a
plainer visual style. The audit below is about the *gap*, not a
green-field build.

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

## 3. Map architecture (pluggable geographic layers) — tracked here, built alongside

Requirement (repeated across two messages): geographic overlays (FIRs,
sectors, airways, waypoints, airports, coastlines) must load from
external JSON files; the renderer must not hardcode Vietnam (or any
other) geometry; when the AIP is supplied later, it gets converted into
these data files and just plugs in — no architecture change at that
point.

**Status: not yet designed/built** (this document was written to
complete the figure audit first, per instruction). Will design as:
- A small `GeoLayerManager` (or similarly named) module owned by the
  dashboard frontend, independent of `dashboard.js`'s pipeline-data
  rendering — it has no opinion about tracks/candidates/complexity, it
  only knows how to fetch, cache, and draw named layers.
- Each layer type (fir, sector, airway, waypoint, airport, coastline)
  gets its own JSON schema (GeoJSON-flavoured — likely literal GeoJSON
  `Feature`/`FeatureCollection` for polygon/line layers so we're not
  inventing a bespoke format) and its own default style, but shares one
  loader/cache/toggle mechanism.
- Files live under a new `astra/dashboard/static/geo/*.json` (or
  similar) directory, served as static files — reading them is a
  `fetch()`, not a new Flask route, unless we want server-side
  validation on load (undecided, will revisit when the AIP arrives).
- Zero coordinates for Vietnam (or anywhere) are hardcoded in the
  renderer; an empty/missing layer file simply means that layer draws
  nothing, not an error. This is enforceable by construction: the
  renderer only ever receives a manifest of "layer name → file path"
  plus generic draw code, never a literal polygon.
- The Traffic Projection Display (Fig 32) and any future Operations
  map both consume the same `GeoLayerManager`, so building it once
  benefits both screens.

Will expand this section with the concrete module/file layout once
implementation starts.

---

## 4. Decisions needed before/while coding (do not proceed silently on these)

1. **"Act by" window** (Fig 30/31/32) — is it `[onset - lead, onset]`,
   `[onset, dissipation]`, or something else? Needs a definition before
   any backend field is added.
2. **Sectorisation-change notifications** (Fig 26/27) — build only the
   alert-diff half now, or also fake/stub sector-merge events? Leaning
   toward "alert half only," see §2.
3. **Level-capping / trajectory-change rationale strings** (Fig 31) —
   OK to add one descriptive string field to `ResolutionCandidate` (via
   `astra/resolution/candidates.py`), or keep it a pure frontend
   heuristic? Leaning toward the small backend field (more honest),
   see §2.
4. **"No-go zone" pairwise proximity forecast** (Fig 34) — build now as
   a new pure-function module, or defer? This is the biggest net-new
   piece of logic in the whole audit; flagging explicitly rather than
   deciding unilaterally.
5. **Multi-aircraft single-solution candidates** (Fig 31, SWR002 +
   SWR004 both modified) — confirmed *not* building this (out of M7's
   explicit single-aircraft-lever scope); noted so nobody re-litigates
   it as a "missing feature" later.
6. **2-hour forecast horizon** (Fig 24) vs. current 60-min max — widen
   `prediction_horizons_min`/`max_prediction_horizon_min`, and accept
   whatever accuracy dead-reckoning gives at that range? Config-only
   change, but changes what the chart implies about model confidence.

## 5. Completed features

*(none yet — audit phase only so far)*

## 6. Pending features (in rough build order once decisions above land)

1. Map architecture — pluggable geo-layers (§3), no Vietnam geometry.
2. Visual pass on existing Operations screen: radar background, aircraft
   symbols/labels, hotspot region rendering, confidence/urgency visuals
   — reusing existing data, no backend change.
3. Information-architecture split into two pages (Complexity Forecast /
   Dissipation Workspace) per Figs 24-32, reusing existing panels.
4. Sector complexity **forecast** (small backend extension, §2 Fig 24) —
   `SectorComplexityEngine.forecast()`.
5. Complexity-reference constants through the serializer (§2 Fig 33).
6. Everything gated behind §4's open decisions.

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

## 8. Backend additions made so far

*(none yet)*

## 9. Remaining work / open questions

See §4. Nothing else outstanding beyond "get answers to §4, then start
on §6 in order."
