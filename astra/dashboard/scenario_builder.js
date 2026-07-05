/**
 * ASTRA Scenario Builder frontend.
 *
 * Talks only to `/scenario*` (astra.dashboard.scenario_routes) -- it
 * never reads `/state` and has no idea what the pipeline's tracks,
 * complexity scores, or resolution candidates look like. This page's
 * only job is editing the *input* traffic, not visualizing the
 * pipeline's output (that is the Operations HMI's job).
 */

(function () {
    "use strict";

    const POLL_MS = 1000;
    let editingCallsign = null; // non-null while the modal is editing (vs spawning)

    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------

    function clockFmt(seconds) {
        const total = Math.max(0, Math.round(seconds || 0));
        const h = String(Math.floor(total / 3600)).padStart(2, "0");
        const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
        const s = String(total % 60).padStart(2, "0");
        return `${h}:${m}:${s}`;
    }

    function toast(message, kind) {
        const el = document.getElementById("sb-toast");
        el.textContent = message;
        el.className = "sb-toast" + (kind ? ` sb-toast-${kind}` : "");
        el.classList.remove("hidden");
        clearTimeout(el._hideTimer);
        el._hideTimer = setTimeout(() => el.classList.add("hidden"), 2600);
    }

    async function api(path, options) {
        const response = await fetch(path, options);
        let body;
        try {
            body = await response.json();
        } catch (err) {
            body = { ok: false, error: "Malformed response from server." };
        }
        if (!response.ok || !body.ok) {
            throw new Error(body.error || `Request failed (${response.status})`);
        }
        return body;
    }

    // ------------------------------------------------------------------
    // Sim controls
    // ------------------------------------------------------------------

    async function control(action, extra) {
        try {
            await api("/scenario/control", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(Object.assign({ action }, extra || {})),
            });
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    function setupControls() {
        document.getElementById("sb-btn-pause-resume").addEventListener("click", () => {
            control(window.__sbRunning ? "pause" : "resume");
        });
        document.getElementById("sb-btn-step").addEventListener("click", () => control("step", { ticks: 1 }));
        document.getElementById("sb-btn-reset").addEventListener("click", () => {
            if (confirm("Reset will delete all aircraft and set the sim clock to zero. Continue?")) {
                control("reset");
            }
        });
    }

    // ------------------------------------------------------------------
    // Aircraft table
    // ------------------------------------------------------------------

    function aircraftRow(ac) {
        const fl = Math.round(ac.altitude_ft / 100);
        return `
            <tr data-callsign="${ac.callsign}">
                <td>${ac.callsign}</td>
                <td>${ac.aircraft_type}</td>
                <td><input type="number" step="0.0001" class="sb-field" data-field="lat" value="${ac.lat.toFixed(4)}"></td>
                <td><input type="number" step="0.0001" class="sb-field" data-field="lon" value="${ac.lon.toFixed(4)}"></td>
                <td><input type="number" step="1" class="sb-field" data-field="heading_deg" value="${Math.round(ac.heading_deg)}"></td>
                <td><input type="number" step="10" class="sb-field" data-field="altitude_ft" value="${Math.round(ac.altitude_ft)}"></td>
                <td><input type="number" step="1" class="sb-field" data-field="ground_speed_kt" value="${Math.round(ac.ground_speed_kt)}"></td>
                <td><input type="number" step="10" class="sb-field" data-field="vertical_speed_fpm" value="${Math.round(ac.vertical_speed_fpm)}"></td>
                <td class="sb-row-actions">
                    <button class="sb-btn sb-btn-small sb-btn-delete" title="Delete">&times;</button>
                </td>
            </tr>`;
    }

    function renderAircraftTable(aircraft) {
        const tbody = document.getElementById("sb-aircraft-tbody");
        if (aircraft.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="empty-row">No aircraft yet &mdash; spawn one or load a preset.</td></tr>';
            return;
        }
        tbody.innerHTML = aircraft.map(aircraftRow).join("");

        tbody.querySelectorAll("tr[data-callsign]").forEach((row) => {
            const callsign = row.dataset.callsign;
            row.querySelectorAll(".sb-field").forEach((input) => {
                input.addEventListener("change", () => {
                    const field = input.dataset.field;
                    const value = Number(input.value);
                    if (Number.isNaN(value)) {
                        return;
                    }
                    editAircraft(callsign, { [field]: value });
                });
            });
            row.querySelector(".sb-btn-delete").addEventListener("click", () => deleteAircraft(callsign));
        });
    }

    async function editAircraft(callsign, fields) {
        try {
            await api(`/scenario/aircraft/${encodeURIComponent(callsign)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(fields),
            });
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    async function deleteAircraft(callsign) {
        try {
            await api(`/scenario/aircraft/${encodeURIComponent(callsign)}`, { method: "DELETE" });
            toast(`Deleted ${callsign}`, "ok");
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    // ------------------------------------------------------------------
    // Spawn / edit modal
    // ------------------------------------------------------------------

    function openModal(prefill) {
        editingCallsign = prefill ? prefill.callsign : null;
        document.getElementById("sb-modal-title").textContent = editingCallsign ? `Edit ${editingCallsign}` : "Spawn aircraft";
        document.getElementById("sb-modal-submit").textContent = editingCallsign ? "Save" : "Spawn";
        document.getElementById("sb-f-callsign").value = prefill ? prefill.callsign : "";
        document.getElementById("sb-f-callsign").disabled = Boolean(editingCallsign);
        document.getElementById("sb-f-type").value = prefill ? prefill.aircraft_type : "A320";
        document.getElementById("sb-f-lat").value = prefill ? prefill.lat : 10.82;
        document.getElementById("sb-f-lon").value = prefill ? prefill.lon : 106.67;
        document.getElementById("sb-f-hdg").value = prefill ? Math.round(prefill.heading_deg) : 90;
        document.getElementById("sb-f-alt").value = prefill ? prefill.altitude_ft : 30000;
        document.getElementById("sb-f-spd").value = prefill ? prefill.ground_speed_kt : 280;
        document.getElementById("sb-modal-backdrop").classList.remove("hidden");
    }

    function closeModal() {
        document.getElementById("sb-modal-backdrop").classList.add("hidden");
        editingCallsign = null;
    }

    async function submitModal(evt) {
        evt.preventDefault();
        const payload = {
            callsign: document.getElementById("sb-f-callsign").value.trim(),
            aircraft_type: document.getElementById("sb-f-type").value.trim(),
            lat: Number(document.getElementById("sb-f-lat").value),
            lon: Number(document.getElementById("sb-f-lon").value),
            heading_deg: Number(document.getElementById("sb-f-hdg").value),
            altitude_ft: Number(document.getElementById("sb-f-alt").value),
            speed_kt: Number(document.getElementById("sb-f-spd").value),
        };
        try {
            if (editingCallsign) {
                await api(`/scenario/aircraft/${encodeURIComponent(editingCallsign)}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        aircraft_type: payload.aircraft_type,
                        lat: payload.lat,
                        lon: payload.lon,
                        heading_deg: payload.heading_deg,
                        altitude_ft: payload.altitude_ft,
                        ground_speed_kt: payload.speed_kt,
                    }),
                });
                toast(`Updated ${editingCallsign}`, "ok");
            } else {
                await api("/scenario/aircraft", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                toast(`Spawned ${payload.callsign}`, "ok");
            }
            closeModal();
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    function setupModal() {
        document.getElementById("sb-btn-add").addEventListener("click", () => openModal(null));
        document.getElementById("sb-modal-cancel").addEventListener("click", closeModal);
        document.getElementById("sb-modal-form").addEventListener("submit", submitModal);
        document.getElementById("sb-modal-backdrop").addEventListener("click", (evt) => {
            if (evt.target.id === "sb-modal-backdrop") {
                closeModal();
            }
        });
    }

    // ------------------------------------------------------------------
    // Mini map preview
    // ------------------------------------------------------------------

    function renderMiniMap(aircraft) {
        const canvas = document.getElementById("sb-map-canvas");
        const ctx = canvas.getContext("2d");
        const width = canvas.width;
        const height = canvas.height;
        ctx.clearRect(0, 0, width, height);

        ctx.strokeStyle = "#1c2732";
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

        if (aircraft.length === 0) {
            ctx.fillStyle = "#7c8a97";
            ctx.font = "12px monospace";
            ctx.fillText("No aircraft to preview", 12, 20);
            return;
        }

        const pad = 0.4;
        const lats = aircraft.map((a) => a.lat);
        const lons = aircraft.map((a) => a.lon);
        const minLat = Math.min(...lats) - pad;
        const maxLat = Math.max(...lats) + pad;
        const minLon = Math.min(...lons) - pad;
        const maxLon = Math.max(...lons) + pad;
        const latSpan = Math.max(maxLat - minLat, 0.1);
        const lonSpan = Math.max(maxLon - minLon, 0.1);
        const project = (lat, lon) => [
            ((lon - minLon) / lonSpan) * width,
            height - ((lat - minLat) / latSpan) * height,
        ];

        aircraft.forEach((ac) => {
            const [x, y] = project(ac.lat, ac.lon);
            const headingRad = (ac.heading_deg * Math.PI) / 180;
            ctx.save();
            ctx.translate(x, y);
            ctx.rotate(headingRad);
            ctx.beginPath();
            ctx.moveTo(0, -7);
            ctx.lineTo(4, 6);
            ctx.lineTo(-4, 6);
            ctx.closePath();
            ctx.fillStyle = "#35c3a3";
            ctx.fill();
            ctx.restore();

            ctx.fillStyle = "#d7e2ea";
            ctx.font = "10px monospace";
            ctx.fillText(`${ac.callsign} FL${Math.round(ac.altitude_ft / 100)}`, x + 8, y + 3);
        });
    }

    // ------------------------------------------------------------------
    // Presets
    // ------------------------------------------------------------------

    async function loadPresets() {
        try {
            const body = await api("/scenario/presets");
            const container = document.getElementById("sb-presets-list");
            container.innerHTML = body.presets
                .map(
                    (p) => `
                <div class="sb-preset-card" data-key="${p.key}">
                    <div class="sb-preset-label">${p.label}</div>
                    <div class="sb-preset-desc">${p.description}</div>
                    <div class="sb-preset-count">${p.aircraft_count} aircraft</div>
                </div>`
                )
                .join("");
            container.querySelectorAll(".sb-preset-card").forEach((card) => {
                card.addEventListener("click", () => loadPreset(card.dataset.key, card.querySelector(".sb-preset-label").textContent));
            });
        } catch (err) {
            toast(err.message, "error");
        }
    }

    async function loadPreset(key, label) {
        if (!confirm(`Load "${label}"? This resets the current scenario.`)) {
            return;
        }
        try {
            await api(`/scenario/presets/${encodeURIComponent(key)}/load`, { method: "POST" });
            toast(`Loaded preset: ${label}`, "ok");
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    // ------------------------------------------------------------------
    // Saved scenarios
    // ------------------------------------------------------------------

    async function loadSavedList() {
        try {
            const body = await api("/scenario/scenarios");
            const container = document.getElementById("sb-saved-list");
            if (body.scenarios.length === 0) {
                container.innerHTML = '<p class="panel-hint">No saved scenarios yet.</p>';
                return;
            }
            container.innerHTML = body.scenarios
                .map(
                    (name) => `
                <div class="sb-saved-row" data-name="${name}">
                    <span class="sb-saved-name">${name}</span>
                    <span class="sb-row-actions">
                        <button class="sb-btn sb-btn-small sb-btn-primary sb-load-scn">Load</button>
                        <button class="sb-btn sb-btn-small sb-btn-danger sb-delete-scn">Delete</button>
                    </span>
                </div>`
                )
                .join("");
            container.querySelectorAll(".sb-saved-row").forEach((row) => {
                const name = row.dataset.name;
                row.querySelector(".sb-load-scn").addEventListener("click", () => loadSavedScenario(name));
                row.querySelector(".sb-delete-scn").addEventListener("click", () => deleteSavedScenario(name));
            });
        } catch (err) {
            toast(err.message, "error");
        }
    }

    async function saveScenario() {
        const nameInput = document.getElementById("sb-save-name");
        const name = nameInput.value.trim();
        if (!name) {
            toast("Enter a scenario name first.", "error");
            return;
        }
        try {
            await api("/scenario/scenarios", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
            });
            toast(`Saved "${name}"`, "ok");
            nameInput.value = "";
            await loadSavedList();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    async function loadSavedScenario(name) {
        if (!confirm(`Load "${name}"? This resets the current scenario.`)) {
            return;
        }
        try {
            await api(`/scenario/scenarios/${encodeURIComponent(name)}/load`, { method: "POST" });
            toast(`Loaded "${name}"`, "ok");
            await refresh();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    async function deleteSavedScenario(name) {
        if (!confirm(`Delete saved scenario "${name}"?`)) {
            return;
        }
        try {
            await api(`/scenario/scenarios/${encodeURIComponent(name)}`, { method: "DELETE" });
            await loadSavedList();
        } catch (err) {
            toast(err.message, "error");
        }
    }

    // ------------------------------------------------------------------
    // Poll loop
    // ------------------------------------------------------------------

    async function refresh() {
        try {
            const body = await api("/scenario/state");
            window.__sbRunning = body.running;
            const badge = document.getElementById("sb-run-badge");
            badge.textContent = body.running ? "RUNNING" : "PAUSED";
            badge.className = "badge " + (body.running ? "badge-running" : "badge-waiting");
            document.getElementById("sb-btn-pause-resume").textContent = body.running ? "Pause" : "Resume";
            document.getElementById("sb-sim-time").textContent = "t = " + clockFmt(body.sim_time_s);
            document.getElementById("sb-aircraft-count").textContent = `${body.aircraft.length} aircraft`;
            renderAircraftTable(body.aircraft);
            renderMiniMap(body.aircraft);
        } catch (err) {
            // Most likely "requires --mock mode" -- surfaced once via the
            // banner already rendered server-side; avoid spamming toasts
            // on every poll tick.
        }
    }

    function poll() {
        refresh().finally(() => setTimeout(poll, POLL_MS));
    }

    document.addEventListener("DOMContentLoaded", () => {
        setupControls();
        setupModal();
        document.getElementById("sb-btn-save").addEventListener("click", saveScenario);
        loadPresets();
        loadSavedList();
        poll();
    });
})();
