"""
Predefined traffic-situation templates for the Scenario Builder page
(thesis goal 4: "choose predefined traffic situations (crossing, merge,
arrival rush, etc.)").

Each preset is a plain, JSON-safe dict -- no domain objects -- so
`scenario_routes.py` can hand it straight to `StateReader.create_aircraft()`
in a loop without importing anything from `astra.interface`. Coordinates
are anchored around the Ho Chi Minh FIR (~10.8N, 106.7E) at speeds and
separations chosen so every preset actually exercises the full pipeline
end to end (see "Why these specific numbers" below) -- they are
illustrative traffic geometry for demoing the pipeline, not real
published waypoints or airways.

Why these specific numbers
---------------------------
Two structural constraints, discovered empirically while validating
these presets (and documented in the thesis' Milestone 7 follow-up),
drive every coordinate/speed choice here:

1.  Aircraft must start within `hotspot_dbscan_eps_nm` (15 NM) /
    `separation_vertical_ft` (1000 ft) of at least one other aircraft in
    the SAME preset. `TrackerEngine` only opens/extends a track from the
    *currently observed* (horizon-0) cluster -- a cluster that only
    exists in a predicted future horizon can never become a track (a
    known, documented limitation; see docs/PROJECT_STATUS.md). Presets
    that start further apart than this, however dramatic their eventual
    convergence, will never produce a track, a forecast, or a resolution
    -- they will simply do nothing, which is indistinguishable from a
    quiet scene without checking the logs.
2.  Speeds are terminal-area (105-145 kt), not cruise. At typical cruise
    speed (300+ kt) two converging aircraft close, cross, and disperse
    again *within* a single 5-minute prediction horizon -- no future
    horizon ever catches them above `forecast_onset_threshold`, so
    `ForecastEngine.predicted_onset_s` never fires, and
    `ResolutionEngine` has nothing eligible to resolve even though a
    real (if brief) hotspot existed.

`crossing` is validated to reliably cross `forecast_onset_threshold`
(50) within the first few predicted horizons and trigger a resolution.
`ComplexityEngine._effective_conflict_reference` caps the MTCA/LTCA
conflict sub-score's saturation reference
(`complexity_mtca_reference_count`/`complexity_ltca_reference_count`,
calibrated for 3-5 *concurrent* conflict pairs) at each cluster's
actual maximum possible pair count (`C(n, 2)`) -- found and fixed while
validating `arrival_sequencing` (see
docs/backend_improvements_backlog.md item 2) -- so small clusters like
`crossing`'s single pair aren't structurally prevented from reaching
the onset threshold no matter how severe the actual conflict.

A handful of earlier hand-picked presets (`merge`, `arrival_rush`,
`head_on`, `parallel_overtake`, `free_flow`, `dogleg_turn`) and two
thesis-replay presets (`thesis_baseline`, `thesis_converging_hotspot`)
were built against an older connector that had no route-aware spawn
profile at all -- every aircraft in them just flew a fixed heading
forever, with no concept of "land" or "overflight". They have been
retired now that `MockConnector` supports real `flight_type`-driven
behaviour (see "Route-aware flight profiles" below); their old .scn
equivalents (`scenarios/thesis_baseline.scn`,
`scenarios/thesis_converging_hotspot.scn`) have been removed for the
same reason. `crossing` and `thesis_multi_hotspot` are kept as
lightweight, destination-agnostic tracking/complexity demos (no
`route_waypoints`, so `flight_type` doesn't apply to them either); the
operational presets below are the ones exercising the new system.

Route-aware flight profiles ("LANDING"/"OVERFLIGHT")
------------------------------------------------------
Every preset that supplies `route_waypoints` can additionally tag each
aircraft with a `flight_type` of `"LANDING"` or `"OVERFLIGHT"` (see
`astra.interface.mock_connector.VALID_FLIGHT_TYPES` and
`MockConnector.create_aircraft`'s docstring) -- this is the connector's
actual spawn-profile mechanism and supersedes the older approach (still
used by `crossing`/`thesis_multi_hotspot` below, which have no
`route_waypoints` at all) of just placing dead-reckoning aircraft on a
converging heading with no destination-aware behaviour:

* `"LANDING"`: the aircraft genuinely descends over the final 40 NM of
  its route and stops (ground speed zeroed) exactly at the final
  waypoint -- for a route whose last waypoint is a real arrival
  airport/fix.
* `"OVERFLIGHT"`: the aircraft holds its cruise level straight through
  the final waypoint and is automatically despawned 5 minutes after
  passing it -- for a route whose last waypoint is only a crossing
  fix, not a destination.
* Omitted (`None`): plain route-following with no forced end-of-route
  behaviour -- e.g. a departure already established at its climb-band
  altitude, where this project's connector has no "climb away" profile
  to model.

Every preset below built from real airway data
(`scenario_presets_operational.py`) now sets `flight_type` per
aircraft according to this rule, so aircraft actually land, hold
level, or despawn as their role implies instead of flying off the map
forever.

Operational (geo-based) scenarios
----------------------------------
`arrival_sequencing`, `sector_overload` and `crossing_airways` are a
different family, built by `scenario_presets_operational.py` on top of
`scenario_geo.py`'s helpers instead of hand-picked coordinates. Where
every preset above exists to reliably exercise one pipeline stage with
illustrative geometry, these three exist to answer a specific
question: *what can ASTRA show 30-60 minutes before an ATCO would
normally have to intervene* -- so they use real waypoints/airways from
`geo/airways.json`, real sector polygons from `geo/sectors.json`, and
distances/speeds chosen so the relevant prediction genuinely sits in
that medium-term window, not seconds away.

They still have to respect the same two structural constraints as
everything else in this file (a track can only open from a horizon-0
cluster; cruise-speed encounters can close within a single horizon), so
none of them start with zero existing proximity -- each has some
subset of its aircraft within clustering range *now*, with the
medium-term story coming from what is still approaching, not yet
close. See each builder function's own docstring in
`scenario_presets_operational.py` for the exact reasoning, and
`scenarios/{arrival_sequencing,sector_overload,crossing_airways}_demo.py`
for scripts that run each preset through the real pipeline (via
`MockConnector`, no BlueSky) and record what ASTRA actually predicts.
"""

