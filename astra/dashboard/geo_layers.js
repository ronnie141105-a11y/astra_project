/**
 * ASTRA pluggable geographic overlay layers.
 *
 * Loads `geo/manifest.json` (a list of named layers: FIRs, sectors,
 * airways, waypoints, airports, coastlines) plus each layer's GeoJSON
 * file, and draws them onto any canvas 2D context given a lat/lon ->
 * pixel `project(lat, lon) -> [x, y]` function.
 *
 * This module has NO geographic knowledge baked in -- no coordinates,
 * no country/FIR names, nothing. Every layer file starts out an empty
 * `FeatureCollection`; converting the Vietnam AIP into these files
 * later is the *only* step needed to put real geometry on the map. If
 * you are looking for where Vietnam's FIR/sector polygons should go,
 * it is `geo/firs.json` / `geo/sectors.json`, not this file.
 *
 * Used by both the Operations map and (once built) the Dissipation
 * Workspace's Traffic Projection Display -- one shared instance,
 * created once in `dashboard.js`, so geo overlays are never drawn by
 * two different code paths.
 */

class GeoLayerManager {
    /**
     * @param {string} manifestUrl - Path to the layer manifest JSON.
     */
    constructor(manifestUrl) {
        this.manifestUrl = manifestUrl || "/static/geo/manifest.json";
        this.layers = []; // [{id, label, kind, style, labelField, zIndex, visible, geojson}]
        this.ready = false;
    }

    /** Fetch the manifest and every layer file it references. Safe to call once. */
    async init() {
        try {
            const manifestResponse = await fetch(this.manifestUrl);
            const manifest = await manifestResponse.json();
            const base = this.manifestUrl.replace(/manifest\.json$/, "");
            const entries = await Promise.all(
                (manifest.layers || []).map(async (entry) => {
                    let geojson = { type: "FeatureCollection", features: [] };
                    try {
                        const response = await fetch(base + entry.file.replace(/^geo\//, ""));
                        geojson = await response.json();
                    } catch (err) {
                        console.warn(`GeoLayerManager: could not load layer '${entry.id}' (${entry.file}); drawing it empty.`, err);
                    }
                    return {
                        id: entry.id,
                        label: entry.label,
                        kind: entry.kind,
                        style: entry.style || {},
                        labelField: entry.label_field || null,
                        zIndex: entry.z_index || 0,
                        visible: entry.default_visible !== false,
                        geojson,
                    };
                })
            );
            this.layers = entries.sort((a, b) => a.zIndex - b.zIndex);
        } catch (err) {
            console.warn("GeoLayerManager: manifest failed to load; map will render with no geo overlays.", err);
            this.layers = [];
        }
        this.ready = true;
        return this.layers;
    }

    /** `[{id, label, kind, visible}]` for a layer-toggle control. */
    getToggleList() {
        return this.layers.map((l) => ({ id: l.id, label: l.label, kind: l.kind, visible: l.visible }));
    }

    setVisible(id, visible) {
        const layer = this.layers.find((l) => l.id === id);
        if (layer) {
            layer.visible = visible;
        }
    }

    isEmpty() {
        return this.layers.every((l) => (l.geojson.features || []).length === 0);
    }

    /**
     * Draw every visible layer, in ascending z-index order, onto `ctx`.
     *
     * @param {CanvasRenderingContext2D} ctx
     * @param {(lat: number, lon: number) => [number, number]} project
     */
    draw(ctx, project) {
        this.layers.forEach((layer) => {
            if (!layer.visible) {
                return;
            }
            const features = layer.geojson.features || [];
            features.forEach((feature) => this._drawFeature(ctx, project, layer, feature));
        });
    }

    _drawFeature(ctx, project, layer, feature) {
        const geometry = feature.geometry;
        if (!geometry) {
            return;
        }
        const label = layer.labelField ? feature.properties && feature.properties[layer.labelField] : null;
        switch (geometry.type) {
            case "Polygon":
                this._drawPolygon(ctx, project, layer.style, geometry.coordinates, label);
                break;
            case "MultiPolygon":
                geometry.coordinates.forEach((poly) => this._drawPolygon(ctx, project, layer.style, poly, label));
                break;
            case "LineString":
                this._drawLine(ctx, project, layer.style, geometry.coordinates);
                break;
            case "MultiLineString":
                geometry.coordinates.forEach((line) => this._drawLine(ctx, project, layer.style, line));
                break;
            case "Point":
                this._drawPoint(ctx, project, layer.style, geometry.coordinates, label);
                break;
            case "MultiPoint":
                geometry.coordinates.forEach((pt) => this._drawPoint(ctx, project, layer.style, pt, label));
                break;
            default:
                break; // Unsupported geometry kind: draw nothing rather than guess.
        }
    }

    /** GeoJSON polygon coordinates: `[ring, ...]`, each ring `[[lon, lat], ...]`, first ring = exterior. */
    _drawPolygon(ctx, project, style, rings, label) {
        if (!rings || rings.length === 0) {
            return;
        }
        ctx.beginPath();
        rings.forEach((ring) => {
            ring.forEach(([lon, lat], idx) => {
                const [x, y] = project(lat, lon);
                if (idx === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            });
            ctx.closePath();
        });
        if (style.fill) {
            ctx.fillStyle = style.fill;
            ctx.fill();
        }
        ctx.setLineDash(style.dash || []);
        ctx.strokeStyle = style.stroke || "#4a5866";
        ctx.lineWidth = style.width || 1;
        ctx.stroke();
        ctx.setLineDash([]);
        if (label) {
            const [lon0, lat0] = rings[0][0];
            const [lx, ly] = project(lat0, lon0);
            ctx.fillStyle = style.stroke || "#8494a2";
            ctx.font = "10px monospace";
            ctx.fillText(label, lx + 4, ly - 4);
        }
    }

    _drawLine(ctx, project, style, points) {
        if (!points || points.length === 0) {
            return;
        }
        ctx.beginPath();
        points.forEach(([lon, lat], idx) => {
            const [x, y] = project(lat, lon);
            if (idx === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.setLineDash(style.dash || []);
        ctx.strokeStyle = style.stroke || "#33424e";
        ctx.lineWidth = style.width || 1;
        ctx.stroke();
        ctx.setLineDash([]);
    }

    _drawPoint(ctx, project, style, coord, label) {
        const [lon, lat] = coord;
        const [x, y] = project(lat, lon);
        const size = style.size || 3;
        ctx.fillStyle = style.fill || "#d7e2ea";
        switch (style.marker) {
            case "star":
                this._drawStar(ctx, x, y, size);
                break;
            case "square":
                ctx.fillRect(x - size, y - size, size * 2, size * 2);
                break;
            default:
                ctx.beginPath();
                ctx.arc(x, y, size, 0, Math.PI * 2);
                ctx.fill();
                break;
        }
        if (label) {
            ctx.fillStyle = style.fill || "#8494a2";
            ctx.font = "9px monospace";
            ctx.fillText(label, x + size + 3, y + 3);
        }
    }

    _drawStar(ctx, cx, cy, size) {
        const spikes = 5;
        const outer = size;
        const inner = size / 2.2;
        ctx.beginPath();
        for (let i = 0; i < spikes * 2; i++) {
            const r = i % 2 === 0 ? outer : inner;
            const angle = (Math.PI / spikes) * i - Math.PI / 2;
            const x = cx + r * Math.cos(angle);
            const y = cy + r * Math.sin(angle);
            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }
        ctx.closePath();
        ctx.fill();
    }
}

window.GeoLayerManager = GeoLayerManager;
