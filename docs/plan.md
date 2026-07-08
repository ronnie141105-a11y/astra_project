## Plan: ASTRA HMI UI Parity

TL;DR - Make the dashboard look and behave like the EUROCONTROL ASTRA HMI with some functional changes: improve canvas/font fidelity, add UI toggles (sector, aircraft labels), remove radar rings, make aircraft spawn follow airways using existing geo/airways.json (mock connector), add an aircraft panel, and fix alert overflow and conflict/hotspot discrepancies. This plan documents the specific files to change, step-by-step implementation tasks, verification steps, and blockers for handoff to an implementation agent. use color scheme same as ASTRA UI showed in picture.

**Steps**
1. Discovery review (complete): identified UI and backend files involved in rendering, scenario spawn, geo layers, mock connector, hotspot and conflict logic.
2. UI toggles and controls: add `showAircraftLabels` and optional `showRadarRings` UI flags and corresponding controls in the dashboard UI.
3. Canvas DPI & fonts: implement devicePixelRatio-aware canvas backing store resizing; bump CSS font sizes and adjust `ctx.font` to match; set textBaseline/textAlign and consider line coordinate rounding.
4. Remove radar circle: disable the concentric rings/crosshair drawing in `drawGrid()` or gate with a toggle.
5. Scenario airway spawning: add UI to pick airways (`/scenario/airways` endpoint), extend scenario payload to include airway/route metadata, and implement route-following behaviour in `MockConnector` by storing waypoint lists and updating `_propagate_positions()`.
6. Aircraft panel & alert overflow: add a new aircraft panel UI element, implement `renderAircraftPanel()`, and fix alerts overflow via a scroll wrapper and adjusted CSS (`table` inside `.table-scroll`).
7. Conflict/hotspot investigation: reproduce failing cases, check DBSCAN params and distance metric in `hotspot/distance.py`, and verify CPA thresholds in `complexity/conflict.py`.
8. Tests & validation: add unit tests for route-following, run existing hotspot/complexity tests, and perform manual UI checks on toggles, label hiding, and high-DPI fidelity.

**Verification**
1. Unit tests: run existing tests and add tests for MockConnector route-following. Re-run `pytest tests/` and expect no regressions.
2. UI checks: in browser devtools, simulate DPR values, verify text sharpness, toggle `Labels` and `Sectors`, remove `Radar rings`, spawn airway-following aircraft, and confirm they follow airway geometry.
3. Alerts panel: populate many alerts and verify `.table-scroll` prevents overflow and retains column widths; verify cell ellipsis or wrapping per column design.
4. Conflict vs hotspot: reproduce the bug with recorded snapshot; use logging to verify aircraft heading/speed present and the DBSCAN `eps`/min_samples, then tune params and re-run tests.

**Decisions & assumptions**
- Airway-following will be implemented in `MockConnector` for the scenario builder; live BlueSky integration is out-of-scope for this plan and would require connector/simulator support.
- Backwards compatibility: scenario create endpoint should accept optional `route` metadata; if absent, fallback to current random/spawn behaviour.
- Canvas DPI fix will be implemented in frontend only and should be performant if resizing logic runs only when CSS dimensions change.

**Further considerations**
1. Label decluttering: optionally implement a simple screen-space collision check to avoid overlapping labels for dense traffic. Recommendation: start with toggles and sizing, then add decluttering only if needed.
2. Persist UI toggles in `localStorage` so operator preferences survive reloads.
3. Accessibility: ensure new controls are keyboard-focusable and panels are collapsible for narrow screens.


## Implementation notes for Claude (hand-off)

Purpose: give Claude an exact, actionable checklist and file-level instructions to implement the remaining UI changes so the HMI matches the ASTRA reference screenshot.

Summary of what we've done (so Claude doesn't redo work):
- Implemented DPR-aware canvas resizing and context scaling (`ensureCanvasSize`) so canvas text and shapes render sharply on high-DPI displays. See `astra/dashboard/dashboard.js` (functions: `ensureCanvasSize`, updated `renderMap`, `renderTrafficOverlay`).
- Removed radar concentric rings and crosshair from the map (`drawGrid()` in `astra/dashboard/dashboard.js`).
- Added an aircraft-label toggle UI and runtime flag: `ui.showAircraftLabels` persisted in `localStorage`. Toggle injected into the existing `map-layer-toggles` control area; label drawing in `drawAircraftMarker()` respects the flag.

