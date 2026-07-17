"""
Scenario Builder HTTP routes.

Thesis goal 4: a page that can spawn/edit/delete aircraft, save/load
named scenarios, load predefined traffic situations, and pause/resume/
single-step/reset the simulation. This module is the write path onto
`StateReader` (mock mode only, see `StateReader.is_mock`) that makes
that page possible; `astra.dashboard.routes` (the read-only `/state`
poll endpoint the Operations HMI uses) is untouched.

Nothing here computes a prediction, cluster, complexity score, track,
or resolution candidate -- exactly like `astra.dashboard.routes`, this
module only mutates the traffic *input* the pipeline reads. All engine
math still lives in `astra.trajectory` / `astra.hotspot` / etc.

Every response is JSON: `{"ok": true, ...}` on success or
`{"ok": false, "error": "..."}` with a 4xx status on failure. Nothing
here ever raises past the route handler uncaught -- a bad request from
the builder UI (unknown callsign, bad field, mock-mode-only action
against a live BlueSky session) is reported as a normal JSON error, not
a 500.
"""

import json
import os
import re
from typing import Dict

from flask import Blueprint, Response, jsonify, render_template, request

from astra.dashboard import scenario_presets
from astra.interface.state_reader import StateReader

#: Only these characters may appear in a saved scenario's file-safe name.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Always required to spawn an aircraft, regardless of how its position
#: is determined (free-standing lat/lon, or an airway spawn).
_REQUIRED_CREATE_FIELDS_BASE = ("callsign", "aircraft_type", "altitude_ft", "speed_kt")

#: Required only for a free-standing spawn (no `airway_designator`).
_REQUIRED_POSITION_FIELDS = ("lat", "lon", "heading_deg")

#: Path to the static airways GeoJSON also used by the map's airway layer.
_AIRWAYS_PATH = os.path.join(os.path.dirname(__file__), "geo", "airways.json")

#: Lazily-populated cache of `_load_airways()`'s result -- the file never
#: changes at runtime, so there's no need to re-read/re-parse it per request.
_airways_cache = None


