/**
 * ASTRA dashboard frontend (Milestone 9).
 *
 * Polls `/state` on `window.ASTRA_POLL_INTERVAL_S` and renders a modern,
 * ASTRA-inspired operational HMI: traffic/prediction map with a
 * time-horizon scrubber, an alerts table (onset/act-by/confidence),
 * an Event & Dissipation panel (complexity reduction, ranked
 * candidates with a client-side solution lifecycle, before/after
 * component bars, what-if profiles), the onset/peak/dissipation
 * timeline, a coordination-steps disclosure, and a sector-complexity
 * page. Nothing here computes a prediction, cluster, complexity
 * score, track, or candidate -- it only draws what the backend
 * already serialized.
 */

(function () {
    "use strict";

    const POLL_INTERVAL_MS = Math.max(250, window.ASTRA_POLL_INTERVAL_S * 1000);

    // Canvas drawing can't read CSS custom properties directly, so these
    // mirror :root's --aircraft-pink / --solution-magenta / --amber in
    // dashboard.css. Keep the two in sync if either changes.
    const AIRCRAFT_COLOR = "#ff3d9a";
    const SOLUTION_COLOR = "#ff2fd6";
    const PREDICTED_PATH_COLOR = "#ffbf69";

    // Session-only UI state. Never sent to the backend; resets on reload.
    const ui = {
        selectedArhacId: null,
        selectedCandidateIndex: {}, // arhac_id -> candidate index being previewed
        selectedAircraftCallsign: null, // set by clicking an aircraft marker on the map
        lifecycle: {}, // arhac_id -> "DRAFT" | "PROPOSED" | "ACKNOWLEDGED" | "CANCELED"
        selectedHorizon: 0,
        availableHorizons: [0],
        // Map-animation state (see interpolatedObservedAircraft/renderTrafficOverlay):
        mapProject: null, // cached projector from the last static renderMap() pass
        aircraftHighlight: {}, // callsign -> {color, bucket}, from buildAircraftHighlightMap()
        prevSnapshotAircraft: null, // observed aircraft list from the *previous* poll
        prevCycleAtMs: 0,
        curCycleAtMs: 0,
        // Map view state (see "Map view state" section below): {minLat,
        // maxLat, minLon, maxLon} or null until first initialized (from
        // localStorage or a fit-to-FIR/traffic computation).
        view: null,
        showAircraftLabels: true,
        showPredictedPaths: true,
        // Sector polygon features (geo/sectors.json) hidden individually,
        // by their shared "name" property (e.g. "Sector 2 Ho Chi Minh
        // ACC") -- independent of the whole-layer "Sectors" toggle above.
        hiddenSectorNames: new Set(),
    };

    const LIFECYCLE_STAGES = ["DRAFT", "PROPOSED", "ACKNOWLEDGED"];

    // Shared geo-overlay layer manager (FIRs/sectors/airways/waypoints/
    // airports/coastlines) -- see geo_layers.js. Own module, own data
    // files; this dashboard only owns *when* to draw it (in renderMap)
    // and the toggle checkboxes' UI chrome.
    const geoLayers = new GeoLayerManager(window.ASTRA_GEO_MANIFEST_URL);

    function setupGeoLayerToggles() {
        const container = document.getElementById("map-layer-toggles");
        if (!container) {
            return;
        }
        container.innerHTML = geoLayers
            .getToggleList()
            .map(
                (l) => `
            <label class="layer-toggle">
                <input type="checkbox" data-layer-id="${l.id}" ${l.visible ? "checked" : ""}>
                ${l.label}
            </label>`
            )
            .join("");
        container.querySelectorAll("input[data-layer-id]").forEach((input) => {
            input.addEventListener("change", () => {
                geoLayers.setVisible(input.dataset.layerId, input.checked);
                savePersistedLayerVisibility();
                if (window.__astraLastCycle) {
                    renderMap(window.__astraLastCycle);
                }
            });
        });

        // Add an aircraft label toggle to the same control area
        const labelToggle = document.createElement("label");
        labelToggle.className = "layer-toggle";
        labelToggle.innerHTML = `
            <input type="checkbox" id="toggle-aircraft-labels" ${ui.showAircraftLabels ? "checked" : ""}>
            Aircraft labels
        `;
        container.appendChild(labelToggle);
        const lbl = document.getElementById("toggle-aircraft-labels");
        if (lbl) {
            lbl.addEventListener("change", () => {
                ui.showAircraftLabels = lbl.checked;
                try {
                    localStorage.setItem("astra_show_aircraft_labels_v1", JSON.stringify(ui.showAircraftLabels));
                } catch (e) {}
                if (window.__astraLastCycle) {
                    renderMap(window.__astraLastCycle);
                    renderTrafficOverlay();
                }
            });
        }

        // Predicted-path lines (amber dead-reckoning/route-aware + magenta
        // resolution solution) toggle -- off by default clutter reduction
        // for busy scenes; the aircraft markers themselves are unaffected.
        const pathToggle = document.createElement("label");
        pathToggle.className = "layer-toggle";
        pathToggle.innerHTML = `
            <input type="checkbox" id="toggle-predicted-paths" ${ui.showPredictedPaths ? "checked" : ""}>
            Predicted paths
        `;
        container.appendChild(pathToggle);
        const pathsInput = document.getElementById("toggle-predicted-paths");
        if (pathsInput) {
            pathsInput.addEventListener("change", () => {
                ui.showPredictedPaths = pathsInput.checked;
                try {
                    localStorage.setItem("astra_show_predicted_paths_v1", JSON.stringify(ui.showPredictedPaths));
                } catch (e) {}
                if (window.__astraLastCycle) {
                    renderMap(window.__astraLastCycle);
                }
            });
        }

        renderSectorToggleRow();
    }

    /** One chip per named sector (e.g. "1", "2", "5"), independent of the
     * whole-layer "Sectors" checkbox above -- lets an operator isolate a
     * couple of sectors (e.g. "just 1 and 2") without hiding the rest of
     * the geo overlay. No-op (renders nothing) until sectors.json has
     * data, so it never shows an empty row. */
    function renderSectorToggleRow() {
        const row = document.getElementById("map-sector-toggles");
        if (!row) {
            return;
        }
        const names = distinctSectorNames();
        if (names.length === 0) {
            row.innerHTML = "";
            return;
        }
        row.innerHTML =
            '<span class="sector-toggle-label">Sectors shown:</span>' +
            names
                .map((name) => {
                    const short = (name.match(/\d+/) || [name])[0];
                    const active = !ui.hiddenSectorNames.has(name);
                    return `<button class="sector-chip ${active ? "active" : ""}" data-sector-name="${name}" title="${name}">${short}</button>`;
                })
                .join("");
        row.querySelectorAll(".sector-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const name = chip.dataset.sectorName;
                if (ui.hiddenSectorNames.has(name)) {
                    ui.hiddenSectorNames.delete(name);
                } else {
                    ui.hiddenSectorNames.add(name);
                }
                savePersistedHiddenSectors();
                renderSectorToggleRow();
                if (window.__astraLastCycle) {
                    renderMap(window.__astraLastCycle);
                }
            });
        });
    }

    function loadPersistedUiPrefs() {
        try {
            const raw = localStorage.getItem("astra_show_aircraft_labels_v1");
            if (raw !== null) {
                ui.showAircraftLabels = JSON.parse(raw);
            }
            const pathsRaw = localStorage.getItem("astra_show_predicted_paths_v1");
            if (pathsRaw !== null) {
                ui.showPredictedPaths = JSON.parse(pathsRaw);
            }
        } catch (e) {
            // ignore
        }
        ui.hiddenSectorNames = loadPersistedHiddenSectors();
    }

    // ------------------------------------------------------------------
    // Small shared helpers
    // ------------------------------------------------------------------

    /** Ensure a canvas backing store is sized for the current devicePixelRatio
     * and scale the context so drawing coordinates are in CSS pixels. */
    function ensureCanvasSize(canvas) {
        const dpr = window.devicePixelRatio || 1;
        const cssW = Math.max(1, Math.round(canvas.clientWidth));
        const cssH = Math.max(1, Math.round(canvas.clientHeight));
        const backW = Math.round(cssW * dpr);
        const backH = Math.round(cssH * dpr);
        if (canvas.width !== backW || canvas.height !== backH) {
            canvas.width = backW;
            canvas.height = backH;
            const ctx = canvas.getContext("2d");
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
    }

    /** Map a 0-100 complexity score to a colour on a green->amber->red scale. */
    function complexityColor(score) {
        const clamped = Math.max(0, Math.min(100, score));
        if (clamped < 50) {
            const t = clamped / 50;
            return lerpColor([53, 195, 163], [224, 166, 60], t); // accent -> amber
        }
        const t = (clamped - 50) / 50;
        return lerpColor([224, 166, 60], [224, 85, 60], t); // amber -> red
    }

    /** Map a 0-1 confidence to a colour on a red->amber->green scale (inverse of above). */
    function confidenceColor(value) {
        return complexityColor(100 - Math.max(0, Math.min(1, value)) * 100);
    }

    function lerpColor(a, b, t) {
        const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
        return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }

    /** Apply an alpha to a color regardless of whether it's `#rrggbb` (as
     * `urgencyColor()` returns) or `rgb(r, g, b)` (as `lerpColor()`/
     * `complexityColor()` returns) -- always yields `rgba(r, g, b, alpha)`.
     * The previous string-replace approach (`.replace("rgb","rgba")...`)
     * silently did nothing on hex input, since neither "rgb" nor ")"
     * appear in "#e0553c" -- so any region whose ring color came from
     * `urgencyColor()` (i.e. any track with onset urgency, not just the
     * plain complexity-score gradient) rendered fully opaque instead of
     * as a soft glow, however low the alpha argument was. That was the
     * actual cause of hotspots still looking like solid filled discs.
     */
    function withAlpha(color, alpha) {
        if (color.startsWith("#")) {
            const hex = color.slice(1);
            const full = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex;
            const r = parseInt(full.slice(0, 2), 16);
            const g = parseInt(full.slice(2, 4), 16);
            const b = parseInt(full.slice(4, 6), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }
        const match = color.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        if (match) {
            return `rgba(${match[1]}, ${match[2]}, ${match[3]}, ${alpha})`;
        }
        return color; // unrecognized format -- draw opaque rather than throw
    }

    function fmt(value, digits) {
        if (value === null || value === undefined) {
            return "-";
        }
        return Number(value).toFixed(digits !== undefined ? digits : 1);
    }

    /** Format a sim-clock second count as HH:MM:SS. */
    function clockFmt(seconds) {
        if (seconds === null || seconds === undefined) {
            return "-";
        }
        const total = Math.max(0, Math.round(seconds));
        const h = String(Math.floor(total / 3600)).padStart(2, "0");
        const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
        const s = String(total % 60).padStart(2, "0");
        return `${h}:${m}:${s}`;
    }

    /** Format a horizon (minutes) label for the scrubber/table. */
    function horizonLabel(horizonMin) {
        return horizonMin === 0 ? "observed" : `+${horizonMin} min`;
    }

    function statusPill(status) {
        return `<span class="status-pill status-${status}">${status}</span>`;
    }

    /** Rough planar distance in NM between two lat/lon points (display use only). */
    function roughDistanceNm(lat1, lon1, lat2, lon2) {
        const dLat = (lat2 - lat1) * 60; // 1 deg lat ~= 60 NM
        const dLon = (lon2 - lon1) * 60 * Math.cos((lat1 * Math.PI) / 180);
        return Math.sqrt(dLat * dLat + dLon * dLon);
    }

    /** Format a countdown (seconds) as a live-ticking mm:ss, or "-" if unknown/past. */
    function countdownFmt(seconds) {
        if (seconds === null || seconds === undefined || seconds < 0) {
            return "-";
        }
        const total = Math.round(seconds);
        const m = String(Math.floor(total / 60)).padStart(2, "0");
        const s = String(total % 60).padStart(2, "0");
        return `${m}:${s}`;
    }

    /** Bucket a track's onset-in-seconds into an urgency tier, shared by the
     * alerts table's `onsetClass()` row styling and the map's hotspot-ring /
     * aircraft-highlight styling -- one urgency definition, several renderers. */
    function urgencyBucket(onsetInS) {
        if (onsetInS === null || onsetInS === undefined) {
            return "none";
        }
        if (onsetInS <= 300) {
            return "soon";
        }
        if (onsetInS <= 900) {
            return "near";
        }
        return "far";
    }

    /** Urgency tier -> a CSS colour, shared by hotspot rings and aircraft highlight labels. */
    function urgencyColor(bucket) {
        switch (bucket) {
            case "soon":
                return "#e0553c"; // --red
            case "near":
                return "#e0a63c"; // --amber
            case "far":
                return "#4a90a4"; // --blue
            default:
                return "#8494a2"; // neutral, no active track
        }
    }

    /** Find the open track (if any) whose centroid is closest to a given
     * lat/lon, for tying a map element (hotspot ring, aircraft) back to the
     * track whose forecast should drive its urgency styling. `maxNm` bounds
     * the match so distant tracks never "claim" an unrelated element. */
    function nearestTrack(lat, lon, tracks, maxNm) {
        let best = null;
        let bestDist = maxNm;
        tracks.forEach((t) => {
            if (t.status === "CLOSED" || !t.centroid) {
                return;
            }
            const d = roughDistanceNm(lat, lon, t.centroid.lat, t.centroid.lon);
            if (d < bestDist) {
                bestDist = d;
                best = t;
            }
        });
        return best;
    }

    /** `{callsign: {color, bucket}}` for every aircraft belonging to an open
     * track, for the map's aircraft-marker highlight colour. Built once per
     * poll cycle and reused every animation frame (cheap dict lookup) rather
     * than recomputed per marker per frame. */
    function buildAircraftHighlightMap(cycle) {
        const nowS = cycle.snapshot.timestamp_s;
        const map = {};
        cycle.tracks.forEach((t) => {
            if (t.status === "CLOSED") {
                return;
            }
            const onsetInS = t.predicted_onset_s === null ? null : t.predicted_onset_s - nowS;
            const bucket = urgencyBucket(onsetInS);
            const color = urgencyColor(bucket);
            t.member_aircraft.forEach((callsign) => {
                map[callsign] = { color, bucket };
            });
        });
        return map;
    }

    // ------------------------------------------------------------------
    // Tabs
    // ------------------------------------------------------------------

    function setupTabs() {
        document.querySelectorAll(".tab-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
                document.querySelectorAll(".tab-page").forEach((p) => p.classList.remove("active"));
                btn.classList.add("active");
                document.getElementById(btn.dataset.tab).classList.add("active");
            });
        });
    }

    // ------------------------------------------------------------------
    // Header
    // ------------------------------------------------------------------

    function renderHeader(payload) {
        const badge = document.getElementById("status-badge");
        const timeEl = document.getElementById("status-time");
        const cycleEl = document.getElementById("status-cycle");

        if (payload.has_data) {
            badge.textContent = "LIVE";
            badge.className = "badge badge-live";
            timeEl.textContent = "t = " + clockFmt(payload.updated_at_s);
        } else {
            badge.textContent = "WAITING";
            badge.className = "badge badge-waiting";
            timeEl.textContent = "t = \u2013";
        }
        cycleEl.textContent = "cycle " + payload.cycle_count;
    }

    // ------------------------------------------------------------------
    // Horizon scrubber (button group -- one click per horizon)
    // ------------------------------------------------------------------

    //: Curated set of horizons shown as buttons. `0` ("Now") is always
    //: shown first if available; the rest are intersected with whatever
    //: horizons the backend actually computed this cycle
    //: (`ASTRAConfig.prediction_horizons_min`), so this degrades
    //: gracefully if that list ever changes server-side.
    const HORIZON_BUTTON_MINUTES = [10, 20, 30, 40, 50, 60];

    function selectHorizon(horizonMin) {
        ui.selectedHorizon = horizonMin;
        document.querySelectorAll("#horizon-buttons .horizon-btn").forEach((btn) => {
            btn.classList.toggle("active", Number(btn.dataset.horizon) === horizonMin);
        });
        if (window.__astraLastCycle) {
            renderMap(window.__astraLastCycle);
        }
    }

    function setupHorizonScrubber() {
        const container = document.getElementById("horizon-buttons");
        container.addEventListener("click", (evt) => {
            const btn = evt.target.closest(".horizon-btn");
            if (!btn || btn.disabled) {
                return;
            }
            selectHorizon(Number(btn.dataset.horizon));
        });
    }

    function syncHorizonScrubber(cycle) {
        const horizons = Object.keys(cycle.regions_by_horizon)
            .map(Number)
            .sort((a, b) => a - b);
        ui.availableHorizons = horizons.length > 0 ? horizons : [0];
        const available = new Set(ui.availableHorizons);
        const wanted = [0, ...HORIZON_BUTTON_MINUTES];

        const container = document.getElementById("horizon-buttons");
        const alreadyBuilt = container.childElementCount === wanted.length;
        if (!alreadyBuilt) {
            container.innerHTML = wanted
                .map((h) => {
                    const label = h === 0 ? "Now" : `+${h}m`;
                    return `<button type="button" class="horizon-btn" data-horizon="${h}">${label}</button>`;
                })
                .join("");
        }
        container.querySelectorAll(".horizon-btn").forEach((btn) => {
            const h = Number(btn.dataset.horizon);
            btn.disabled = !available.has(h);
        });

        if (!available.has(ui.selectedHorizon)) {
            ui.selectedHorizon = ui.availableHorizons[0];
        }
        container.querySelectorAll(".horizon-btn").forEach((btn) => {
            btn.classList.toggle("active", Number(btn.dataset.horizon) === ui.selectedHorizon);
        });
    }

    // ------------------------------------------------------------------
    // Map panel (plan view: traffic at scrubbed horizon + full predicted paths)
    // ------------------------------------------------------------------

    /** Walk any GeoJSON geometry, calling `fn([lon, lat])` for every coordinate pair. */
    function forEachCoordinate(geometry, fn) {
        if (!geometry) {
            return;
        }
        switch (geometry.type) {
            case "Point":
                fn(geometry.coordinates);
                break;
            case "MultiPoint":
            case "LineString":
                geometry.coordinates.forEach(fn);
                break;
            case "Polygon":
            case "MultiLineString":
                geometry.coordinates.forEach((ring) => ring.forEach(fn));
                break;
            case "MultiPolygon":
                geometry.coordinates.forEach((poly) => poly.forEach((ring) => ring.forEach(fn)));
                break;
            default:
                break;
        }
    }

    // ------------------------------------------------------------------
    // Map view state: pan/zoom persistence + fit-to-FIR
    //
    // The map used to recompute its lat/lon bounds from scratch every
    // poll (computeBounds() below), which is fine for an auto-fit view
    // but incompatible with letting the operator pan/zoom -- any
    // interaction would just get wiped out on the next poll's redraw.
    // `ui.view` is now the single source of truth for what's on screen;
    // it's computed once (fit-to-FIR, or a restored localStorage value)
    // and only ever changed by an explicit pan/zoom/reset action, never
    // silently recomputed from traffic on a timer.
    // ------------------------------------------------------------------

    const VIEW_STORAGE_KEY = "astra_map_view_v1";
    const LAYER_VISIBILITY_STORAGE_KEY = "astra_map_layer_visibility_v1";
    const MIN_SPAN_DEG = 0.05;
    const MAX_SPAN_DEG = 220;

    function loadPersistedView() {
        try {
            const raw = localStorage.getItem(VIEW_STORAGE_KEY);
            if (!raw) {
                return null;
            }
            const v = JSON.parse(raw);
            if (
                typeof v.minLat === "number" &&
                typeof v.maxLat === "number" &&
                typeof v.minLon === "number" &&
                typeof v.maxLon === "number"
            ) {
                return v;
            }
        } catch (err) {
            // Corrupt/old-format value -- ignore and fall back to auto-fit.
        }
        return null;
    }

    function savePersistedView(view) {
        try {
            localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(view));
        } catch (err) {
            // Storage unavailable (private browsing, quota, ...) -- the map
            // still works, it just won't remember the view across reloads.
        }
    }

    function loadPersistedLayerVisibility() {
        try {
            const raw = localStorage.getItem(LAYER_VISIBILITY_STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (err) {
            return {};
        }
    }

    function savePersistedLayerVisibility() {
        try {
            const state = {};
            geoLayers.layers.forEach((l) => {
                state[l.id] = l.visible;
            });
            localStorage.setItem(LAYER_VISIBILITY_STORAGE_KEY, JSON.stringify(state));
        } catch (err) {
            // Non-fatal -- see savePersistedView.
        }
    }

    const HIDDEN_SECTORS_STORAGE_KEY = "astra_hidden_sectors_v1";

    function loadPersistedHiddenSectors() {
        try {
            const raw = localStorage.getItem(HIDDEN_SECTORS_STORAGE_KEY);
            return raw ? new Set(JSON.parse(raw)) : new Set();
        } catch (err) {
            return new Set();
        }
    }

    function savePersistedHiddenSectors() {
        try {
            localStorage.setItem(HIDDEN_SECTORS_STORAGE_KEY, JSON.stringify(Array.from(ui.hiddenSectorNames)));
        } catch (err) {
            // Non-fatal -- see savePersistedView.
        }
    }

    /** Distinct sector `name` values in the "sectors" geo layer, in file
     * order -- e.g. ["Sector 1 Ho Chi Minh ACC", "Sector 2 Ho Chi Minh ACC", ...].
     * A sector is often several polygons (one per altitude layer) sharing
     * one name; this groups them for a single "show/hide Sector N" toggle. */
    function distinctSectorNames() {
        const layer = geoLayers.layers.find((l) => l.id === "sectors");
        if (!layer) {
            return [];
        }
        const seen = new Set();
        const names = [];
        (layer.geojson.features || []).forEach((f) => {
            const name = f.properties && f.properties.name;
            if (name && !seen.has(name)) {
                seen.add(name);
                names.push(name);
            }
        });
        return names;
    }

    /** Bounding box (with the same padding convention as computeBounds) of
     * one geo layer's own geometry, or null if that layer has no features
     * yet (e.g. still an empty placeholder). */
    function geoLayerBounds(layerId, pad) {
        const layer = geoLayers.layers.find((l) => l.id === layerId);
        if (!layer) {
            return null;
        }
        const lats = [];
        const lons = [];
        (layer.geojson.features || []).forEach((feature) => {
            forEachCoordinate(feature.geometry, ([lon, lat]) => {
                lats.push(lat);
                lons.push(lon);
            });
        });
        if (lats.length === 0) {
            return null;
        }
        const p = pad === undefined ? 0.08 : pad;
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        const minLon = Math.min(...lons);
        const maxLon = Math.max(...lons);
        const latSpan = Math.max(maxLat - minLat, MIN_SPAN_DEG);
        const lonSpan = Math.max(maxLon - minLon, MIN_SPAN_DEG);
        return {
            minLat: minLat - latSpan * p,
            maxLat: maxLat + latSpan * p,
            minLon: minLon - lonSpan * p,
            maxLon: maxLon + lonSpan * p,
        };
    }

    /** The view "Reset" (double-click) and first-load fit to: the FIR
     * layer's own extent if it's loaded and populated, else falling back
     * to fitting the current traffic (computeBounds) -- so the map is
     * never a blank/degenerate view before real geometry arrives. */
    function fitToDataView(cycle) {
        return geoLayerBounds("firs") || computeBounds(cycle);
    }

    function makeUnprojector(bounds, width, height) {
        const latSpan = bounds.maxLat - bounds.minLat || 1;
        const lonSpan = bounds.maxLon - bounds.minLon || 1;
        return function unproject(x, y) {
            const lon = bounds.minLon + (x / width) * lonSpan;
            const lat = bounds.minLat + ((height - y) / height) * latSpan;
            return [lat, lon];
        };
    }

    function computeBounds(cycle) {
        const lats = [];
        const lons = [];
        cycle.snapshot.aircraft.forEach((ac) => {
            lats.push(ac.lat);
            lons.push(ac.lon);
        });
        Object.values(cycle.prediction.paths).forEach((points) => {
            points.forEach((p) => {
                lats.push(p.lat);
                lons.push(p.lon);
            });
        });
        Object.values(cycle.regions_by_horizon).forEach((regions) => {
            regions.forEach((region) => {
                lats.push(region.cluster.centroid_lat);
                lons.push(region.cluster.centroid_lon);
            });
        });
        Object.values(cycle.sector_regions || {}).forEach((region) => {
            lats.push(region.cluster.centroid_lat);
            lons.push(region.cluster.centroid_lon);
        });
        geoLayers.layers.forEach((layer) => {
            if (!layer.visible) {
                return;
            }
            (layer.geojson.features || []).forEach((feature) => {
                forEachCoordinate(feature.geometry, ([lon, lat]) => {
                    lats.push(lat);
                    lons.push(lon);
                });
            });
        });
        if (lats.length === 0) {
            return { minLat: -1, maxLat: 1, minLon: -1, maxLon: 1 };
        }
        const pad = 0.15;
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        const minLon = Math.min(...lons);
        const maxLon = Math.max(...lons);
        const latSpan = Math.max(maxLat - minLat, 0.05);
        const lonSpan = Math.max(maxLon - minLon, 0.05);
        return {
            minLat: minLat - latSpan * pad,
            maxLat: maxLat + latSpan * pad,
            minLon: minLon - lonSpan * pad,
            maxLon: maxLon + lonSpan * pad,
        };
    }

    function makeProjector(bounds, width, height) {
        const latSpan = bounds.maxLat - bounds.minLat || 1;
        const lonSpan = bounds.maxLon - bounds.minLon || 1;
        return function project(lat, lon) {
            const x = ((lon - bounds.minLon) / lonSpan) * width;
            const y = height - ((lat - bounds.minLat) / latSpan) * height;
            return [x, y];
        };
    }

    function drawGrid(ctx, width, height) {
        // Faint square reference grid (kept subtle -- the range rings below
        // are the primary "this is a radar" visual cue).
        ctx.strokeStyle = "#141c24";
        ctx.lineWidth = 1;
        for (let i = 1; i < 8; i++) {
            const x = (width / 8) * i;
            const y = (height / 8) * i;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.moveTo(0, y);
            ctx.lineTo(width, y);
            ctx.stroke();
        }

        // Radar-style concentric range rings removed for this HMI layout.
        // (Previously drawn here; removed per design decision.)
    }

    function drawSectorBoundaries(ctx, project, bounds, width, sectorRegions) {
        Object.entries(sectorRegions || {}).forEach(([name, region]) => {
            const [cx, cy] = project(region.cluster.centroid_lat, region.cluster.centroid_lon);
            const degPerNm = (bounds.maxLon - bounds.minLon) / 60;
            const radiusPx = Math.max(
                10,
                (region.cluster.horizontal_extent_nm * degPerNm * width) /
                    (bounds.maxLon - bounds.minLon || 1)
            );
            ctx.beginPath();
            ctx.setLineDash([5, 5]);
            ctx.strokeStyle = "#4a5866";
            ctx.lineWidth = 1;
            ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = "#8494a2";
            ctx.font = "12px monospace";
            ctx.fillText(name, cx - radiusPx + 4, cy - radiusPx + 12);
        });
    }

    /** Hotspot ring style now reflects onset urgency (reusing the same
     * `urgencyBucket`/`urgencyColor` the alerts table and aircraft
     * highlighting use), not just the current complexity score -- a
     * hotspot 2 minutes from onset should look more alarming on the map
     * than one 40 minutes out, even if their scores happen to match. */
    /** Convert a distance in NM to on-screen pixels at a given latitude,
     * consistent with `makeProjector`'s lon-based projection (NM per
     * degree of longitude shrinks with cos(latitude); NM per degree of
     * latitude is ~constant at 60). The previous formula here divided by
     * `bounds.maxLon - bounds.minLon` after multiplying by it, so it
     * always cancelled out to a fixed, zoom-independent pixel size --
     * hotspot rings stayed the same huge size on screen however far you
     * zoomed in, which is what made them look like they "filled the
     * whole screen" once a couple of aircraft were close together. */
    function nmToPixels(nm, centroidLatDeg, bounds, width) {
        const lonSpan = bounds.maxLon - bounds.minLon || 1;
        const nmPerDegLon = 60 * Math.cos((centroidLatDeg * Math.PI) / 180) || 60;
        const degLon = nm / nmPerDegLon;
        return (degLon / lonSpan) * width;
    }

    function drawComplexityRegions(ctx, project, bounds, width, regions, cycle) {
        const nowS = cycle.snapshot.timestamp_s;
        (regions || []).forEach((region) => {
            const [cx, cy] = project(region.cluster.centroid_lat, region.cluster.centroid_lon);
            const radiusPx = Math.max(
                18,
                nmToPixels(region.cluster.horizontal_extent_nm, region.cluster.centroid_lat, bounds, width)
            );
            const track = nearestTrack(
                region.cluster.centroid_lat,
                region.cluster.centroid_lon,
                cycle.tracks,
                region.cluster.horizontal_extent_nm * 2 + 5
            );
            const onsetInS = track && track.predicted_onset_s !== null ? track.predicted_onset_s - nowS : null;
            const bucket = urgencyBucket(onsetInS);
            const ringColor = bucket === "none" ? complexityColor(region.complexity_score) : urgencyColor(bucket);
            const dash = { soon: [], near: [6, 4], far: [3, 5], none: [3, 5] }[bucket];
            const lineWidth = bucket === "soon" ? 2.5 : 1.5;

            // Soft glow instead of a flat filled disc: a radial gradient
            // fading from a low peak opacity at the centroid to fully
            // transparent at the edge, so it reads as a hazy overlay
            // rather than an opaque wash that hides the traffic and map
            // underneath it. Extends slightly past the ring itself
            // (1.4x) so the fade-out is gradual, not a visible edge.
            const glowRadius = radiusPx * 1.4;
            const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowRadius);
            glow.addColorStop(0, withAlpha(ringColor, 0.12));
            glow.addColorStop(0.6, withAlpha(ringColor, 0.05));
            glow.addColorStop(1, withAlpha(ringColor, 0));
            ctx.beginPath();
            ctx.fillStyle = glow;
            ctx.arc(cx, cy, glowRadius, 0, Math.PI * 2);
            ctx.fill();

            ctx.beginPath();
            ctx.setLineDash(dash);
            ctx.strokeStyle = ringColor;
            ctx.globalAlpha = 0.7;
            ctx.lineWidth = lineWidth;
            ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
            ctx.stroke();
            ctx.globalAlpha = 1;
            ctx.setLineDash([]);
            // A soon-onset ring gets a second, slightly larger ring for a
            // "target lock" look -- a static stand-in for a pulse animation
            // that reads clearly even on a once-per-poll redraw.
            if (bucket === "soon") {
                ctx.beginPath();
                ctx.strokeStyle = withAlpha(ringColor, 0.4);
                ctx.lineWidth = 1;
                ctx.arc(cx, cy, radiusPx + 5, 0, Math.PI * 2);
                ctx.stroke();
            }
            if (track && track.forecast_urgency_rank) {
                ctx.beginPath();
                ctx.fillStyle = ringColor;
                ctx.arc(cx + radiusPx - 4, cy - radiusPx + 4, 8, 0, Math.PI * 2);
                ctx.fill();
                ctx.fillStyle = "#060a0f";
                ctx.font = "bold 11px monospace";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText(String(track.forecast_urgency_rank), cx + radiusPx - 4, cy - radiusPx + 5);
                ctx.textAlign = "left";
                ctx.textBaseline = "alphabetic";
            }
        });
    }

    /** The resolution candidate currently being previewed in the Event
     * panel (whichever track is focused there, whichever chip is active),
     * or null if no track is focused / it has no eligible candidates.
     * Shared by `drawResolutionSolutionPath` (the magenta line) and
     * `drawScrubbedTraffic` (so the target aircraft's marker at a future
     * horizon reflects the proposed clearance instead of "do nothing"). */
    function getActiveResolutionCandidate(cycle) {
        const track = cycle.tracks.find((t) => t.arhac_id === ui.selectedArhacId);
        if (!track) {
            return null;
        }
        const rs = cycle.resolution_sets.find((r) => r.arhac_id === track.arhac_id);
        if (!rs || rs.candidates.length === 0) {
            return null;
        }
        const activeIdx = Math.min(ui.selectedCandidateIndex[track.arhac_id] || 0, rs.candidates.length - 1);
        return rs.candidates[activeIdx];
    }

    /** The resolution candidate currently being previewed in the Event
     * panel (whichever track is focused there, whichever chip is active) --
     * drawn on the map as a magenta line so it's clear which way the
     * proposed clearance actually sends the aircraft, not just that a
     * clearance exists. Draws nothing if no track is focused or the
     * focused track has no eligible candidates.
     *
     * Note on what this line represents: each candidate is a single
     * constant clearance change (e.g. one heading step, held from the
     * observed position onward) evaluated at one horizon -- not a
     * multi-leg "turn away, then turn back onto a waypoint" maneuver.
     * The line drawn here is exactly that: a kink at the observed
     * position, then straight to the hypothetical horizon point(s). A
     * dogleg-back-to-course maneuver would need a resolution-engine
     * change (multi-leg candidate generation), not just a rendering one.
     */
    function drawResolutionSolutionPath(ctx, project, cycle) {
        const candidate = getActiveResolutionCandidate(cycle);
        if (!candidate || !candidate.hypothetical_path || candidate.hypothetical_path.length === 0) {
            return;
        }
        const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === candidate.target_callsign);
        if (!observed) {
            return;
        }
        const points = [...candidate.hypothetical_path].sort((a, b) => a.horizon_min - b.horizon_min);

        ctx.setLineDash([9, 3]);
        ctx.lineWidth = 2;
        ctx.strokeStyle = SOLUTION_COLOR;
        ctx.beginPath();
        const [sx, sy] = project(observed.lat, observed.lon);
        ctx.moveTo(sx, sy);
        points.forEach((p) => {
            const [px, py] = project(p.lat, p.lon);
            ctx.lineTo(px, py);
        });
        ctx.stroke();
        ctx.setLineDash([]);

        // Small filled diamond at the far end so the proposed heading's
        // direction is unambiguous even on a short line.
        const last = points[points.length - 1];
        const [ex, ey] = project(last.lat, last.lon);
        ctx.save();
        ctx.translate(ex, ey);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = SOLUTION_COLOR;
        ctx.fillRect(-4, -4, 8, 8);
        ctx.restore();
    }

    /** Where each aircraft is *currently drawn* at `ui.selectedHorizon`
     * -- observed position at horizon 0, else whatever
     * `drawScrubbedTraffic` itself would plot (hypothetical position for
     * a resolution's target aircraft, otherwise the plain prediction).
     * Shared by click hit-testing and the emphasized selected-aircraft
     * trajectory, so both always agree with what's on screen. */
    function getRenderedAircraftPosition(cycle, callsign) {
        if (ui.selectedHorizon === 0) {
            return cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign) || null;
        }
        const candidate = getActiveResolutionCandidate(cycle);
        if (candidate && candidate.target_callsign === callsign && candidate.hypothetical_path) {
            const hypo = candidate.hypothetical_path.find((p) => p.horizon_min === ui.selectedHorizon);
            if (hypo) {
                return { callsign, lat: hypo.lat, lon: hypo.lon, altitude_ft: hypo.altitude_ft };
            }
        }
        const points = cycle.prediction.paths[callsign];
        const atHorizon = points ? points.find((p) => p.horizon_min === ui.selectedHorizon) : null;
        return atHorizon ? { callsign, lat: atHorizon.lat, lon: atHorizon.lon, altitude_ft: atHorizon.altitude_ft } : null;
    }

    /** The callsign whose marker is within `radiusPx` of a click point, or
     * null. Picks the closest one if several are within range. */
    function findAircraftAtPixel(cycle, project, clickPx, clickPy, radiusPx) {
        let best = null;
        let bestDist = radiusPx;
        cycle.snapshot.aircraft.forEach((ac) => {
            const pos = getRenderedAircraftPosition(cycle, ac.callsign);
            if (!pos) {
                return;
            }
            const [x, y] = project(pos.lat, pos.lon);
            const dist = Math.hypot(x - clickPx, y - clickPy);
            if (dist < bestDist) {
                bestDist = dist;
                best = ac.callsign;
            }
        });
        return best;
    }

    /** The selected aircraft's full trajectory (observed position through
     * every predicted horizon, or its hypothetical path if it's also the
     * target of the previewed resolution candidate) drawn in bright
     * white -- deliberately independent of the "Predicted paths" toggle,
     * since picking a single aircraft to inspect is exactly the
     * uncluttered alternative to "every aircraft's path always on". */
    function drawSelectedAircraftPath(ctx, project, cycle) {
        const callsign = ui.selectedAircraftCallsign;
        if (!callsign) {
            return;
        }
        const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign);
        if (!observed) {
            return;
        }
        const candidate = getActiveResolutionCandidate(cycle);
        const useHypothetical = candidate && candidate.target_callsign === callsign && candidate.hypothetical_path;
        const points = useHypothetical
            ? [...candidate.hypothetical_path].sort((a, b) => a.horizon_min - b.horizon_min)
            : (cycle.prediction.paths[callsign] || []).slice().sort((a, b) => a.horizon_min - b.horizon_min);

        ctx.setLineDash([2, 3]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "rgba(255, 255, 255, 0.8)";
        ctx.beginPath();
        const [sx, sy] = project(observed.lat, observed.lon);
        ctx.moveTo(sx, sy);
        points.forEach((p) => {
            const [px, py] = project(p.lat, p.lon);
            ctx.lineTo(px, py);
        });
        ctx.stroke();
        ctx.setLineDash([]);
        points.forEach((p) => {
            const [px, py] = project(p.lat, p.lon);
            ctx.beginPath();
            ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
            ctx.arc(px, py, 2.5, 0, Math.PI * 2);
            ctx.fill();
        });
    }

    /** Populates/shows/hides the map's floating aircraft-info box for
     * whatever `ui.selectedAircraftCallsign` currently is. Always shows
     * the aircraft's real observed state (heading/speed/level), not a
     * predicted-horizon snapshot of it, regardless of which horizon
     * button is active -- the trajectory line is what shows the future. */
    function renderAircraftInfoBox(cycle) {
        const box = document.getElementById("aircraft-info-box");
        if (!box) {
            return;
        }
        const callsign = ui.selectedAircraftCallsign;
        const ac = callsign ? cycle.snapshot.aircraft.find((a) => a.callsign === callsign) : null;
        if (!ac) {
            box.classList.add("hidden");
            box.innerHTML = "";
            return;
        }
        const fl = Math.round(ac.altitude_ft / 100);
        box.classList.remove("hidden");
        box.innerHTML = `
            <div class="info-title"><span>${ac.callsign}</span><span class="info-close" id="aircraft-info-close">&times;</span></div>
            <div class="info-row"><span>Type</span><span>${ac.aircraft_type}</span></div>
            <div class="info-row"><span>Heading</span><span>${fmt(ac.heading_deg, 0)}&deg;</span></div>
            <div class="info-row"><span>Speed</span><span>${fmt(ac.ground_speed_kt, 0)} kt</span></div>
            <div class="info-row"><span>Level</span><span>FL${fl}</span></div>
            <div class="info-row"><span>V/S</span><span>${fmt(ac.vertical_speed_fpm, 0)} fpm</span></div>
        `;
        const closeBtn = document.getElementById("aircraft-info-close");
        if (closeBtn) {
            closeBtn.addEventListener("click", () => {
                ui.selectedAircraftCallsign = null;
                renderAircraftInfoBox(cycle);
                renderMap(cycle);
            });
        }
    }

    function drawPredictedPaths(ctx, project, cycle) {
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 1.5;
        Object.entries(cycle.prediction.paths).forEach(([callsign, points]) => {
            const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign);
            if (!observed || points.length === 0) {
                return;
            }
            ctx.beginPath();
            ctx.strokeStyle = "rgba(255, 191, 105, 0.75)"; // PREDICTED_PATH_COLOR (amber), distinct from aircraft/solution colors
            const [sx, sy] = project(observed.lat, observed.lon);
            ctx.moveTo(sx, sy);
            points.forEach((p) => {
                const [px, py] = project(p.lat, p.lon);
                ctx.lineTo(px, py);
            });
            ctx.stroke();
        });
        ctx.setLineDash([]);
    }

    /** One aircraft marker: heading triangle + speed leader line (when
     * heading/speed are known -- observed/interpolated traffic) or a plain
     * dot (predicted-horizon points only carry a position, no heading), plus
     * a boxed data-block label. This is the *only* place aircraft are
     * drawn -- both the animated "now" layer and the horizon-scrubbed
     * predicted layer call this, so the two never drift into two different
     * looks for the same underlying concept.
     *
     * @param {object} ac - {callsign, lat, lon, altitude_ft, heading_deg?, ground_speed_kt?}
     * @param {{color?: string, showHeading?: boolean}} opts
     */
    function drawAircraftMarker(ctx, project, ac, opts) {
        const options = opts || {};
        const color = options.color || AIRCRAFT_COLOR;
        const [x, y] = project(ac.lat, ac.lon);

        if (options.showHeading && ac.heading_deg !== undefined && ac.heading_deg !== null) {
            const headingRad = (ac.heading_deg * Math.PI) / 180;
            // Speed leader line -- standard radar "velocity vector": length
            // scales with ground speed so a fast jet visibly reaches further
            // ahead of its own symbol than a slow one, at a glance.
            const gs = ac.ground_speed_kt || 0;
            const leaderLen = Math.max(10, Math.min(40, gs / 10));
            const lx = x + Math.sin(headingRad) * leaderLen;
            const ly = y - Math.cos(headingRad) * leaderLen;
            ctx.beginPath();
            ctx.strokeStyle = color;
            ctx.globalAlpha = 0.55;
            ctx.lineWidth = 1;
            ctx.moveTo(x, y);
            ctx.lineTo(lx, ly);
            ctx.stroke();
            ctx.globalAlpha = 1;

            ctx.save();
            ctx.translate(x, y);
            ctx.rotate(headingRad);
            ctx.beginPath();
            ctx.moveTo(0, -7);
            ctx.lineTo(4, 6);
            ctx.lineTo(-4, 6);
            ctx.closePath();
            ctx.fillStyle = color;
            ctx.fill();
            ctx.restore();
        } else {
            ctx.beginPath();
            ctx.fillStyle = color;
            ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.fill();
        }

        if (ui.showAircraftLabels) {
            const label = `${ac.callsign} FL${Math.round(ac.altitude_ft / 100)}`;
            ctx.font = "12px monospace";
            const textWidth = ctx.measureText(label).width;
            const boxX = x + 8;
            const boxY = y - 9;
            ctx.fillStyle = "rgba(6, 10, 15, 0.72)";
            ctx.fillRect(boxX - 3, boxY - 2, textWidth + 6, 16);
            ctx.strokeStyle = color;
            ctx.globalAlpha = 0.7;
            ctx.lineWidth = 1;
            ctx.strokeRect(boxX - 3, boxY - 2, textWidth + 6, 16);
            ctx.globalAlpha = 1;
            ctx.fillStyle = "#d7e2ea";
            ctx.fillText(label, boxX, y + 3);
        }
    }

    /** Traffic at the scrubbed horizon: observed/interpolated aircraft (with
     * heading+leader line) at horizon 0, predicted-position dots (no
     * heading data) at future horizons. Delegates every marker to
     * `drawAircraftMarker` so both cases render identically apart from that. */
    function drawScrubbedTraffic(ctx, project, cycle, horizonMin, aircraftHighlight, observedOverride) {
        const highlight = aircraftHighlight || {};
        if (horizonMin === 0) {
            const list = observedOverride || cycle.snapshot.aircraft;
            list.forEach((ac) => {
                const h = highlight[ac.callsign];
                drawAircraftMarker(ctx, project, ac, { color: h ? h.color : AIRCRAFT_COLOR, showHeading: true });
            });
            return;
        }
        // If a resolution candidate is currently being previewed, its
        // target aircraft is drawn at the *hypothetical* (post-clearance)
        // position instead of the do-nothing prediction, in the solution
        // color -- so scrubbing the horizon forward actually shows the
        // aircraft turning onto the proposed heading, matching the
        // magenta line drawn by `drawResolutionSolutionPath`. Every other
        // aircraft is unaffected (still the plain predicted position).
        const candidate = getActiveResolutionCandidate(cycle);
        const hypoPoint =
            candidate && candidate.hypothetical_path
                ? candidate.hypothetical_path.find((p) => p.horizon_min === horizonMin)
                : null;

        Object.entries(cycle.prediction.paths).forEach(([callsign, points]) => {
            const isHypoTarget = candidate && callsign === candidate.target_callsign && hypoPoint;
            const atHorizon = isHypoTarget ? hypoPoint : points.find((p) => p.horizon_min === horizonMin);
            if (!atHorizon) {
                return;
            }
            const h = highlight[callsign];
            drawAircraftMarker(
                ctx,
                project,
                { callsign, lat: atHorizon.lat, lon: atHorizon.lon, altitude_ft: atHorizon.altitude_ft },
                { color: isHypoTarget ? SOLUTION_COLOR : h ? h.color : "#e0a63c", showHeading: false }
            );
        });
    }

    /** Linearly interpolate observed-aircraft positions between the previous
     * and current poll, by wall-clock fraction elapsed -- purely a display
     * smoothing (§ "smoother animations"); the backend still only ever
     * ticks once per `poll_interval_s`, this just avoids the traffic layer
     * visibly jumping every time it does. */
    function interpolatedObservedAircraft() {
        const cur = window.__astraLastCycle;
        if (!cur) {
            return [];
        }
        const curList = cur.snapshot.aircraft;
        const prevList = ui.prevSnapshotAircraft;
        if (!prevList) {
            return curList;
        }
        const span = Math.max(1, ui.curCycleAtMs - ui.prevCycleAtMs);
        const t = Math.max(0, Math.min(1, (performance.now() - ui.curCycleAtMs) / span));
        const prevByCallsign = {};
        prevList.forEach((ac) => {
            prevByCallsign[ac.callsign] = ac;
        });
        return curList.map((ac) => {
            const p = prevByCallsign[ac.callsign];
            if (!p) {
                return ac; // just spawned -- nothing to interpolate from
            }
            return Object.assign({}, ac, {
                lat: p.lat + (ac.lat - p.lat) * t,
                lon: p.lon + (ac.lon - p.lon) * t,
            });
        });
    }

    /** Static base layer -- background, geo overlays, sector/hotspot rings,
     * faint predicted paths. Redrawn once per poll cycle (not per animation
     * frame): nothing here depends on sub-poll-interval time, so redrawing
     * it 60x/sec would just burn cycles for an identical picture.
     *
     * Also called directly (not just per-poll) after a pan/zoom/reset, so
     * the view updates instantly without waiting for the next tick --
     * `cycle` is always `window.__astraLastCycle` in that case. */
    function renderMap(cycle) {
        const canvas = document.getElementById("map-canvas");
        ensureCanvasSize(canvas);
        const ctx = canvas.getContext("2d");
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        ctx.clearRect(0, 0, width, height);

        if (!ui.view) {
            ui.view = loadPersistedView() || fitToDataView(cycle);
        }
        const bounds = ui.view;
        const project = makeProjector(bounds, width, height);
        // Cached for the traffic-overlay animation loop, which must reuse
        // this exact projection rather than recompute bounds every frame
        // (recomputing from interpolated positions would make the view
        // visibly "breathe" as aircraft move).
        ui.mapProject = project;
        ui.aircraftHighlight = buildAircraftHighlightMap(cycle);

        drawGrid(ctx, width, height);
        geoLayers.draw(ctx, project, (layer, feature) => {
            if (layer.id !== "sectors") {
                return true;
            }
            const name = feature.properties && feature.properties.name;
            return !ui.hiddenSectorNames.has(name);
        });
        drawSectorBoundaries(ctx, project, bounds, width, cycle.sector_regions);
        const regionsAtHorizon = cycle.regions_by_horizon[String(ui.selectedHorizon)] || [];
        drawComplexityRegions(ctx, project, bounds, width, regionsAtHorizon, cycle);
        if (ui.showPredictedPaths) {
            drawPredictedPaths(ctx, project, cycle);
            drawResolutionSolutionPath(ctx, project, cycle);
        }
        drawSelectedAircraftPath(ctx, project, cycle);
        renderAircraftInfoBox(cycle);
    }

    /** Animated overlay -- aircraft markers only, redrawn every animation
     * frame so horizon-0 traffic glides between polls instead of jumping. */
    function renderTrafficOverlay() {
        const canvas = document.getElementById("map-traffic-canvas");
        const cycle = window.__astraLastCycle;
        if (!canvas || !cycle || !ui.mapProject) {
            return;
        }
        ensureCanvasSize(canvas);
        const ctx = canvas.getContext("2d");
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        ctx.clearRect(0, 0, width, height);
        const observed = ui.selectedHorizon === 0 ? interpolatedObservedAircraft() : null;
        drawScrubbedTraffic(ctx, ui.mapProject, cycle, ui.selectedHorizon, ui.aircraftHighlight, observed);
    }

    // ------------------------------------------------------------------
    // Alerts table
    // ------------------------------------------------------------------

    function onsetClass(onsetInS) {
        const bucket = urgencyBucket(onsetInS);
        return bucket === "none" ? "" : `onset-${bucket}`;
    }

    function nearestSectorName(track, cycle) {
        const sectorRegions = cycle.sector_regions || {};
        const names = Object.keys(sectorRegions);
        if (names.length === 0 || !track.centroid) {
            return "-";
        }
        let best = null;
        let bestDist = Infinity;
        names.forEach((name) => {
            const c = sectorRegions[name].cluster;
            const d = roughDistanceNm(track.centroid.lat, track.centroid.lon, c.centroid_lat, c.centroid_lon);
            if (d < bestDist) {
                bestDist = d;
                best = name;
            }
        });
        return best || "-";
    }

    function confidenceBadge(confidence) {
        if (confidence === null || confidence === undefined) {
            return "-";
        }
        const pct = Math.round(confidence * 100);
        return `<span style="color:${confidenceColor(confidence)}">${pct}%</span>`;
    }

    function renderTracksTable(cycle, onSelect) {
        const tbody = document.getElementById("tracks-tbody");
        const tracks = cycle.tracks;
        if (tracks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No open tracks.</td></tr>';
            return;
        }
        const nowS = cycle.snapshot.timestamp_s;
        const sorted = [...tracks].sort((a, b) => {
            const ar = a.forecast_urgency_rank === null ? Infinity : a.forecast_urgency_rank;
            const br = b.forecast_urgency_rank === null ? Infinity : b.forecast_urgency_rank;
            if (ar !== br) {
                return ar - br;
            }
            return a.priority - b.priority;
        });
        tbody.innerHTML = sorted
            .map((t) => {
                const onsetInS = t.predicted_onset_s === null ? null : t.predicted_onset_s - nowS;
                const onsetLabel = onsetInS === null ? "-" : countdownFmt(onsetInS);
                const actByLabel = t.predicted_onset_s === null ? "-" : clockFmt(t.predicted_onset_s);
                const complexityNow = t.current_complexity_score !== null ? t.current_complexity_score : t.peak_complexity;
                const selected = t.arhac_id === ui.selectedArhacId ? "selected" : "";
                return `
            <tr class="${selected}" data-arhac-id="${t.arhac_id}">
                <td>${t.arhac_id.slice(0, 8)}</td>
                <td>${statusPill(t.status)}</td>
                <td class="${onsetClass(onsetInS)}">${onsetLabel}</td>
                <td>${actByLabel}</td>
                <td>${t.member_aircraft.join(", ")}</td>
                <td>${nearestSectorName(t, cycle)}</td>
                <td style="color:${complexityColor(complexityNow)}">${fmt(complexityNow)}</td>
                <td>${confidenceBadge(t.confidence)}</td>
            </tr>`;
            })
            .join("");
        tbody.querySelectorAll("tr[data-arhac-id]").forEach((row) => {
            row.addEventListener("click", () => onSelect(row.dataset.arhacId));
        });
    }

    // ------------------------------------------------------------------
    // Aircraft panel
    // ------------------------------------------------------------------

    /** Centre the map view on one lat/lon, keeping the current zoom span. */
    function panMapTo(lat, lon) {
        if (!ui.view) {
            return;
        }
        const latSpan = ui.view.maxLat - ui.view.minLat;
        const lonSpan = ui.view.maxLon - ui.view.minLon;
        ui.view = {
            minLat: lat - latSpan / 2,
            maxLat: lat + latSpan / 2,
            minLon: lon - lonSpan / 2,
            maxLon: lon + lonSpan / 2,
        };
        savePersistedView(ui.view);
        if (window.__astraLastCycle) {
            renderMap(window.__astraLastCycle);
        }
    }

    /** One-time wiring for the Aircraft panel's manual clearance form
     * (--mock only). The dropdown's options are refreshed on every
     * `renderAircraftPanel()` call, not here -- this only attaches the
     * submit handler. */
    function setupManualClearanceForm() {
        const form = document.getElementById("manual-clearance-form");
        const status = document.getElementById("clearance-status");
        const button = form.querySelector("button");
        form.addEventListener("submit", async (evt) => {
            evt.preventDefault();
            const callsign = document.getElementById("clearance-callsign").value;
            const clearanceType = document.getElementById("clearance-type").value;
            const rawValue = document.getElementById("clearance-value").value;
            if (!callsign || rawValue === "") {
                status.textContent = "Pick an aircraft and a value.";
                status.className = "clearance-status error";
                return;
            }
            button.disabled = true;
            status.textContent = "Sending\u2026";
            status.className = "clearance-status";
            try {
                const resp = await fetch("/scenario/clearance", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ callsign, clearance_type: clearanceType, value: Number(rawValue) }),
                });
                const body = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    throw new Error(body.error || `HTTP ${resp.status}`);
                }
                status.textContent = `${callsign}: ${clearanceType} \u2192 ${rawValue} sent.`;
                status.className = "clearance-status ok";
                document.getElementById("clearance-value").value = "";
            } catch (err) {
                status.textContent = `Failed: ${err.message}`;
                status.className = "clearance-status error";
            } finally {
                button.disabled = false;
            }
        });
    }

    /** Keep the manual-clearance callsign dropdown in sync with current
     * traffic, preserving whatever the user has selected across polls
     * (rebuilding options every cycle would otherwise reset it). */
    function syncClearanceCallsignOptions(aircraft) {
        const select = document.getElementById("clearance-callsign");
        if (!select) {
            return;
        }
        const previousValue = select.value;
        const callsigns = aircraft.map((ac) => ac.callsign).sort();
        const optionsKey = callsigns.join(",");
        if (select.dataset.optionsKey === optionsKey) {
            return;
        }
        select.dataset.optionsKey = optionsKey;
        select.innerHTML =
            '<option value="" disabled>Aircraft&hellip;</option>' +
            callsigns.map((cs) => `<option value="${cs}">${cs}</option>`).join("");
        if (callsigns.includes(previousValue)) {
            select.value = previousValue;
        }
    }

    /** Simulation-speed button group (--mock only; a 409 from the server
     * just means "not running --mock", which the status text reports).
     * Purely a multiplier on how much sim-time each poll advances by --
     * see MockConnector.set_speed_multiplier -- so it speeds up how fast
     * tracks/forecasts play out without changing how often the browser
     * itself refreshes. */
    const SPEED_MULTIPLIERS = [1, 2, 5, 10, 20];

    function setupSpeedButtons() {
        const container = document.getElementById("speed-buttons");
        if (!container) {
            return;
        }
        container.innerHTML = SPEED_MULTIPLIERS.map(
            (x) => `<button type="button" class="horizon-btn${x === 1 ? " active" : ""}" data-speed="${x}">${x}x</button>`
        ).join("");
        container.addEventListener("click", async (evt) => {
            const btn = evt.target.closest(".horizon-btn");
            if (!btn) {
                return;
            }
            const multiplier = Number(btn.dataset.speed);
            container.querySelectorAll(".horizon-btn").forEach((b) => b.classList.toggle("active", b === btn));
            try {
                await fetch("/scenario/control", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "speed", multiplier }),
                });
            } catch (e) {
                // Live-BlueSky mode or server hiccup -- button state already
                // reflects the click; nothing further to show here.
            }
        });
    }

    /** Pause/resume the mock simulation clock without leaving the
     * Dissipation Workspace for the Scenario Builder page. Tracks
     * running state optimistically from each action's own response
     * (matching scenario_builder.js's pattern); reads the real current
     * state once at startup via GET /scenario/state so the label starts
     * correct even if the sim was already paused before this page loaded. */
    function setupPauseResumeButton() {
        const btn = document.getElementById("pause-resume-btn");
        if (!btn) {
            return;
        }
        let running = true;

        function applyState(isRunning) {
            running = isRunning;
            btn.textContent = running ? "Pause" : "Resume";
            btn.classList.toggle("active", !running);
        }

        fetch("/scenario/state")
            .then((r) => r.json())
            .then((body) => {
                if (body && typeof body.running === "boolean") {
                    applyState(body.running);
                }
            })
            .catch(() => {});

        btn.addEventListener("click", async () => {
            btn.disabled = true;
            try {
                const resp = await fetch("/scenario/control", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: running ? "pause" : "resume" }),
                });
                const body = await resp.json().catch(() => ({}));
                if (resp.ok && typeof body.running === "boolean") {
                    applyState(body.running);
                }
            } catch (e) {
                // Live-BlueSky mode or server hiccup -- leave label as-is.
            } finally {
                btn.disabled = false;
            }
        });
    }

    /** All currently-observed aircraft, sorted callsign-wise, each with an
     * urgency badge colour (shared with the map highlight/hotspot rings)
     * when the aircraft belongs to an open track. Click a row to pan the
     * map to that aircraft. */
    function renderAircraftPanel(cycle) {
        const container = document.getElementById("aircraft-list");
        if (!container) {
            return;
        }
        const aircraft = [...cycle.snapshot.aircraft].sort((a, b) => a.callsign.localeCompare(b.callsign));
        syncClearanceCallsignOptions(aircraft);
        if (aircraft.length === 0) {
            container.innerHTML = '<p class="empty-row">No aircraft in view.</p>';
            return;
        }
        const highlight = ui.aircraftHighlight || {};
        container.innerHTML = aircraft
            .map((ac) => {
                const h = highlight[ac.callsign];
                const badgeColor = h ? h.color : "#4a5866";
                const fl = `FL${Math.round(ac.altitude_ft / 100)}`;
                const hdg = ac.heading_deg !== null && ac.heading_deg !== undefined ? `${Math.round(ac.heading_deg)}\u00b0` : "-";
                const gs = ac.ground_speed_kt !== null && ac.ground_speed_kt !== undefined ? `${Math.round(ac.ground_speed_kt)}kt` : "-";
                return `
            <div class="aircraft-row" data-callsign="${ac.callsign}">
                <span class="ac-badge" style="background:${badgeColor}"></span>
                <span class="ac-callsign">${ac.callsign}</span>
                <span class="ac-field">${fl}</span>
                <span class="ac-field">${gs}</span>
                <span class="ac-field">${hdg}</span>
            </div>`;
            })
            .join("");
        container.querySelectorAll(".aircraft-row").forEach((row) => {
            row.addEventListener("click", () => {
                const ac = aircraft.find((a) => a.callsign === row.dataset.callsign);
                if (ac) {
                    panMapTo(ac.lat, ac.lon);
                }
            });
        });
    }

    // ------------------------------------------------------------------
    // Event & Dissipation panel
    // ------------------------------------------------------------------

    /** Generic progress ring: `pct` in [0, 1], stroked in `color`. */
    function ringSvg(pct, size, color) {
        const r = size / 2 - 6;
        const c = 2 * Math.PI * r;
        const clamped = Math.max(0, Math.min(1, pct === null || pct === undefined ? 0 : pct));
        return `
            <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#1c2732" stroke-width="6" />
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6"
                    stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - clamped)}"
                    stroke-linecap="round" transform="rotate(-90 ${size / 2} ${size / 2})" />
            </svg>`;
    }

    /** Before/after complexity rings (linked by an arrow) plus a confidence bar,
     * mirroring the reference ASTRA Event & Dissipation panel layout. */
    function renderComplexityReduction(track, candidate) {
        const container = document.getElementById("complexity-reduction");
        const before = candidate ? candidate.complexity_before : track.current_complexity_score;
        const after = candidate ? candidate.complexity_after : null;
        const afterKnown = after !== null && after !== undefined;
        const confidence = track.confidence;
        const confidencePct = confidence === null || confidence === undefined ? 0 : confidence;

        container.innerHTML = `
            <div class="ring-pair">
                <div class="ring-slot">
                    ${ringSvg(before / 100, 64, complexityColor(before))}
                    <div class="ring-value">${fmt(before, 0)}</div>
                </div>
                <div class="ring-arrow">&rarr;</div>
                <div class="ring-slot">
                    ${afterKnown ? ringSvg(after / 100, 64, complexityColor(after)) : ringSvg(0, 64, "#1c2732")}
                    <div class="ring-value">${afterKnown ? fmt(after, 0) : "-"}</div>
                </div>
                <div class="ring-caption">Complexity</div>
            </div>
            <div class="confidence-bar-wrap">
                <div class="confidence-bar-caption">Confidence</div>
                <div class="confidence-bar-track">
                    <div class="confidence-bar-fill" style="width:${confidencePct * 100}%; background:${confidenceColor(confidencePct)}"></div>
                </div>
                <div class="confidence-bar-value">${confidence === null ? "-" : Math.round(confidence * 100) + "%"}</div>
            </div>`;
    }

    /** Draft -> Proposed -> Acknowledged stepper with Reject/Proceed actions,
     * one per ARHAC event (not per-candidate) -- see reference Fig 30. */
    function renderEventStepper(rs, onChange) {
        const container = document.getElementById("event-stepper");
        if (!rs) {
            container.innerHTML = "";
            return;
        }
        const current = ui.lifecycle[rs.arhac_id] || "DRAFT";
        const currentIdx = LIFECYCLE_STAGES.indexOf(current);
        const captions = { DRAFT: "Under proposal", PROPOSED: "Coordinate with other actors", ACKNOWLEDGED: "Dissipation in effect" };
        container.innerHTML = `
            <div class="stepper-steps">
                ${LIFECYCLE_STAGES.map((stage, idx) => {
                    const state = idx < currentIdx ? "done" : idx === currentIdx ? "current" : "pending";
                    return `
                    <div class="stepper-step ${state}">
                        <div class="stepper-dot">${idx + 1}</div>
                        <div class="stepper-text">
                            <div class="stepper-label">${stage[0] + stage.slice(1).toLowerCase()}</div>
                            ${idx === currentIdx ? `<div class="stepper-caption">${captions[stage]}</div>` : ""}
                        </div>
                    </div>`;
                }).join('<div class="stepper-connector"></div>')}
            </div>
            <div class="stepper-actions">
                <button id="stepper-reject" class="btn-stepper btn-reject" ${currentIdx === 0 ? "disabled" : ""}>Reject</button>
                <button id="stepper-proceed" class="btn-stepper btn-proceed" ${currentIdx === LIFECYCLE_STAGES.length - 1 ? "disabled" : ""}>Proceed</button>
            </div>`;
        const reject = document.getElementById("stepper-reject");
        const proceed = document.getElementById("stepper-proceed");
        if (reject) {
            reject.addEventListener("click", () => {
                ui.lifecycle[rs.arhac_id] = "DRAFT";
                onChange();
            });
        }
        if (proceed) {
            proceed.addEventListener("click", () => {
                ui.lifecycle[rs.arhac_id] = LIFECYCLE_STAGES[Math.min(currentIdx + 1, LIFECYCLE_STAGES.length - 1)];
                onChange();
            });
        }
    }

    /** Numbered "solution proposal" chips (one per ranked candidate) plus a
     * single detail line for whichever chip is active. */
    function renderCandidateList(rs, onSelectCandidate) {
        const container = document.getElementById("candidate-list");
        if (!rs || rs.candidates.length === 0) {
            container.innerHTML = '<p class="panel-hint">No eligible resolution candidates this cycle.</p>';
            return;
        }
        const activeIdx = Math.min(ui.selectedCandidateIndex[rs.arhac_id] || 0, rs.candidates.length - 1);
        const c = rs.candidates[activeIdx];
        const scoreClass = c.resolution_score >= 0 ? "cand-score-positive" : "cand-score-negative";
        const sign = c.delta_value >= 0 ? "+" : "";
        container.innerHTML = `
            <div class="panel-hint" style="margin-bottom:6px;">Solution proposal (evaluated at +${rs.evaluated_horizon_min} min)</div>
            <div class="candidate-chips">
                ${rs.candidates
                    .map((_, idx) => `<button class="candidate-chip ${idx === activeIdx ? "active" : ""}" data-idx="${idx}">${idx + 1}</button>`)
                    .join("")}
            </div>
            <div class="candidate-current">
                <span class="cand-type">${c.clearance_type}</span>
                <span>${c.target_callsign}</span>
                <span>${sign}${fmt(c.delta_value, 0)}</span>
                <span class="${scoreClass}">score ${fmt(c.resolution_score, 2)}</span>
            </div>`;
        container.querySelectorAll(".candidate-chip").forEach((chip) => {
            chip.addEventListener("click", () => onSelectCandidate(Number(chip.dataset.idx)));
        });
    }

    function renderComponentBars(candidate) {
        const container = document.getElementById("component-bars");
        if (!candidate || !candidate.complexity_before_components) {
            container.innerHTML = '<p class="panel-hint">No component breakdown for this candidate.</p>';
            return;
        }
        const before = candidate.complexity_before_components;
        const after = candidate.complexity_after_components || {};
        const keys = Object.keys(before);
        container.innerHTML = keys
            .map((key) => {
                const b = before[key];
                const a = after[key] !== undefined ? after[key] : b;
                const max = Math.max(b, a, 0.0001) * 1.15;
                const bPct = (b / max) * 100;
                const aPct = (a / max) * 100;
                return `
                <div class="component-row">
                    <div class="component-name">${key}: ${fmt(b, 2)} &rarr; ${fmt(a, 2)}</div>
                    <div class="component-bar-track">
                        <div class="component-bar-fill before" style="width:${bPct}%"></div>
                    </div>
                    <div class="component-bar-track">
                        <div class="component-bar-fill after" style="width:${aPct}%"></div>
                    </div>
                </div>`;
            })
            .join("");
    }

    /** Build {horizon_min -> {lat, lon, altitude_ft}} for one aircraft, horizon 0 = observed. */
    function pathByHorizon(cycle, callsign) {
        const byHorizon = {};
        const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign);
        if (observed) {
            byHorizon[0] = { lat: observed.lat, lon: observed.lon, altitude_ft: observed.altitude_ft };
        }
        (cycle.prediction.paths[callsign] || []).forEach((p) => {
            byHorizon[p.horizon_min] = { lat: p.lat, lon: p.lon, altitude_ft: p.altitude_ft };
        });
        return byHorizon;
    }

    function renderWhatIfVertical(cycle, candidate) {
        const svg = document.getElementById("whatif-vertical");
        if (!candidate) {
            svg.innerHTML = "";
            return;
        }
        const originalByHorizon = pathByHorizon(cycle, candidate.target_callsign);
        const hypoByHorizon = { 0: originalByHorizon[0] };
        candidate.hypothetical_path.forEach((p) => {
            hypoByHorizon[p.horizon_min] = p;
        });
        const horizons = Array.from(
            new Set([...Object.keys(originalByHorizon), ...Object.keys(hypoByHorizon)].map(Number))
        ).sort((a, b) => a - b);
        if (horizons.length < 2) {
            svg.innerHTML = '<text x="10" y="70" fill="#7c8a97" font-size="11">Not enough points to plot.</text>';
            return;
        }
        const width = 420;
        const height = 140;
        const maxH = Math.max(...horizons, 1);
        const allAlts = horizons
            .flatMap((h) => [originalByHorizon[h] && originalByHorizon[h].altitude_ft, hypoByHorizon[h] && hypoByHorizon[h].altitude_ft])
            .filter((v) => v !== undefined);
        const minAlt = Math.min(...allAlts);
        const maxAlt = Math.max(...allAlts, minAlt + 100);
        const x = (h) => 10 + (h / maxH) * (width - 20);
        const y = (alt) => height - 12 - ((alt - minAlt) / (maxAlt - minAlt)) * (height - 24);
        const line = (byHorizon, color) => {
            const pts = horizons
                .filter((h) => byHorizon[h])
                .map((h) => `${x(h)},${y(byHorizon[h].altitude_ft)}`)
                .join(" ");
            return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" />`;
        };
        svg.innerHTML = `
            ${line(originalByHorizon, "#4a90a4")}
            ${line(hypoByHorizon, "#35c3a3")}
            <text x="10" y="14" fill="#4a90a4" font-size="10">original</text>
            <text x="70" y="14" fill="#35c3a3" font-size="10">with clearance</text>`;
    }

    function renderWhatIfHorizontal(cycle, candidate) {
        const svg = document.getElementById("whatif-horizontal");
        if (!candidate) {
            svg.innerHTML = "";
            return;
        }
        const originalByHorizon = pathByHorizon(cycle, candidate.target_callsign);
        const hypoByHorizon = { 0: originalByHorizon[0] };
        candidate.hypothetical_path.forEach((p) => {
            hypoByHorizon[p.horizon_min] = p;
        });
        const points = [...Object.values(originalByHorizon), ...Object.values(hypoByHorizon)];
        if (points.length < 2) {
            svg.innerHTML = '<text x="10" y="70" fill="#7c8a97" font-size="11">Not enough points to plot.</text>';
            return;
        }
        const width = 420;
        const height = 140;
        const lats = points.map((p) => p.lat);
        const lons = points.map((p) => p.lon);
        const bounds = {
            minLat: Math.min(...lats),
            maxLat: Math.max(...lats, Math.min(...lats) + 0.01),
            minLon: Math.min(...lons),
            maxLon: Math.max(...lons, Math.min(...lons) + 0.01),
        };
        const project = makeProjector(bounds, width - 20, height - 20);
        const line = (byHorizon, color) => {
            const horizons = Object.keys(byHorizon).map(Number).sort((a, b) => a - b);
            const pts = horizons
                .map((h) => {
                    const [px, py] = project(byHorizon[h].lat, byHorizon[h].lon);
                    return `${px + 10},${py + 10}`;
                })
                .join(" ");
            return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" />`;
        };
        svg.innerHTML = `
            ${line(originalByHorizon, "#4a90a4")}
            ${line(hypoByHorizon, "#35c3a3")}`;
    }

    function renderEventPanel(cycle) {
        const empty = document.getElementById("event-empty");
        const body = document.getElementById("event-body");
        const track = cycle.tracks.find((t) => t.arhac_id === ui.selectedArhacId);
        if (!track) {
            empty.classList.remove("hidden");
            body.classList.add("hidden");
            return;
        }
        empty.classList.add("hidden");
        body.classList.remove("hidden");

        const rs = cycle.resolution_sets.find((r) => r.arhac_id === track.arhac_id);
        const activeIdx = ui.selectedCandidateIndex[track.arhac_id] || 0;
        const candidate = rs && rs.candidates.length > 0 ? rs.candidates[Math.min(activeIdx, rs.candidates.length - 1)] : null;

        renderEventStepper(rs, () => renderEventPanel(cycle));
        renderComplexityReduction(track, candidate);
        renderCandidateList(rs, (idx) => {
            ui.selectedCandidateIndex[track.arhac_id] = idx;
            renderEventPanel(cycle);
            if (window.__astraLastCycle) {
                renderMap(window.__astraLastCycle);
            }
        });
        renderComponentBars(candidate);
        renderWhatIfVertical(cycle, candidate);
        renderWhatIfHorizontal(cycle, candidate);
    }

    // ------------------------------------------------------------------
    // Timeline panel
    // ------------------------------------------------------------------

    function renderTimeline(tracks) {
        const container = document.getElementById("timeline-list");
        const openTracks = tracks.filter((t) => t.status !== "CLOSED" && t.history.length > 0);
        if (openTracks.length === 0) {
            container.innerHTML = '<p class="panel-hint">No open tracks with history yet.</p>';
            return;
        }
        container.innerHTML = openTracks.map((t) => renderTrackTimeline(t)).join("");
    }

    function renderTrackTimeline(track) {
        const width = 460;
        const height = 60;
        const times = track.history.map((h) => h.time_s);
        const markers = [track.predicted_onset_s, track.predicted_peak_time_s, track.predicted_dissipation_s].filter(
            (v) => v !== null && v !== undefined
        );
        const allTimes = times.concat(markers);
        const minT = Math.min(...allTimes);
        const maxT = Math.max(...allTimes, minT + 1);
        const x = (t) => 10 + ((t - minT) / (maxT - minT)) * (width - 20);
        const y = (score) => height - 8 - (Math.max(0, Math.min(100, score)) / 100) * (height - 16);

        const points = track.history.map((h) => `${x(h.time_s)},${y(h.complexity_score)}`).join(" ");

        const markerSvg = [
            [track.predicted_onset_s, "#e0a63c", "onset"],
            [track.predicted_peak_time_s, "#e0553c", "peak"],
            [track.predicted_dissipation_s, "#35c3a3", "dissipation"],
        ]
            .filter(([t]) => t !== null && t !== undefined)
            .map(
                ([t, color, label]) =>
                    `<line x1="${x(t)}" y1="0" x2="${x(t)}" y2="${height}" stroke="${color}" stroke-dasharray="3,3" />` +
                    `<text x="${x(t)}" y="10" fill="${color}" font-size="9">${label}</text>`
            )
            .join("");

        return `
            <div class="track-timeline">
                <div class="track-timeline-label">
                    ARHAC ${track.arhac_id.slice(0, 8)} ${statusPill(track.status)}
                </div>
                <svg width="${width}" height="${height}">
                    ${markerSvg}
                    <polyline points="${points}" fill="none" stroke="#4a90a4" stroke-width="1.5" />
                </svg>
            </div>`;
    }

    // ------------------------------------------------------------------
    // Sector complexity page
    // ------------------------------------------------------------------

    function sectorHistorySvg(samples) {
        const width = 320;
        const height = 90;
        if (samples.length < 2) {
            return '<svg viewBox="0 0 320 90"><text x="10" y="45" fill="#7c8a97" font-size="11">Not enough history yet.</text></svg>';
        }
        const times = samples.map((s) => s.bucket_start_s);
        const minT = Math.min(...times);
        const maxT = Math.max(...times, minT + 1);
        const x = (t) => 6 + ((t - minT) / (maxT - minT)) * (width - 12);
        const y = (score) => height - 6 - (Math.max(0, Math.min(100, score)) / 100) * (height - 14);
        const points = samples.map((s) => `${x(s.bucket_start_s)},${y(s.complexity_score)}`).join(" ");
        const bars = samples
            .map((s) => {
                const color = complexityColor(s.complexity_score);
                return `<circle cx="${x(s.bucket_start_s)}" cy="${y(s.complexity_score)}" r="2.5" fill="${color}" />`;
            })
            .join("");
        return `
            <svg viewBox="0 0 ${width} ${height}">
                <polyline points="${points}" fill="none" stroke="#4a90a4" stroke-width="1.5" />
                ${bars}
            </svg>`;
    }

    function renderSectorsTab(cycle) {
        const container = document.getElementById("sector-charts");
        const names = Object.keys(cycle.sector_regions || {});
        if (names.length === 0) {
            container.innerHTML =
                '<p class="panel-hint">No sectors configured. Add entries to <code>ASTRAConfig.sectors</code> to populate this page.</p>';
            return;
        }
        container.innerHTML = names
            .map((name) => {
                const region = cycle.sector_regions[name];
                const history = (cycle.sector_history && cycle.sector_history[name]) || [];
                return `
                <div class="sector-card">
                    <div class="sector-card-head">
                        <span class="sector-card-name">${name}</span>
                        <span class="sector-card-score" style="color:${complexityColor(region.complexity_score)}">
                            ${fmt(region.complexity_score)}
                        </span>
                    </div>
                    <div class="panel-hint" style="margin:0 0 6px 0;">${region.cluster.member_callsigns.length} aircraft now</div>
                    ${sectorHistorySvg(history)}
                </div>`;
            })
            .join("");
    }

    // ------------------------------------------------------------------
    // Poll loop
    // ------------------------------------------------------------------

    function selectTrack(arhacId) {
        ui.selectedArhacId = arhacId;
        if (window.__astraLastCycle) {
            renderTracksTable(window.__astraLastCycle, selectTrack);
            renderEventPanel(window.__astraLastCycle);
            renderMap(window.__astraLastCycle);
        }
    }

    function render(payload) {
        renderHeader(payload);
        if (!payload.has_data) {
            return;
        }
        const cycle = payload.cycle;
        // Shift current -> previous *before* overwriting, so the traffic
        // overlay's animation loop can interpolate between the two exact
        // snapshots the poll loop actually saw (not a guess).
        if (window.__astraLastCycle) {
            ui.prevSnapshotAircraft = window.__astraLastCycle.snapshot.aircraft;
            ui.prevCycleAtMs = ui.curCycleAtMs || performance.now();
        }
        ui.curCycleAtMs = performance.now();
        window.__astraLastCycle = cycle;

        if (ui.selectedArhacId && !cycle.tracks.some((t) => t.arhac_id === ui.selectedArhacId)) {
            ui.selectedArhacId = null;
        }
        if (!ui.selectedArhacId && cycle.tracks.length > 0) {
            const sorted = [...cycle.tracks].sort((a, b) => {
                const ar = a.forecast_urgency_rank === null ? Infinity : a.forecast_urgency_rank;
                const br = b.forecast_urgency_rank === null ? Infinity : b.forecast_urgency_rank;
                return ar - br;
            });
            ui.selectedArhacId = sorted[0].arhac_id;
        }

        syncHorizonScrubber(cycle);
        renderMap(cycle);
        renderTracksTable(cycle, selectTrack);
        renderAircraftPanel(cycle);
        renderEventPanel(cycle);
        renderTimeline(cycle.tracks);
        renderSectorsTab(cycle);
    }

    function poll() {
        fetch("/state")
            .then((response) => response.json())
            .then(render)
            .catch((err) => console.error("ASTRA dashboard: /state fetch failed", err))
            .finally(() => setTimeout(poll, POLL_INTERVAL_MS));
    }

    function animateTrafficOverlay() {
        renderTrafficOverlay();
        requestAnimationFrame(animateTrafficOverlay);
    }

    /** Restore any saved per-layer show/hide state onto `geoLayers` --
     * called once, right after `geoLayers.init()` resolves and before the
     * toggle checkboxes are built, so the checkboxes' initial `checked`
     * state already reflects what was persisted. */
    function applyPersistedLayerVisibility() {
        const saved = loadPersistedLayerVisibility();
        geoLayers.layers.forEach((l) => {
            if (Object.prototype.hasOwnProperty.call(saved, l.id)) {
                l.visible = saved[l.id];
            }
        });
    }

    /** Wheel-to-zoom (anchored under the cursor), drag-to-pan, and
     * double-click-to-reset (fit to FIR extent) on the map. Attached to
     * `#map-stack` (the wrapper around both canvases) so it doesn't
     * matter which of the two stacked canvases is on top. Every
     * interaction only ever touches `ui.view` + triggers an immediate
     * `renderMap()` -- the animated traffic overlay is untouched by any
     * of this, since it only ever reads `ui.mapProject`, whatever set it. */
    function setupMapInteraction() {
        const stack = document.getElementById("map-stack");
        const canvas = document.getElementById("map-canvas");
        if (!stack || !canvas) {
            return;
        }

        function currentCycleOrEmpty() {
            return window.__astraLastCycle;
        }

        function redraw() {
            const cycle = currentCycleOrEmpty();
            if (cycle) {
                renderMap(cycle);
            }
        }

        function toCanvasPx(clientX, clientY) {
            const rect = canvas.getBoundingClientRect();
            return [
                ((clientX - rect.left) / rect.width) * canvas.width,
                ((clientY - rect.top) / rect.height) * canvas.height,
            ];
        }

        stack.addEventListener(
            "wheel",
            (evt) => {
                evt.preventDefault();
                if (!ui.view) {
                    return;
                }
                const [px, py] = toCanvasPx(evt.clientX, evt.clientY);
                const unproject = makeUnprojector(ui.view, canvas.width, canvas.height);
                const [anchorLat, anchorLon] = unproject(px, py);
                // Continuous (not stepped) scale factor per wheel notch --
                // "smooth zoom" as fine-grained zoom, not an eased tween.
                const factor = evt.deltaY > 0 ? 1.15 : 1 / 1.15;
                const curLatSpan = ui.view.maxLat - ui.view.minLat;
                const curLonSpan = ui.view.maxLon - ui.view.minLon;
                const newLatSpan = Math.max(MIN_SPAN_DEG, Math.min(MAX_SPAN_DEG, curLatSpan * factor));
                const newLonSpan = Math.max(MIN_SPAN_DEG, Math.min(MAX_SPAN_DEG, curLonSpan * factor));
                // Keep the point under the cursor fixed on screen: it should
                // sit at the same fractional position within the new span.
                const fracX = px / canvas.width;
                const fracY = 1 - py / canvas.height;
                ui.view = {
                    minLon: anchorLon - fracX * newLonSpan,
                    maxLon: anchorLon + (1 - fracX) * newLonSpan,
                    minLat: anchorLat - fracY * newLatSpan,
                    maxLat: anchorLat + (1 - fracY) * newLatSpan,
                };
                savePersistedView(ui.view);
                redraw();
            },
            { passive: false }
        );

        let dragging = false;
        let dragStartPx = null;
        let dragStartView = null;
        let dragMoved = false;
        stack.addEventListener("mousedown", (evt) => {
            if (!ui.view) {
                return;
            }
            dragging = true;
            dragMoved = false;
            dragStartPx = [evt.clientX, evt.clientY];
            dragStartView = Object.assign({}, ui.view);
            stack.classList.add("map-dragging");
        });
        window.addEventListener("mousemove", (evt) => {
            if (!dragging) {
                return;
            }
            const rect = canvas.getBoundingClientRect();
            const dxPx = ((evt.clientX - dragStartPx[0]) / rect.width) * canvas.width;
            const dyPx = ((evt.clientY - dragStartPx[1]) / rect.height) * canvas.height;
            if (Math.hypot(dxPx, dyPx) > 3) {
                dragMoved = true;
            }
            const lonSpan = dragStartView.maxLon - dragStartView.minLon;
            const latSpan = dragStartView.maxLat - dragStartView.minLat;
            const dLon = -(dxPx / canvas.width) * lonSpan;
            const dLat = (dyPx / canvas.height) * latSpan;
            ui.view = {
                minLon: dragStartView.minLon + dLon,
                maxLon: dragStartView.maxLon + dLon,
                minLat: dragStartView.minLat + dLat,
                maxLat: dragStartView.maxLat + dLat,
            };
            redraw();
        });
        window.addEventListener("mouseup", (evt) => {
            if (!dragging) {
                return;
            }
            dragging = false;
            stack.classList.remove("map-dragging");
            savePersistedView(ui.view);
            // A mousedown/mouseup with negligible movement in between is a
            // click, not a pan -- select whichever aircraft marker (if any)
            // is under the cursor at its currently-drawn position.
            if (!dragMoved) {
                const cycle = currentCycleOrEmpty();
                if (cycle && ui.view) {
                    const [px, py] = toCanvasPx(evt.clientX, evt.clientY);
                    const project = makeProjector(ui.view, canvas.width, canvas.height);
                    const hit = findAircraftAtPixel(cycle, project, px, py, 14);
                    ui.selectedAircraftCallsign = hit === ui.selectedAircraftCallsign ? null : hit;
                    redraw();
                }
            }
        });

        stack.addEventListener("dblclick", () => {
            const cycle = currentCycleOrEmpty();
            ui.view = fitToDataView(cycle || { snapshot: { aircraft: [] }, prediction: { paths: {} }, regions_by_horizon: {}, sector_regions: {} });
            savePersistedView(ui.view);
            redraw();
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        setupTabs();
        setupHorizonScrubber();
        setupSpeedButtons();
        setupPauseResumeButton();
        setupManualClearanceForm();
        setupMapInteraction();
        loadPersistedUiPrefs();
        geoLayers.init().then(() => {
            applyPersistedLayerVisibility();
            setupGeoLayerToggles();
            // Prefer a fresh fit-to-FIR over whatever bounds an even-earlier
            // render used (e.g. the very first poll landing before geo
            // layers finished loading, which can only have fit to traffic) --
            // unless the operator already has a saved view, which wins.
            if (!loadPersistedView()) {
                ui.view = null;
            }
            if (window.__astraLastCycle) {
                renderMap(window.__astraLastCycle);
            }
        });
        requestAnimationFrame(animateTrafficOverlay);
        poll();
    });
})();