from typing import Dict, List

from astra.dashboard import scenario_presets_operational as operational
from astra.dashboard.scenario_types import Preset, PresetAircraft

__all__ = ["PresetAircraft", "Preset", "PRESETS", "list_presets", "get_preset"]


_CENTER_LAT = 10.82
_CENTER_LON = 106.67

PRESETS: Dict[str, Preset] = {
    "crossing": {
        "key": "crossing",
        "label": "Crossing traffic",
        "description": (
            "Two aircraft on perpendicular tracks converging on the same "
            "point and altitude -- the classic single conflict pair. "
            "Starts below the onset threshold (~38 pts) and is forecast "
            "to cross it by the 5-min horizon (~56 pts)."
        ),
        "aircraft": [
            {
                "callsign": "HVN101",
                "aircraft_type": "A321",
                "lat": 10.953243,
                "lon": 106.670000,
                "heading_deg": 180.0,
                "altitude_ft": 34000,
                "speed_kt": 60,
            },
            {
                "callsign": "VJC202",
                "aircraft_type": "A320",
                "lat": 10.819970,
                "lon": 106.805655,
                "heading_deg": 270.0,
                "altitude_ft": 34000,
                "speed_kt": 65,
            },
        ],
    },
    # ---- Thesis scenario (identical traffic to scenarios/thesis_multi_hotspot.scn) ----
    # This mirrors the one remaining BlueSky .scn file used for the thesis'
    # Chapter 4 data collection (same coordinates/speeds/types), so results
    # loaded here match the documented thesis numbers -- and, being a plain
    # preset, needs no BlueSky process at all, only `--mock` mode. The .scn
    # file remains available unchanged for anyone who does want to run it
    # against a live BlueSky node instead; both paths produce the same
    # traffic because MockConnector and BlueSky consume the same CRE syntax.
    "thesis_multi_hotspot": {
        "key": "thesis_multi_hotspot",
        "label": "Thesis: two simultaneous hotspots",
        "description": (
            "Two independent 4-aircraft converging crosses ~55 NM apart, "
            "active at the same time -- multi-track tracking/forecasting/"
            "ranking stress test. Identical traffic to "
            "scenarios/thesis_multi_hotspot.scn."
        ),
        "aircraft": [
            {"callsign": "HVN401", "aircraft_type": "A320", "lat": 11.11655, "lon": 106.85000, "heading_deg": 180.0, "altitude_ft": 34000, "speed_kt": 120},
            {"callsign": "VJC402", "aircraft_type": "B738", "lat": 10.78345, "lon": 106.85000, "heading_deg": 0.0, "altitude_ft": 34000, "speed_kt": 130},
            {"callsign": "PIC403", "aircraft_type": "A319", "lat": 10.94995, "lon": 107.01964, "heading_deg": 270.0, "altitude_ft": 34500, "speed_kt": 115},
            {"callsign": "AXJ404", "aircraft_type": "B77W", "lat": 10.94995, "lon": 106.68036, "heading_deg": 90.0, "altitude_ft": 34000, "speed_kt": 125},
            {"callsign": "QTR405", "aircraft_type": "A359", "lat": 10.71655, "lon": 106.35000, "heading_deg": 180.0, "altitude_ft": 37000, "speed_kt": 122},
            {"callsign": "SIA406", "aircraft_type": "A320", "lat": 10.38345, "lon": 106.35000, "heading_deg": 0.0, "altitude_ft": 37000, "speed_kt": 128},
            {"callsign": "THA407", "aircraft_type": "B738", "lat": 10.54995, "lon": 106.51942, "heading_deg": 270.0, "altitude_ft": 37500, "speed_kt": 118},
            {"callsign": "CPA408", "aircraft_type": "A321", "lat": 10.54995, "lon": 106.18058, "heading_deg": 90.0, "altitude_ft": 37000, "speed_kt": 124},
        ],
    },
    # ---- Operational (geo-based) scenarios -------------------------------
    # Built from the real published airway/sector network in
    # astra/dashboard/geo/ via scenario_presets_operational.py, instead of
    # hand-picked demo coordinates -- see that module's docstring and each
    # builder function for the operational story and the numbers behind it.
    # These exist to demonstrate ASTRA's actual value proposition -- what
    # it can show 30-60 minutes before an ATCO would normally have to
    # intervene -- rather than tactical (already-close) conflicts.
    "arrival_sequencing": {
        "key": "arrival_sequencing",
        "label": "Arrival sequencing / transfer coordination",
        "description": (
            "Two aircraft in-trail on real airway W1 (MEVON-BMT-ENRIN-AC-"
            "ESDOB-TSH), 5 NM apart, same level, near-identical speed -- "
            "fully separation-compliant now, but the trailing aircraft is "
            "slightly faster and closes to inside MTCA minima roughly "
            "40-50 min out, well before reaching the sector-boundary fix "
            "AC. A flow/workload story that ASTRA also correctly resolves "
            "as a genuine, if distant, conflict: ResolutionEngine "
            "typically proposes a speed adjustment automatically within "
            "the first couple of poll cycles. See "
            "scenarios/arrival_sequencing_demo.py for the measured "
            "onset time and the resulting before/after spacing at AC."
        ),
        "aircraft": operational.arrival_sequencing_aircraft(),
    },
    "sector_overload": {
        "key": "sector_overload",
        "label": "Sector overload (~40 aircraft)",
        "description": (
            "~40 aircraft on 11 real route segments across HCM ACC "
            "Sectors 1, 2, 5, 6 and 7 -- overflights, arrivals and "
            "departures (including reverse-direction traffic on the same "
            "airway), realistic in-trail spacing, varied cruise levels. "
            "No sector is already overloaded now; several independently "
            "unremarkable flows converge on Sectors 6/7 over the next "
            "30-60 min, an emergent density peak SectorComplexityEngine "
            "is built to trend ahead of time. See "
            "scenarios/sector_overload_demo.py for the measured "
            "per-sector complexity/count trend."
        ),
        "aircraft": operational.sector_overload_aircraft(),
    },
    "crossing_airways": {
        "key": "crossing_airways",
        "label": "Crossing airways at AC",
        "description": (
            "Three real inbound flows converging on waypoint AC -- W1 "
            "from the NNE, W2 from due east, W15 from the ENE -- each "
            "contributing a lead aircraft (~12 NM out, forming one cluster "
            "now with no separation loss) and a trailing aircraft ~30-36 "
            "min out on the same airway. A genuine medium-term hotspot: "
            "no emergency now, but sustained crossing traffic as the next "
            "wave arrives. See scenarios/crossing_airways_demo.py for the "
            "measured onset horizon and proposed strategic adjustments."
        ),
        "aircraft": operational.crossing_airways_aircraft(),
    },
    "nominal_sector_traffic": {
        "key": "nominal_sector_traffic",
        "label": "Nominal sector traffic (~72 aircraft)",
        "description": (
            "72 aircraft (configurable 40-100) spread across 22 real "
            "airways -- a realistic overflight/arrival/departure mix, no "
            "pair closer than 10 NM at spawn. Arrivals descend from "
            "FL200-FL300 and level at FL100; departures climb from low "
            "altitude and level at a jittered intermediate/cruise level "
            "-- both at 2000 ft/min, both via this project's automatic "
            "level-off (ALT-capture), not an indefinite climb/descent. "
            "A general-purpose baseline scene for exercising the whole "
            "pipeline at volume, not a single scripted encounter."
        ),
        "aircraft": operational.nominal_sector_traffic_aircraft(),
    },
    "convergence_hotspot": {
        "key": "convergence_hotspot",
        "label": "5-way convergence hotspot at AC (30 min)",
        "description": (
            "5 aircraft on 5 real routes (W1, Q1, W2, W15, L644) "
            "approaching waypoint AC from 5 distinct bearings, all "
            "reaching it simultaneously at exactly t=30 min -- same "
            "cruise level, a genuine 5-way lateral encounter. All start "
            "100+ NM apart (no track at t=0), so early cycles show "
            "normal tracker noise before a real cluster forms as they "
            "close in -- ResolutionEngine then proposes both single- "
            "and joint-aircraft candidates from it."
        ),
        "aircraft": operational.convergence_hotspot_aircraft(),
    },
    "solvable_conflict": {
        "key": "solvable_conflict",
        "label": "Solvable 2-aircraft conflict at BMT (20 min)",
        "description": (
            "A TSH departure and a PLK arrival, both reaching BMT -- and "
            "the same level, FL100 -- at exactly t=20 min (BMT-PLK isn't "
            "linked by any single filed airway in the current dataset, "
            "so that leg is a flagged straight-line approximation between "
            "the two real points). A clean, resolvable benchmark: a real "
            "lateral and vertical conflict at a known time/place, with "
            "both a heading-based and an altitude-based fix genuinely "
            "available to ResolutionEngine."
        ),
        "aircraft": operational.solvable_conflict_aircraft(),
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