Priority next tasks (implement these in order):

1) Fix Alerts Panel Overflow (high priority) -- **DONE**
- `index.html`'s `#tracks-table` is now wrapped in `<div class="table-scroll">`
  (`max-height: 360px; overflow-y: auto`). The table itself is
  `table-layout: fixed` with explicit per-column `%` widths; every column
  keeps `white-space: nowrap` + ellipsis except `Flights` (5th column),
  which wraps instead of pushing the row wider than the panel.

2) Aircraft Side Panel (ARHAC/aircraft list) UI -- **DONE**
- New `#panel-aircraft` section (below `#panel-alerts`, same grid column)
  lists every `cycle.snapshot.aircraft`, sorted by callsign, with an
  urgency-colour badge (reused from `ui.aircraftHighlight` /
  `buildAircraftHighlightMap`), FL, ground speed, and heading. Implemented
  as `renderAircraftPanel(cycle)` in `dashboard.js`, called from `render()`
  right after `renderMap()` (so `ui.aircraftHighlight` is already current
  for this cycle). Clicking a row calls `panMapTo(lat, lon)`, which
  re-centres `ui.view` on that aircraft at the current zoom span and
  persists it.

3) Spawn aircraft along airways (server + mock) -- **DONE**
- `GET /scenario/airways` (new, in `scenario_routes.py`) reads and
  reshapes `astra/dashboard/geo/airways.json` (cached in-process after
  first read) into `[{designator, waypoint_names, coordinates:[{lat,lon}]}]`.
- `POST /scenario/aircraft` now accepts an optional `airway_designator`
  (+ optional `start_index`, default 0) instead of `lat`/`lon`/`heading_deg`;
  when given, the server looks up the airway, spawns at
  `coordinates[start_index]`, and passes the remaining points as
  `route_waypoints` to `StateReader.create_aircraft()` -> `MockConnector`.
  Free-standing spawns (no `airway_designator`) are unchanged --
  `lat`/`lon`/`heading_deg` are still required in that case.
- `MockConnector._AircraftRecord` gained `route_waypoints` / `route_index`.
  `_advance_along_route()` (called from `_propagate_positions()` when a
  record has a route) walks the remaining leg distance each tick, can
  consume multiple short legs in one oversized tick (capped at
  `_MAX_LEGS_PER_TICK = 50`), and clears the route once the final waypoint
  is passed -- the aircraft then reverts to plain straight-line dead
  reckoning on its last heading (flies "off the end" of the airway rather
  than stopping). Initial heading on spawn is the bearing to the first
  waypoint, overriding any given `heading_deg`.
- `scenario_builder.html`/`.js` gained an "Airway" `<select>` in the
  spawn modal (`loadAirways()` populates it from `/scenario/airways`);
  picking one hides the lat/lon/heading fields and sends
  `airway_designator` instead. Only offered for new spawns, not edits
  (`PATCH` has no route-reassignment support).
- Regression tests: `tests/test_interface.py` (new, 18 checks) -- no-route
  baseline unchanged, waypoint-by-waypoint following, route clears +
  continues straight past the last waypoint, a large-`dt` multi-leg tick,
  and the two new endpoints (including the unknown-airway 404 case).

4) Label decluttering & font tuning (polish) -- **DONE**
- `geo_layers.js` had two copies (`astra/dashboard/geo_layers.js`, the one
  Flask actually serves per `index.html`'s `url_for('static', ...)`, and
  a stale duplicate at `astra/dashboard/geo/geo_layers.js` that had the
  label-declutter/diamond-marker work but was never wired in). Consolidated
  onto the served path; the stray duplicate is deleted.
- Base body font 13px -> 14px monospace, `-webkit-font-smoothing:
  antialiased`; smallest UI labels 10px -> 11px; canvas `ctx.font` sizes
  bumped ~1-2px across `dashboard.js` and `geo_layers.js` (aircraft labels
  are now 12px, sector/waypoint labels 10-11px).

