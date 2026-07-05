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

    // Session-only UI state. Never sent to the backend; resets on reload.
    const ui = {
        selectedArhacId: null,
        selectedCandidateIndex: {}, // arhac_id -> candidate index being previewed
        lifecycle: {}, // arhac_id -> "DRAFT" | "PROPOSED" | "ACKNOWLEDGED" | "CANCELED"
        selectedHorizon: 0,
        availableHorizons: [0],
    };

    const LIFECYCLE_STAGES = ["DRAFT", "PROPOSED", "ACKNOWLEDGED", "CANCELED"];

    // ------------------------------------------------------------------
    // Small shared helpers
    // ------------------------------------------------------------------

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

    function setupCoordinationToggle() {
        const toggle = document.getElementById("coordination-toggle");
        const body = document.getElementById("coordination-body");
        toggle.addEventListener("click", () => {
            body.classList.toggle("hidden");
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
    // Horizon scrubber
    // ------------------------------------------------------------------

    function setupHorizonScrubber() {
        const range = document.getElementById("horizon-range");
        range.addEventListener("input", () => {
            const idx = Number(range.value);
            ui.selectedHorizon = ui.availableHorizons[idx] !== undefined ? ui.availableHorizons[idx] : 0;
            document.getElementById("horizon-value").textContent = horizonLabel(ui.selectedHorizon);
            if (window.__astraLastCycle) {
                renderMap(window.__astraLastCycle);
            }
        });
    }

    function syncHorizonScrubber(cycle) {
        const horizons = Object.keys(cycle.regions_by_horizon)
            .map(Number)
            .sort((a, b) => a - b);
        ui.availableHorizons = horizons.length > 0 ? horizons : [0];
        const range = document.getElementById("horizon-range");
        range.min = 0;
        range.max = ui.availableHorizons.length - 1;
        const currentIdx = ui.availableHorizons.indexOf(ui.selectedHorizon);
        if (currentIdx === -1) {
            ui.selectedHorizon = ui.availableHorizons[0];
            range.value = 0;
        } else {
            range.value = currentIdx;
        }
        document.getElementById("horizon-value").textContent = horizonLabel(ui.selectedHorizon);
    }

    // ------------------------------------------------------------------
    // Map panel (plan view: traffic at scrubbed horizon + full predicted paths)
    // ------------------------------------------------------------------

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
            ctx.font = "10px monospace";
            ctx.fillText(name, cx - radiusPx + 4, cy - radiusPx + 12);
        });
    }

    function drawComplexityRegions(ctx, project, bounds, width, regions) {
        (regions || []).forEach((region) => {
            const [cx, cy] = project(region.cluster.centroid_lat, region.cluster.centroid_lon);
            const degPerNm = (bounds.maxLon - bounds.minLon) / 60;
            const radiusPx = Math.max(
                18,
                (region.cluster.horizontal_extent_nm * degPerNm * width) /
                    (bounds.maxLon - bounds.minLon || 1)
            );
            const color = complexityColor(region.complexity_score);
            ctx.beginPath();
            ctx.fillStyle = color.replace("rgb", "rgba").replace(")", ", 0.18)");
            ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
            ctx.stroke();
        });
    }

    function drawFaintPredictedPaths(ctx, project, cycle) {
        ctx.setLineDash([3, 5]);
        ctx.lineWidth = 1;
        Object.entries(cycle.prediction.paths).forEach(([callsign, points]) => {
            const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign);
            if (!observed || points.length === 0) {
                return;
            }
            ctx.beginPath();
            ctx.strokeStyle = "rgba(74, 144, 164, 0.35)";
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

    /** Draw traffic *at the scrubbed horizon*: observed triangle markers at
     * horizon 0, plain position markers (no heading data) at future horizons. */
    function drawScrubbedTraffic(ctx, project, cycle, horizonMin) {
        if (horizonMin === 0) {
            cycle.snapshot.aircraft.forEach((ac) => {
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
                ctx.font = "11px monospace";
                ctx.fillText(`${ac.callsign} FL${Math.round(ac.altitude_ft / 100)}`, x + 8, y + 3);
            });
            return;
        }
        Object.entries(cycle.prediction.paths).forEach(([callsign, points]) => {
            const atHorizon = points.find((p) => p.horizon_min === horizonMin);
            if (!atHorizon) {
                return;
            }
            const [x, y] = project(atHorizon.lat, atHorizon.lon);
            ctx.beginPath();
            ctx.fillStyle = "#e0a63c";
            ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = "#d7e2ea";
            ctx.font = "11px monospace";
            ctx.fillText(
                `${callsign} FL${Math.round(atHorizon.altitude_ft / 100)}`,
                x + 8,
                y + 3
            );
        });
    }

    function renderMap(cycle) {
        const canvas = document.getElementById("map-canvas");
        const ctx = canvas.getContext("2d");
        const width = canvas.width;
        const height = canvas.height;
        ctx.clearRect(0, 0, width, height);

        const bounds = computeBounds(cycle);
        const project = makeProjector(bounds, width, height);

        drawGrid(ctx, width, height);
        drawSectorBoundaries(ctx, project, bounds, width, cycle.sector_regions);
        const regionsAtHorizon = cycle.regions_by_horizon[String(ui.selectedHorizon)] || [];
        drawComplexityRegions(ctx, project, bounds, width, regionsAtHorizon);
        drawFaintPredictedPaths(ctx, project, cycle);
        drawScrubbedTraffic(ctx, project, cycle, ui.selectedHorizon);
    }

    // ------------------------------------------------------------------
    // Alerts table
    // ------------------------------------------------------------------

    function onsetClass(onsetInS) {
        if (onsetInS === null) {
            return "";
        }
        if (onsetInS <= 300) {
            return "onset-soon";
        }
        if (onsetInS <= 900) {
            return "onset-near";
        }
        return "onset-far";
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
                const onsetLabel = onsetInS === null ? "-" : `${Math.round(onsetInS / 60)} min`;
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
    // Event & Dissipation panel
    // ------------------------------------------------------------------

    function confidenceRingSvg(value, size) {
        const r = size / 2 - 6;
        const c = 2 * Math.PI * r;
        const pct = Math.max(0, Math.min(1, value === null || value === undefined ? 0 : value));
        const color = confidenceColor(pct);
        return `
            <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#1c2732" stroke-width="6" />
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6"
                    stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct)}"
                    stroke-linecap="round" transform="rotate(-90 ${size / 2} ${size / 2})" />
            </svg>`;
    }

    function renderComplexityReduction(track, candidate) {
        const container = document.getElementById("complexity-reduction");
        const before = candidate ? candidate.complexity_before : track.current_complexity_score;
        const after = candidate ? candidate.complexity_after : null;
        const afterKnown = after !== null && after !== undefined;
        const afterClass = afterKnown && after > before ? "cr-after-up" : "cr-after-down";
        container.innerHTML = `
            <div class="confidence-ring-wrap">
                ${confidenceRingSvg(track.confidence, 74)}
                <div class="confidence-ring-value">${
                    track.confidence === null ? "-" : Math.round(track.confidence * 100) + "%"
                }</div>
            </div>
            <div>
                <div class="confidence-ring-caption">Confidence</div>
                <div class="complexity-scores">
                    <div class="cr-row">
                        <span class="cr-label">Complexity</span>
                    </div>
                    <div class="cr-row">
                        <span class="cr-before">${fmt(before)}</span>
                        <span class="cr-arrow">&rarr;</span>
                        <span class="${afterClass}">${afterKnown ? fmt(after) : "-"}</span>
                    </div>
                </div>
            </div>`;
    }

    function lifecycleButtons(arhacId) {
        const current = ui.lifecycle[arhacId] || "DRAFT";
        return `
            <div class="lifecycle-row">
                ${LIFECYCLE_STAGES.map(
                    (stage) =>
                        `<button class="lifecycle-btn ${stage === current ? "current" : ""}" data-stage="${stage}">${stage}</button>`
                ).join("")}
            </div>`;
    }

    function renderCandidateList(rs, onSelectCandidate) {
        const container = document.getElementById("candidate-list");
        if (!rs || rs.candidates.length === 0) {
            container.innerHTML = '<p class="panel-hint">No eligible resolution candidates this cycle.</p>';
            return;
        }
        const activeIdx = ui.selectedCandidateIndex[rs.arhac_id] || 0;
        container.innerHTML =
            `<div class="panel-hint" style="margin-bottom:6px;">Ranked candidates (evaluated at +${rs.evaluated_horizon_min} min)</div>` +
            rs.candidates
                .map((c, idx) => {
                    const scoreClass = c.resolution_score >= 0 ? "cand-score-positive" : "cand-score-negative";
                    const sign = c.delta_value >= 0 ? "+" : "";
                    const active = idx === activeIdx ? "active" : "";
                    return `
                <div class="candidate-row ${active}" data-idx="${idx}">
                    <span class="cand-type">${c.clearance_type}</span>
                    <span>${c.target_callsign}</span>
                    <span>${sign}${fmt(c.delta_value, 0)}</span>
                    <span class="${scoreClass}">score ${fmt(c.resolution_score, 2)}</span>
                    ${idx === 0 ? lifecycleButtons(rs.arhac_id) : ""}
                </div>`;
                })
                .join("");
        container.querySelectorAll(".candidate-row").forEach((row) => {
            row.addEventListener("click", (evt) => {
                if (evt.target.classList.contains("lifecycle-btn")) {
                    return;
                }
                onSelectCandidate(Number(row.dataset.idx));
            });
        });
        container.querySelectorAll(".lifecycle-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                ui.lifecycle[rs.arhac_id] = btn.dataset.stage;
                renderCandidateList(rs, onSelectCandidate);
            });
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

        renderComplexityReduction(track, candidate);
        renderCandidateList(rs, (idx) => {
            ui.selectedCandidateIndex[track.arhac_id] = idx;
            renderEventPanel(cycle);
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
        }
    }

    function render(payload) {
        renderHeader(payload);
        if (!payload.has_data) {
            return;
        }
        const cycle = payload.cycle;
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

    document.addEventListener("DOMContentLoaded", () => {
        setupTabs();
        setupCoordinationToggle();
        setupHorizonScrubber();
        poll();
    });
})();