"""
Predefined traffic-situation templates for the Scenario Builder page
(thesis goal 4: "choose predefined traffic situations (crossing, merge,
arrival rush, etc.)").

Each preset is a plain, JSON-safe dict -- no domain objects -- so
`scenario_routes.py` can hand it straight to `StateReader.create_aircraft()`
in a loop without importing anything from `astra.interface`. Coordinates
are anchored loosely around the Ho Chi Minh FIR (~10.8N, 106.7E) at
typical en-route levels/speeds; they are illustrative traffic geometry
for demoing the pipeline, not real published waypoints or airways.
"""

from typing import Dict, List, TypedDict


class PresetAircraft(TypedDict):
    callsign: str
    aircraft_type: str
    lat: float
    lon: float
    heading_deg: float
    altitude_ft: float
    speed_kt: float


class Preset(TypedDict):
    key: str
    label: str
    description: str
    aircraft: List[PresetAircraft]


_CENTER_LAT = 10.82
_CENTER_LON = 106.67

PRESETS: Dict[str, Preset] = {
    "crossing": {
        "key": "crossing",
        "label": "Crossing traffic",
        "description": (
            "Two aircraft on perpendicular tracks converging on the same "
            "point and altitude -- the classic single conflict pair."
        ),
        "aircraft": [
            {
                "callsign": "HVN101",
                "aircraft_type": "A321",
                "lat": _CENTER_LAT,
                "lon": _CENTER_LON - 1.2,
                "heading_deg": 90.0,
                "altitude_ft": 34000,
                "speed_kt": 460,
            },
            {
                "callsign": "VJC202",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT - 1.2,
                "lon": _CENTER_LON,
                "heading_deg": 0.0,
                "altitude_ft": 34000,
                "speed_kt": 450,
            },
        ],
    },
    "merge": {
        "key": "merge",
        "label": "Merging streams",
        "description": (
            "Three aircraft converging from different headings onto "
            "roughly the same point and level -- a small merge hotspot."
        ),
        "aircraft": [
            {
                "callsign": "HVN301",
                "aircraft_type": "A359",
                "lat": _CENTER_LAT + 1.0,
                "lon": _CENTER_LON - 1.0,
                "heading_deg": 135.0,
                "altitude_ft": 36000,
                "speed_kt": 470,
            },
            {
                "callsign": "VJC402",
                "aircraft_type": "A321",
                "lat": _CENTER_LAT + 1.0,
                "lon": _CENTER_LON + 1.0,
                "heading_deg": 225.0,
                "altitude_ft": 36000,
                "speed_kt": 460,
            },
            {
                "callsign": "PIC503",
                "aircraft_type": "B789",
                "lat": _CENTER_LAT - 1.4,
                "lon": _CENTER_LON,
                "heading_deg": 0.0,
                "altitude_ft": 36000,
                "speed_kt": 480,
            },
        ],
    },
    "arrival_rush": {
        "key": "arrival_rush",
        "label": "Arrival rush",
        "description": (
            "Five inbound aircraft descending toward the same terminal "
            "area from different directions -- a sustained, multi-aircraft "
            "hotspot rather than a single pair."
        ),
        "aircraft": [
            {
                "callsign": "HVN601",
                "aircraft_type": "A321",
                "lat": _CENTER_LAT + 1.6,
                "lon": _CENTER_LON - 0.4,
                "heading_deg": 200.0,
                "altitude_ft": 24000,
                "speed_kt": 340,
            },
            {
                "callsign": "VJC602",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT + 1.3,
                "lon": _CENTER_LON + 1.1,
                "heading_deg": 230.0,
                "altitude_ft": 22000,
                "speed_kt": 330,
            },
            {
                "callsign": "PIC603",
                "aircraft_type": "B789",
                "lat": _CENTER_LAT - 1.5,
                "lon": _CENTER_LON - 1.2,
                "heading_deg": 40.0,
                "altitude_ft": 26000,
                "speed_kt": 350,
            },
            {
                "callsign": "AXJ604",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT - 1.7,
                "lon": _CENTER_LON + 0.8,
                "heading_deg": 320.0,
                "altitude_ft": 23000,
                "speed_kt": 335,
            },
            {
                "callsign": "HVN605",
                "aircraft_type": "A359",
                "lat": _CENTER_LAT,
                "lon": _CENTER_LON + 2.0,
                "heading_deg": 270.0,
                "altitude_ft": 25000,
                "speed_kt": 345,
            },
        ],
    },
    "head_on": {
        "key": "head_on",
        "label": "Head-on pair",
        "description": (
            "Two aircraft on reciprocal headings at the same level -- "
            "tests onset/urgency timing on a fast-closing geometry."
        ),
        "aircraft": [
            {
                "callsign": "HVN701",
                "aircraft_type": "A321",
                "lat": _CENTER_LAT,
                "lon": _CENTER_LON - 1.8,
                "heading_deg": 90.0,
                "altitude_ft": 35000,
                "speed_kt": 470,
            },
            {
                "callsign": "VJC702",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT,
                "lon": _CENTER_LON + 1.8,
                "heading_deg": 270.0,
                "altitude_ft": 35000,
                "speed_kt": 465,
            },
        ],
    },
    "parallel_overtake": {
        "key": "parallel_overtake",
        "label": "Parallel overtake",
        "description": (
            "Same track, same level, one aircraft faster than the other -- "
            "a slow-building conflict that should show a gentle onset ramp."
        ),
        "aircraft": [
            {
                "callsign": "HVN801",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT - 1.5,
                "lon": _CENTER_LON - 1.5,
                "heading_deg": 45.0,
                "altitude_ft": 33000,
                "speed_kt": 420,
            },
            {
                "callsign": "PIC802",
                "aircraft_type": "B789",
                "lat": _CENTER_LAT - 1.9,
                "lon": _CENTER_LON - 1.9,
                "heading_deg": 45.0,
                "altitude_ft": 33000,
                "speed_kt": 490,
            },
        ],
    },
    "free_flow": {
        "key": "free_flow",
        "label": "Free flow (light)",
        "description": (
            "Four aircraft on divergent, non-conflicting tracks -- a "
            "quiet baseline scene with no expected hotspot, useful for "
            "confirming the dashboard stays calm when it should."
        ),
        "aircraft": [
            {
                "callsign": "HVN901",
                "aircraft_type": "A321",
                "lat": _CENTER_LAT + 0.6,
                "lon": _CENTER_LON - 1.6,
                "heading_deg": 60.0,
                "altitude_ft": 31000,
                "speed_kt": 440,
            },
            {
                "callsign": "VJC902",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT - 0.8,
                "lon": _CENTER_LON + 1.4,
                "heading_deg": 240.0,
                "altitude_ft": 29000,
                "speed_kt": 430,
            },
            {
                "callsign": "PIC903",
                "aircraft_type": "B789",
                "lat": _CENTER_LAT + 1.7,
                "lon": _CENTER_LON + 1.2,
                "heading_deg": 160.0,
                "altitude_ft": 37000,
                "speed_kt": 480,
            },
            {
                "callsign": "AXJ904",
                "aircraft_type": "A320",
                "lat": _CENTER_LAT - 1.6,
                "lon": _CENTER_LON - 0.9,
                "heading_deg": 10.0,
                "altitude_ft": 27000,
                "speed_kt": 400,
            },
        ],
    },
}


def list_presets() -> List[Dict]:
    """Presets without their aircraft lists, for a picker UI."""
    return [
        {"key": p["key"], "label": p["label"], "description": p["description"], "aircraft_count": len(p["aircraft"])}
        for p in PRESETS.values()
    ]


def get_preset(key: str) -> Preset:
    """Raises KeyError if `key` is not a known preset."""
    return PRESETS[key]
