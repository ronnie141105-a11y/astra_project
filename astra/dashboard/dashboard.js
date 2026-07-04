/**
 * ASTRA dashboard frontend (Milestone 8).
 *
 * Polls the `/state` endpoint on an interval matching `poll_interval_s`
 * (design review OQ-5(B) -- the value itself comes from the server via
 * `window.ASTRA_POLL_INTERVAL_S`, never hard-coded here) and re-renders
 * four panels: the plan-view traffic/prediction map, the 4DARHAC hotspot
 * table, the onset/peak/dissipation timeline, and the ranked resolution
 * candidates. Nothing in this file computes a prediction, a cluster, a
 * complexity score, a track, or a resolution candidate -- it only draws
 * what the backend already computed and serialized.
 */

(function () {
    "use strict";

    const POLL_INTERVAL_MS = Math.max(250, window.ASTRA_POLL_INTERVAL_S * 1000);

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
            timeEl.textContent = "t = " + fmt(payload.updated_at_s, 0) + "s";
        } else {
            badge.textContent = "WAITING";
            badge.className = "badge badge-waiting";
            timeEl.textContent = "t = \u2013";
        }
        cycleEl.textContent = "cycle " + payload.cycle_count;
    }

    // ------------------------------------------------------------------
    // Map panel (plan view: observed traffic + predicted paths + heatmap)
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
        (cycle.regions_by_horizon["0"] || []).forEach((region) => {
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

    function renderMap(cycle) {
        const canvas = document.getElementById("map-canvas");
        const ctx = canvas.getContext("2d");
        const width = canvas.width;
        const height = canvas.height;
        ctx.clearRect(0, 0, width, height);

        const bounds = computeBounds(cycle);
        const project = makeProjector(bounds, width, height);

        // Background grid.
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

        // Complexity heatmap: observed (horizon 0) regions, per design
        // review OQ-4(A) -- live-only, no accumulated history.
        (cycle.regions_by_horizon["0"] || []).forEach((region) => {
            const [cx, cy] = project(region.cluster.centroid_lat, region.cluster.centroid_lon);
            const degPerNm = (bounds.maxLon - bounds.minLon) / 60; // rough local scale
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

        // Predicted trajectories: one dashed polyline per aircraft.
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        Object.entries(cycle.prediction.paths).forEach(([callsign, points]) => {
            const observed = cycle.snapshot.aircraft.find((ac) => ac.callsign === callsign);
            if (!observed || points.length === 0) {
                return;
            }
            ctx.beginPath();
            ctx.strokeStyle = "#4a90a4";
            const [sx, sy] = project(observed.lat, observed.lon);
            ctx.moveTo(sx, sy);
            points.forEach((p) => {
                const [px, py] = project(p.lat, p.lon);
                ctx.lineTo(px, py);
            });
            ctx.stroke();
        });
        ctx.setLineDash([]);

        // Observed aircraft: a heading-oriented marker + callsign/altitude label.
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
            ctx.fillText(
                `${ac.callsign} FL${Math.round(ac.altitude_ft / 100)}`,
                x + 8,
                y + 3
            );
        });
    }

    // ------------------------------------------------------------------
    // Tracks table
    // ------------------------------------------------------------------

    function statusPill(status) {
        return `<span class="status-pill status-${status}">${status}</span>`;
    }

    function bestClearanceLabel(arhacId, resolutionByTrack) {
        const rs = resolutionByTrack[arhacId];
        if (!rs || rs.candidates.length === 0) {
            return "-";
        }
        const best = rs.candidates[0];
        const sign = best.delta_value >= 0 ? "+" : "";
        return `${best.clearance_type} ${best.target_callsign} (${sign}${fmt(best.delta_value, 0)}, score=${fmt(best.resolution_score, 2)})`;
    }

    function renderTracksTable(tracks, resolutionByTrack) {
        const tbody = document.getElementById("tracks-tbody");
        if (tracks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No open tracks.</td></tr>';
            return;
        }
        const sorted = [...tracks].sort((a, b) => {
            const ar = a.forecast_urgency_rank === null ? Infinity : a.forecast_urgency_rank;
            const br = b.forecast_urgency_rank === null ? Infinity : b.forecast_urgency_rank;
            if (ar !== br) {
                return ar - br;
            }
            return a.priority - b.priority;
        });
        tbody.innerHTML = sorted
            .map(
                (t) => `
            <tr>
                <td>${t.arhac_id.slice(0, 8)}</td>
                <td>${statusPill(t.status)}</td>
                <td>${t.forecast_urgency_rank === null ? "-" : t.forecast_urgency_rank}</td>
                <td>${t.priority}</td>
                <td>${fmt(t.peak_complexity)}</td>
                <td>${fmt(t.confidence, 2)}</td>
                <td>${t.member_aircraft.join(", ")}</td>
                <td>${bestClearanceLabel(t.arhac_id, resolutionByTrack)}</td>
            </tr>`
            )
            .join("");
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
        const width = 640;
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
    // Resolution panel
    // ------------------------------------------------------------------

    function renderResolutions(resolutionSets) {
        const container = document.getElementById("resolutions-list");
        if (resolutionSets.length === 0) {
            container.innerHTML = '<p class="panel-hint">No eligible tracks this cycle.</p>';
            return;
        }
        container.innerHTML = resolutionSets.map((rs) => renderResolutionBlock(rs)).join("");
    }

    function renderResolutionBlock(rs) {
        if (rs.candidates.length === 0) {
            return `
                <div class="resolution-block">
                    <div class="resolution-header">ARHAC ${rs.arhac_id.slice(0, 8)} &mdash; no candidates</div>
                </div>`;
        }
        const rows = rs.candidates
            .map((c) => {
                const scoreClass = c.resolution_score >= 0 ? "score-positive" : "score-negative";
                const sign = c.delta_value >= 0 ? "+" : "";
                return `
                <tr>
                    <td>${c.clearance_type}</td>
                    <td>${c.target_callsign}</td>
                    <td>${sign}${fmt(c.delta_value, 0)}</td>
                    <td>${fmt(c.complexity_before)} &rarr; ${c.complexity_after === null ? "-" : fmt(c.complexity_after)}</td>
                    <td class="${scoreClass}">${fmt(c.resolution_score, 3)}</td>
                </tr>`;
            })
            .join("");
        return `
            <div class="resolution-block">
                <div class="resolution-header">
                    ARHAC ${rs.arhac_id.slice(0, 8)} &mdash; evaluated at +${rs.evaluated_horizon_min} min
                </div>
                <table>
                    <thead>
                        <tr><th>Type</th><th>Target</th><th>Delta</th><th>Complexity</th><th>Score</th></tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    }

    // ------------------------------------------------------------------
    // Poll loop
    // ------------------------------------------------------------------

    function render(payload) {
        renderHeader(payload);
        if (!payload.has_data) {
            return;
        }
        const cycle = payload.cycle;
        const resolutionByTrack = {};
        cycle.resolution_sets.forEach((rs) => {
            resolutionByTrack[rs.arhac_id] = rs;
        });
        renderMap(cycle);
        renderTracksTable(cycle.tracks, resolutionByTrack);
        renderTimeline(cycle.tracks);
        renderResolutions(cycle.resolution_sets);
    }

    function poll() {
        fetch("/state")
            .then((response) => response.json())
            .then(render)
            .catch((err) => console.error("ASTRA dashboard: /state fetch failed", err))
            .finally(() => setTimeout(poll, POLL_INTERVAL_MS));
    }

    document.addEventListener("DOMContentLoaded", poll);
})();