5) Known limitation: predicted-only hotspots are dropped (was "diagnostic", now root-caused)
- **This is very likely what "aircraft conflicting but no hotspot detected"
  actually is**, now confirmed end-to-end (not a DBSCAN/CPA parameter bug --
  those checked out fine, see below).
- Root cause: `astra/tracking/engine.py`'s `TrackerEngine.update()` only
  ever reads `regions_by_horizon.get(0, [])` (`_IDENTITY_HORIZON_MIN = 0`,
  by design -- see the comment at the top of that file and the
  "Only horizon 0..." row in `docs/Developer_Handover.md`'s Known
  limitations table, now corrected to describe this accurately). Every
  other horizon (5/10/15/30/60 min) is fully computed each cycle by
  `Pipeline._build_regions_by_horizon()` and handed to
  `TrackerEngine.update()`, but silently ignored for opening or matching
  tracks. `ForecastEngine` (Milestone 6) only *forecasts onset/peak/
  dissipation for tracks that already opened from a horizon-0 cluster* --
  it never opens a new one from a predicted horizon, and explicitly
  excludes `CANDIDATE` tracks (`_FORECASTABLE_STATUSES` in
  `astra/forecast/engine.py`).
- Practical effect: two aircraft that are *not yet* within 15 NM / 1000 ft
  of each other right now, but converging fast enough that they will
  breach separation within the 60-minute prediction window, generate a
  perfectly good `ComplexityRegion` at some future horizon -- and then it's
  thrown away. This is exactly the kind of pair the new airway-spawn
  feature (item 3) makes easy to create (two aircraft placed on airways
  that cross downstream).
- Confirmed reproduction (run as-is, no BlueSky needed):
  ```python
  from astra.interface.traffic_state import AircraftState, TrafficSnapshot
  from astra.pipeline import Pipeline
  from astra.utils.config import ASTRAConfig

  def ac(callsign, lat, lon, alt, hdg, gs):
      return AircraftState(callsign, lat, lon, alt, gs, hdg, 0.0, "A320", 0.0)

  pipeline = Pipeline(ASTRAConfig())
  snap = TrafficSnapshot(timestamp_s=0.0, aircraft={
      # ~40 NM apart, closing at 240 kt each -> will meet at ~horizon=5min.
      "AC5": ac("AC5", 47.00, 7.50, 35000.0, 90.0, 240.0),
      "AC6": ac("AC6", 47.00, 8.17, 35000.0, 270.0, 240.0),
  })
  result = pipeline.run_cycle(snap)
  print("tracks opened:", len(result.tracks))          # -> 0
  print(result.regions_by_horizon[5])                  # -> 1 region, complexity=27.2, real cluster
  ```
  Case A (aircraft already <15 NM apart *right now*) works correctly and
  opens a `CANDIDATE` track immediately -- confirmed separately, so the
  DBSCAN `eps`/`min_samples` (`hotspot/engine.py`, `hotspot/distance.py`)
  and MTCA/LTCA thresholds (`complexity/conflict.py`) are NOT the bug;
  don't spend time retuning them.
- Not attempted in this pass: a correct fix means letting `TrackerEngine`
  open/extend candidate tracks from predicted horizons too (roughly: when
  horizon 0 has no match for a track, fall back to the nearest non-empty
  predicted horizon before giving up), which needs new dedup logic so one
  real encounter doesn't spawn a separate track per horizon per cycle, and
  a decision on whether `ForecastEngine` should still exclude
  horizon-only-detected `CANDIDATE` tracks. This touches identity
  semantics that `tests/test_tracking.py` (327 lines) and
  `tests/test_forecast.py` (390 lines) currently assert against -- treat
  as its own scoped task, not a quick patch. A safer, smaller first step
  worth considering: surface predicted-horizon conflicts to the dashboard
  as a separate "predicted conflicts, not yet a hotspot" list (new API
  field, no `TrackerEngine` changes) rather than folding them into
  `FourDArhac` tracks at all.

