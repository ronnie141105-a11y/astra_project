"""Shared preset TypedDicts, split out of `scenario_presets.py` so that
`scenario_presets_operational.py` (and any other preset-builder module)
can import the type without importing `scenario_presets` itself and
creating a cycle (`scenario_presets` imports the operational builders
to populate `PRESETS`).
"""

from typing import List, Optional, Tuple, TypedDict


class PresetAircraft(TypedDict, total=False):
    callsign: str
    aircraft_type: str
    lat: float
    lon: float
    heading_deg: float
    altitude_ft: float
    speed_kt: float
    #: Optional ordered [(lat, lon), ...] remaining route -- when present,
    #: the aircraft is created as route-following (see
    #: astra.trajectory.route_engine.RouteAwareTrajectoryEngine) instead
    #: of plain dead reckoning. Omit for a normal constant-heading aircraft.
    route_waypoints: Optional[List[Tuple[float, float]]]


class Preset(TypedDict):
    key: str
    label: str
    description: str
    aircraft: List[PresetAircraft]
