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

1) Fix Alerts Panel Overflow (high priority)
- Files: `astra/dashboard/index.html`, `astra/dashboard/dashboard.css`, `astra/dashboard/dashboard.js` (optional)
- Steps for Claude:
  1. In `index.html`, wrap the alerts table in a scroll container: add `<div class="table-scroll">` around the `<table id="tracks-table">` and close the div after the table.
  2. In `dashboard.css`, add rules:
     - `.table-scroll { max-height: 360px; overflow-y: auto; }`
     - Replace or relax `th, td { white-space: nowrap; }` with a scoped rule targeting only narrow columns (e.g., keep `ARHAC`/`Status` nowrap, make `Flights` allow wrapping). Use `text-overflow: ellipsis` + `overflow:hidden` for columns that must not expand.
  3. Ensure `renderTracksTable()` still works after DOM changes (it replaces `tbody.innerHTML`) and event listeners are attached correctly.
- Tests: populate many tracks and verify no overflow beyond panel; table scrolls independently and column widths remain stable.

2) Aircraft Side Panel (ARHAC/aircraft list) UI
- Files: `astra/dashboard/index.html`, `astra/dashboard/dashboard.css`, `astra/dashboard/dashboard.js`
- Steps:
  1. Add HTML placeholder in `index.html` (e.g., new `<section id="panel-aircraft" class="panel panel-aircraft">` in the operations layout area; update grid-template-areas if needed or collapse into existing layout).
  2. In `dashboard.css` create styles for `.panel-aircraft`, `.aircraft-row` with `max-height` and `overflow-y:auto` to hold many items.
  3. Implement `renderAircraftPanel(cycle)` in `dashboard.js`:
     - Pull `cycle.snapshot.aircraft` and `ui.aircraftHighlight`.
     - Render rows with callsign, FL, GS, heading, and a small urgency colour badge (reuse `urgencyColor`/`urgencyBucket`).
     - Add click handler to focus/select aircraft (set `ui.selectedArhacId` or pan to aircraft by updating `ui.view`).
  4. Call `renderAircraftPanel(cycle)` from the main `render(cycle)` flow.
- Tests: click rows pan/center map or highlight aircraft; panel scrolls for many aircraft.

3) Spawn aircraft along airways (server + mock)
- Files to change:
  - Frontend: `astra/dashboard/scenario_builder.js` — add an airway dropdown, fetch `/scenario/airways`.
  - Server: `astra/dashboard/scenario_routes.py` — add `GET /scenario/airways` to return `static/geo/airways.json` features; extend create endpoint to accept `route` or `airway_designator` and forward to `StateReader.create_aircraft()`.
  - Connector: `astra/interface/mock_connector.py` — extend `_AircraftRecord` to include `route_waypoints` and implement waypoint-following in `_propagate_positions()` by moving toward current leg and advancing when close.
  - Optionally: `astra/interface/state_reader.py` signature to accept route metadata and pass to connector.
- Steps for Claude:
  1. Add `/scenario/airways` route that reads the static `geo/airways.json` (use Flask `send_from_directory` or `pkg_resources`) and returns a lightweight list of airway names and simplified coordinate arrays.
  2. Modify `scenario_builder.js` to fetch the airway list on load, populate a `<select>` and include the selected airway's coordinates in spawn payload to POST `/scenario/aircraft`.
  3. Extend `MockConnector.create_aircraft()` to accept `route_waypoints` (list of `{lat, lon}`) and store in the record; change propagation to compute heading to next waypoint, set `record.heading_deg`, and move along the path by distance = speed * dt.
  4. Keep backward compatibility: if `route_waypoints` absent, revert to existing spawn behaviour.
- Tests: spawn a mini-route with 3 points and run the mock; verify aircraft follow airway line approx and arrive at final waypoint.

4) Label decluttering & font tuning (polish)
- Files: `astra/dashboard/geo_layers.js`, `astra/dashboard/dashboard.css`, `astra/dashboard/dashboard.js`
- Steps:
  1. Improve canvas font crispness by ensuring all `ctx.font` usages use CSS-pixel sizes (DPR scaling already applied). Increase fonts slightly to match ASTRA screenshot (e.g., base 12-13px on UI, 11-12px for canvas labels).
  2. Implement a simple label declutter in `drawAircraftMarker()` or central label manager: maintain an array of placed label bounding boxes (screen-space) and skip drawing any label that intersects an existing box.
  3. Consider adjusting label box background alpha and stroke thickness for readability.
- Tests: Toggle labels on/off; add high-density test case to verify decluttering works without hiding important labels.

5) Conflict/Hotspot bug investigation (diagnostic)
- Files: `astra/hotspot/distance.py`, `astra/hotspot/engine.py`, `astra/complexity/conflict.py`, `astra/complexity/engine.py`
- Steps for Claude:
  1. Instrument (temporarily) the pipeline to log a failing snapshot: print aircraft involved, their headings, speeds, and the computed pairwise CPA values.
  2. Run unit tests `tests/test_hotspot.py` and `tests/test_complexity.py` to reproduce failure locally.
  3. If hotspots exist but pairwise conflicts are zero: check that aircraft have non-null `heading_deg` and `ground_speed_kt` in snapshot; missing kinematic data will make CPA detect no conflict.
  4. Tune DBSCAN `eps` or `min_samples` in `hotspot/engine.py` and the vertical/horizontal gating in `hotspot/distance.py` if clusters are too small/large.
  5. Re-run tests and add a regression test fixture capturing the failing case.

6) Styling & pixel parity with ASTRA screenshot (iterative)
- Files: `astra/dashboard/dashboard.css`, `astra/dashboard/index.html`, optionally new fonts under `static/fonts/`
- Guidance for Claude:
  - Use `Consolas` or `Menlo` for monospaced HUD text; adjust `font-size` on `body` to 14px for closer visual weight; use `-webkit-font-smoothing: antialiased` in body to help on macOS.
  - Space panels to match screenshot: increase `panel-hint` opacity, tighten panel paddings, and add subtle 1px shadow on raised panels if desired.
  - Match the ASTRA colour palette: tune `--accent`, `--amber`, `--red`, `--blue` variables in `dashboard.css`.

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
- Add aircraft label toggle and respect in rendering (IN-PROGRESS)
- Fix alerts panel overflow with scroll wrapper
- Add airway spawn support (server + mock connector)
- Add aircraft side panel UI


---

If you'd like, I can now:
- produce the exact diff patches for the next task (alerts table scroll wrapper), or
- produce the server API spec + example payloads for airway spawn for Claude to implement.

Tell me which you prefer and I'll create the precise patch or API spec files.