6) Styling & pixel parity with ASTRA screenshot (iterative -- partially done)
- Files: `astra/dashboard/dashboard.css`, `astra/dashboard/index.html`, optionally new fonts under `static/fonts/`
- Done: body font bumped to 14px monospace with antialiasing (see item 4);
  `#tracks-tbody tr.selected` changed from a 10%-opacity teal wash to a
  35%-opacity `--blue` fill, closer to the reference screenshot's solid
  blue selected-row highlight.
- Still open -- guidance for the next pass:
  - Space panels to match screenshot: increase `panel-hint` opacity, tighten panel paddings, and add subtle 1px shadow on raised panels if desired.
  - Match the ASTRA colour palette more closely: tune `--accent`, `--amber`, `--red`, `--blue` variables in `dashboard.css` (current `--accent` is a mint/teal `#35c3a3`; reference leans more blue/magenta).
  - Consider swapping the map/alerts column order (reference puts the big traffic map on the right, ours has it on the left) -- a bigger layout change, do only if there's time left after the above.

Acceptance tests & QA checklist for Claude
- Functional:
  - Toggle `Aircraft labels` shows/hides boxed labels instantly.
  - Alerts panel scroll prevents overflow and preserves column layout.
  - Aircraft spawned via scenario builder follow airway geometry (mock).
  - DPR text is sharp on high-DPI displays (simulate DPR in browser devtools).
- Visual:
  - Fonts, colors, panel spacing closely match the supplied ASTRA screenshot.
  - No radar rings visible.
- Robustness:
  - Backwards compatibility for scenario create API preserved.
  - No console errors in browser devtools during normal operation.

Development notes & constraints
- Live BlueSky integration for airway-following is out-of-scope; implement airway-following in `MockConnector` only unless the BlueSky connector is known to support route injection.
- Avoid redrawing static map layers every animation frame; only the traffic overlay should animate.
- When changing `tbody.innerHTML` ensure event handlers are reattached (current code already reattaches per poll).

TODO list (current)
- Implement DPR canvas scaling and sharp fonts (DONE)
- Remove radar concentric rings (DONE)
- Add aircraft label toggle and respect in rendering (DONE)
- Fix alerts panel overflow with scroll wrapper (DONE)
- Add airway spawn support (server + mock connector) (DONE)
- Add aircraft side panel UI (DONE)
- Sector toggle: already existed pre-plan via `astra/dashboard/geo/manifest.json`'s
  layer list + `#map-layer-toggles` -- no work needed, just confirmed working.
- Root-cause the "conflict but no hotspot" report (DONE -- see item 5 above;
  the fix itself is deferred, scoped for a follow-up session)
- Remaining: item 6 styling/pixel-parity polish (open-ended, iterate as time allows)
- Remaining: `tests/test_dashboard.py`-style Flask test-client coverage for
  `scenario_routes.py`'s non-airway endpoints (`/scenario/scenarios*`,
  `/scenario/presets*`, `/scenario/control`) is still a pre-existing gap
  (not introduced by this pass) -- `tests/test_interface.py` only covers
  the new airway-related endpoints.


---

## Status as of this session (implementation pass following the hand-off above)

All six "priority next tasks" items above were addressed: items 1-4 and 6
(partially) implemented and verified; item 5 was root-caused with a
runnable reproduction but the actual fix deferred as its own scoped task
(see item 5's "Not attempted in this pass" note) -- that's the main thing
to hand to the next session, along with finishing item 6's remaining
polish.

Full regression suite after this pass, all green:
```
tests/test_hotspot.py      24/24
tests/test_complexity.py   42/42
tests/test_forecast.py     47/47
tests/test_resolution.py   39/39
tests/test_tracking.py     44/44
tests/test_dashboard.py    81/81
tests/test_interface.py    18/18  (new -- airway spawn/follow)
```

Also fixed in passing (not in the original numbered list): consolidated
two divergent copies of `geo_layers.js` (see item 4) -- if you go looking
for `astra/dashboard/geo/geo_layers.js`, it no longer exists; the served
one is `astra/dashboard/geo_layers.js`.