def _load_airways() -> list:
    """Read the static airways GeoJSON, reshaped for the Scenario Builder.

    Returns:
        `[{"designator", "waypoint_names", "coordinates": [{"lat", "lon"}, ...]}, ...]`,
        one entry per airway `LineString` feature.
    """
    global _airways_cache
    if _airways_cache is None:
        with open(_AIRWAYS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _airways_cache = [
            {
                "designator": feature.get("properties", {}).get("designator", "?"),
                "waypoint_names": feature.get("properties", {}).get("waypoints", []),
                "coordinates": [
                    {"lat": lat, "lon": lon}
                    for lon, lat in feature.get("geometry", {}).get("coordinates", [])
                ],
            }
            for feature in data.get("features", [])
        ]
    return _airways_cache


def _error(message: str, status: int = 400) -> Response:
    response = jsonify({"ok": False, "error": message})
    response.status_code = status
    return response


def _ok(payload: Dict) -> Response:
    payload = dict(payload)
    payload["ok"] = True
    return jsonify(payload)


def _scenario_path(scenarios_dir: str, name: str) -> str:
    return os.path.join(scenarios_dir, f"{name}.json")


def build_scenario_blueprint(reader: StateReader, scenarios_dir: str) -> Blueprint:
    """Build the Scenario Builder's Flask `Blueprint`.

    Args:
        reader: The same `StateReader` `main.py`'s poll loop reads from.
            Must be mock-backed (`reader.is_mock`) for any of these
            routes to succeed -- checked per-request, not at startup,
            so a helpful JSON error is returned rather than a crash if
            someone opens `/scenario` against a live BlueSky session.
        scenarios_dir: Directory saved scenarios are written to/read
            from as `<name>.json`. Created if it does not exist.

    Returns:
        A `flask.Blueprint` ready to register on the dashboard's app.
    """
    os.makedirs(scenarios_dir, exist_ok=True)
    blueprint = Blueprint("scenario", __name__)

    def _require_mock() -> bool:
        return reader.is_mock

    # ------------------------------------------------------------------
    # Page
    # ------------------------------------------------------------------

    @blueprint.route("/scenario")
    def scenario_page() -> str:
        """Serve the Scenario Builder page shell."""
        return render_template("scenario_builder.html", is_mock=reader.is_mock)

    # ------------------------------------------------------------------
    # Live state: current aircraft + sim clock/run status
    # ------------------------------------------------------------------

    @blueprint.route("/scenario/state")
    def scenario_state() -> Response:
        """Current aircraft + sim clock, read directly (bypasses the pipeline)."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        aircraft = sorted(reader.list_aircraft(), key=lambda a: a["callsign"])
        return _ok(
            {
                "aircraft": aircraft,
                "running": reader.is_simulation_running(),
                "sim_time_s": reader.simulation_time_s(),
            }
        )

    # ------------------------------------------------------------------
    # Aircraft CRUD
    # ------------------------------------------------------------------

    @blueprint.route("/scenario/airways")
    def airways() -> Response:
        """List airways aircraft can be spawned onto and follow."""
        return _ok({"airways": _load_airways()})

    @blueprint.route("/scenario/aircraft", methods=["POST"])
    def create_aircraft() -> Response:
        """Spawn one aircraft.

        Body: callsign, aircraft_type, altitude_ft, speed_kt, plus either
        lat/lon/heading_deg (free-standing spawn) or airway_designator
        (+ optional start_index) to spawn onto and follow a named airway.
        """
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        body = request.get_json(silent=True) or {}
        missing = [f for f in _REQUIRED_CREATE_FIELDS_BASE if f not in body]
        if missing:
            return _error(f"Missing field(s): {', '.join(missing)}")

        route_waypoints = None
        airway_designator = body.get("airway_designator")
        if airway_designator:
            airway = next(
                (a for a in _load_airways() if a["designator"] == airway_designator), None
            )
            if airway is None:
                return _error(f"Unknown airway '{airway_designator}'.", 404)
            coords = airway["coordinates"]
            if len(coords) < 2:
                return _error(f"Airway '{airway_designator}' has too few points to follow.")
            try:
                start_index = int(body.get("start_index", 0))
            except (TypeError, ValueError):
                return _error("start_index must be an integer.")
            start_index = max(0, min(start_index, len(coords) - 2))
            lat, lon = coords[start_index]["lat"], coords[start_index]["lon"]
            route_waypoints = [(p["lat"], p["lon"]) for p in coords[start_index + 1:]]
            heading_deg = body.get("heading_deg", 0.0)  # MockConnector overrides from the route
        else:
            missing_pos = [f for f in _REQUIRED_POSITION_FIELDS if f not in body]
            if missing_pos:
                return _error(f"Missing field(s): {', '.join(missing_pos)}")
            lat, lon, heading_deg = body["lat"], body["lon"], body["heading_deg"]

        try:
            reader.create_aircraft(
                callsign=str(body["callsign"]).strip().upper(),
                aircraft_type=str(body["aircraft_type"]).strip().upper(),
                lat=float(lat),
                lon=float(lon),
                heading_deg=float(heading_deg),
                altitude_ft=float(body["altitude_ft"]),
                speed_kt=float(body["speed_kt"]),
                route_waypoints=route_waypoints,
            )
        except (TypeError, ValueError) as exc:
            return _error(f"Invalid aircraft field: {exc}")
        return _ok({"callsign": body["callsign"], "on_route": route_waypoints is not None})

    @blueprint.route("/scenario/aircraft/<callsign>", methods=["PATCH"])
    def edit_aircraft(callsign: str) -> Response:
        """Edit one or more fields of an existing aircraft. Body: partial fields."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        body = request.get_json(silent=True) or {}
        editable = {
            "aircraft_type",
            "lat",
            "lon",
            "heading_deg",
            "altitude_ft",
            "ground_speed_kt",
            "vertical_speed_fpm",
        }
        fields = {k: v for k, v in body.items() if k in editable}
        if not fields:
            return _error("No editable fields in request body.")
        try:
            for key in ("lat", "lon", "heading_deg", "altitude_ft", "ground_speed_kt", "vertical_speed_fpm"):
                if key in fields:
                    fields[key] = float(fields[key])
        except (TypeError, ValueError) as exc:
            return _error(f"Invalid field value: {exc}")
        found = reader.update_aircraft(callsign, **fields)
        if not found:
            return _error(f"Unknown callsign '{callsign}'.", 404)
        return _ok({"callsign": callsign.upper()})

    @blueprint.route("/scenario/aircraft/<callsign>", methods=["DELETE"])
    def delete_aircraft(callsign: str) -> Response:
        """Delete one aircraft by callsign."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        reader.remove_aircraft(callsign)
        return _ok({"callsign": callsign.upper()})

    # ------------------------------------------------------------------
    # Simulation control: pause / resume / single-step / reset
    # ------------------------------------------------------------------

    @blueprint.route("/scenario/control", methods=["POST"])
    def control() -> Response:
        """Body: `{"action": "pause"|"resume"|"step"|"reset"}` (optional `"ticks"` for step)."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        if action == "pause":
            reader.send_command("HOLD")
        elif action == "resume":
            reader.send_command("OP")
        elif action == "step":
            ticks = int(body.get("ticks", 1))
            reader.step_simulation(ticks)
        elif action == "reset":
            reader.reset_simulation()
        else:
            return _error(f"Unknown action '{action}'. Expected pause/resume/step/reset.")
        return _ok({"action": action, "running": reader.is_simulation_running()})

    # ------------------------------------------------------------------
    # Predefined traffic situations
    # ------------------------------------------------------------------

    @blueprint.route("/scenario/presets")
    def presets() -> Response:
        """List predefined traffic situations (crossing, merge, arrival rush, ...)."""
        return _ok({"presets": scenario_presets.list_presets()})

    @blueprint.route("/scenario/presets/<key>/load", methods=["POST"])
    def load_preset(key: str) -> Response:
        """Reset the sim, then spawn a preset's aircraft. Leaves the sim paused."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        try:
            preset = scenario_presets.get_preset(key)
        except KeyError:
            return _error(f"Unknown preset '{key}'.", 404)
        reader.reset_simulation()
        for ac in preset["aircraft"]:
            reader.create_aircraft(
                callsign=ac["callsign"],
                aircraft_type=ac["aircraft_type"],
                lat=ac["lat"],
                lon=ac["lon"],
                heading_deg=ac["heading_deg"],
                altitude_ft=ac["altitude_ft"],
                speed_kt=ac["speed_kt"],
                route_waypoints=ac.get("route_waypoints"),
            )
        return _ok({"key": key, "aircraft_count": len(preset["aircraft"])})

    # ------------------------------------------------------------------
    # Save / load / list / delete user scenarios (JSON files on disk)
    # ------------------------------------------------------------------

    @blueprint.route("/scenario/scenarios")
    def list_scenarios() -> Response:
        """List saved scenario names (without the `.json` extension)."""
        names = sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(scenarios_dir)
            if f.endswith(".json")
        )
        return _ok({"scenarios": names})

    @blueprint.route("/scenario/scenarios", methods=["POST"])
    def save_scenario() -> Response:
        """Save the current aircraft set. Body: `{"name": "..."}`."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        body = request.get_json(silent=True) or {}
        name = str(body.get("name", "")).strip()
        if not _NAME_RE.match(name):
            return _error(
                "Scenario name must be 1-64 characters, letters/digits/underscore/hyphen only."
            )
        aircraft = reader.list_aircraft()
        with open(_scenario_path(scenarios_dir, name), "w", encoding="utf-8") as fh:
            json.dump({"name": name, "aircraft": aircraft}, fh, indent=2)
        return _ok({"name": name, "aircraft_count": len(aircraft)})

    @blueprint.route("/scenario/scenarios/<name>/load", methods=["POST"])
    def load_scenario(name: str) -> Response:
        """Reset the sim, then spawn a saved scenario's aircraft."""
        if not _require_mock():
            return _error("Scenario Builder requires --mock mode.", 409)
        if not _NAME_RE.match(name):
            return _error("Invalid scenario name.")
        path = _scenario_path(scenarios_dir, name)
        if not os.path.isfile(path):
            return _error(f"No saved scenario named '{name}'.", 404)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        reader.reset_simulation()
        for ac in data.get("aircraft", []):
            try:
                reader.create_aircraft(
                    callsign=ac["callsign"],
                    aircraft_type=ac["aircraft_type"],
                    lat=float(ac["lat"]),
                    lon=float(ac["lon"]),
                    heading_deg=float(ac["heading_deg"]),
                    altitude_ft=float(ac["altitude_ft"]),
                    speed_kt=float(ac["ground_speed_kt"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return _ok({"name": name, "aircraft_count": len(data.get("aircraft", []))})

    @blueprint.route("/scenario/scenarios/<name>", methods=["DELETE"])
    def delete_scenario(name: str) -> Response:
        """Delete a saved scenario file."""
        if not _NAME_RE.match(name):
            return _error("Invalid scenario name.")
        path = _scenario_path(scenarios_dir, name)
        if os.path.isfile(path):
            os.remove(path)
        return _ok({"name": name})

    return blueprint
