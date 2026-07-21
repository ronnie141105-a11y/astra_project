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

`crossing`, `merge`, and `arrival_rush` are validated to reliably cross
`forecast_onset_threshold` (50) within the first few predicted horizons
and trigger a resolution. `head_on` and `parallel_overtake` also now
reliably cross it (empirically re-validated after the fix below) --
earlier versions of this docstring claimed their 2-aircraft geometry
structurally capped complexity below the 3+ aircraft presets, because
the MTCA/LTCA conflict sub-score's saturation reference
(`complexity_mtca_reference_count`/`complexity_ltca_reference_count`,
calibrated for 3-5 *concurrent* conflict pairs) could never be reached
by a 2-aircraft cluster's one possible pair, no matter how severe the
actual conflict. `ComplexityEngine._effective_conflict_reference` fixes
this by capping the reference at each cluster's actual maximum possible
pair count (`C(n, 2)`) -- found and fixed while validating
`arrival_sequencing` (see docs/backend_improvements_backlog.md item 2);
it only ever lowers the reference for clusters below the configured
reference's own implied size, so `arrival_rush` (10 possible pairs vs. a
default LTCA reference of 5) is completely unaffected, and `merge` (3
possible pairs, same as the default MTCA reference) is only affected on
its LTCA side. `free_flow` intentionally stays far apart -- it is the
negative control, and has no possible pairs to affect either way.

`dogleg_turn` is a different kind of demo and is exempt from
constraint 1 above on purpose: its two aircraft start ~40 NM apart
specifically so no cluster/track forms and the two predicted paths
(dead-reckoning vs route-aware) stay visually distinct on the map for
longer -- the point of that preset is to *see* the predicted-trajectory
lines diverge after a turn, not to trigger tracking/resolution (compare
"CONV1"/"CONV2" in `scripts/evaluate_trajectory_predictors.py`, which
uses the same routes for the formal, non-visual version of this
comparison).

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
    "merge": {
        "key": "merge",
        "label": "Merging streams",
        "description": (
            "Three aircraft converging from different headings onto "
            "roughly the same point and level. Starts at ~45 pts, "
            "forecast to reach ~75 pts by the 5-min horizon."
        ),
        "aircraft": [
            {
                "callsign": "HVN301",
                "aircraft_type": "A359",
                "lat": 10.936588,
                "lon": 106.670000,
                "heading_deg": 180.0,
                "altitude_ft": 36000,
                "speed_kt": 55,
            },
            {
                "callsign": "VJC402",
                "aircraft_type": "A321",
                "lat": 10.761689,
                "lon": 106.772776,
                "heading_deg": 300.0,
                "altitude_ft": 36000,
                "speed_kt": 60,
            },
            {
                "callsign": "PIC503",
                "aircraft_type": "B789",
                "lat": 10.761689,
                "lon": 106.567224,
                "heading_deg": 60.0,
                "altitude_ft": 36500,
                "speed_kt": 65,
            },
        ],
    },
    "arrival_rush": {
        "key": "arrival_rush",
        "label": "Arrival rush",
        "description": (
            "Five inbound aircraft descending toward the same terminal "
            "area from different directions -- a sustained, multi-aircraft "
            "hotspot rather than a single pair. Starts below threshold "
            "(~47 pts), forecast to reach ~80-89 pts by the 10-15 min "
            "horizon."
        ),
        "aircraft": [
            {
                "callsign": "HVN601",
                "aircraft_type": "A321",
                "lat": 10.986554,
                "lon": 106.670000,
                "heading_deg": 180.0,
                "altitude_ft": 24000,
                "speed_kt": 55,
            },
            {
                "callsign": "VJC602",
                "aircraft_type": "A320",
                "lat": 10.871426,
                "lon": 106.831297,
                "heading_deg": 252.0,
                "altitude_ft": 23500,
                "speed_kt": 53,
            },
            {
                "callsign": "PIC603",
                "aircraft_type": "B789",
                "lat": 10.685239,
                "lon": 106.769626,
                "heading_deg": 324.0,
                "altitude_ft": 24500,
                "speed_kt": 57,
            },
            {
                "callsign": "AXJ604",
                "aircraft_type": "A320",
                "lat": 10.685239,
                "lon": 106.570374,
                "heading_deg": 36.0,
                "altitude_ft": 23000,
                "speed_kt": 54,
            },
            {
                "callsign": "HVN605",
                "aircraft_type": "A359",
                "lat": 10.871426,
                "lon": 106.508703,
                "heading_deg": 108.0,
                "altitude_ft": 24000,
                "speed_kt": 56,
            },
        ],
    },
    "head_on": {
        "key": "head_on",
        "label": "Head-on pair",
        "description": (
            "Two aircraft on reciprocal headings at the same level -- "
            "tests onset/urgency timing on a fast-closing geometry. "
            "Starts at ~44 pts, forecast to cross the 50-pt threshold by "
            "the 5-min horizon (~71 pts)."
        ),
        "aircraft": [
            {
                "callsign": "HVN701",
                "aircraft_type": "A321",
                "lat": 10.819988,
                "lon": 106.585216,
                "heading_deg": 90.0,
                "altitude_ft": 35000,
                "speed_kt": 50,
            },
            {
                "callsign": "VJC702",
                "aircraft_type": "A320",
                "lat": 10.819988,
                "lon": 106.754784,
                "heading_deg": 270.0,
                "altitude_ft": 35000,
                "speed_kt": 55,
            },
        ],
    },
    "parallel_overtake": {
        "key": "parallel_overtake",
        "label": "Parallel overtake",
        "description": (
            "Same track, same level, one aircraft faster than the other -- "
            "a slow-building conflict. Starts at ~44 pts and now crosses "
            "the 50-pt onset threshold by the 15-min horizon (~56 pts), "
            "then eases off again once the faster aircraft has passed."
        ),
        "aircraft": [
            {
                "callsign": "HVN801",
                "aircraft_type": "A320",
                "lat": 10.820000,
                "lon": 106.670000,
                "heading_deg": 45.0,
                "altitude_ft": 33000,
                "speed_kt": 115,
            },
            {
                "callsign": "PIC802",
                "aircraft_type": "B789",
                "lat": 10.737548,
                "lon": 106.586091,
                "heading_deg": 45.0,
                "altitude_ft": 33000,
                "speed_kt": 145,
            },
        ],
    },
    "free_flow": {
        "key": "free_flow",
        "label": "Free flow (light)",
        "description": (
            "Four aircraft on divergent, non-conflicting tracks -- a "
            "quiet baseline scene with no expected hotspot, useful for "
            "confirming the dashboard stays calm when it should. The "
            "negative control: no cluster forms at any horizon."
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
    "dogleg_turn": {
        "key": "dogleg_turn",
        "label": "Route-following dogleg (route-aware demo)",
        "description": (
            "Two aircraft, each on a filed route with one sharp turn "
            "partway along. Dead-reckoning prediction (the dashed grey "
            "line) flies straight through both turns; the route-aware "
            "prediction (the solid amber line) turns onto the real leg "
            "and correctly shows the two aircraft converging afterward -- "
            "a hotspot dead reckoning alone would miss entirely. See "
            "scripts/evaluate_trajectory_predictors.py for the same "
            "comparison run as a formal thesis evaluation."
        ),
        "aircraft": [
            {
                "callsign": "CONV1",
                "aircraft_type": "A320",
                "lat": 10.80,
                "lon": 106.20,
                "heading_deg": 90.0,
                "altitude_ft": 35000,
                "speed_kt": 250,
                "route_waypoints": [(10.80, 106.55), (11.50, 106.55)],
            },
            {
                "callsign": "CONV2",
                "aircraft_type": "B738",
                "lat": 10.90,
                "lon": 106.90,
                "heading_deg": 270.0,
                "altitude_ft": 34500,
                "speed_kt": 250,
                "route_waypoints": [(10.90, 106.55), (11.60, 106.55)],
            },
        ],
    },
    # ---- Thesis scenarios (identical traffic to scenarios/thesis_*.scn) ----
    # These mirror the BlueSky .scn files used for the thesis' Chapter 4 data
    # collection exactly (same coordinates/speeds/types), so results loaded
    # here match the documented thesis numbers -- and, being plain presets,
    # need no BlueSky process at all, only `--mock` mode. The .scn files
    # remain available unchanged for anyone who does want to run them
    # against a live BlueSky node instead; both paths produce the same
    # traffic because MockConnector and BlueSky consume the same CRE
    # syntax (scripts/run_scn_offline.py is the CLI equivalent of loading
    # one of these three presets from the dashboard).
    "thesis_baseline": {
        "key": "thesis_baseline",
        "label": "Thesis: baseline (control)",
        "description": (
            "6 well-separated aircraft, no convergence -- confirms zero "
            "false-positive hotspots. Identical traffic to "
            "scenarios/thesis_baseline.scn."
        ),
        "aircraft": [
            {"callsign": "HVN101", "aircraft_type": "A321", "lat": 11.79933, "lon": 106.70000, "heading_deg": 90.0, "altitude_ft": 30000, "speed_kt": 250},
            {"callsign": "VJC202", "aircraft_type": "A320", "lat": 11.25696, "lon": 107.50888, "heading_deg": 200.0, "altitude_ft": 33000, "speed_kt": 260},
            {"callsign": "PIC303", "aircraft_type": "A319", "lat": 10.25726, "lon": 107.65278, "heading_deg": 260.0, "altitude_ft": 36000, "speed_kt": 270},
            {"callsign": "AXJ404", "aircraft_type": "B738", "lat": 9.96723, "lon": 106.70000, "heading_deg": 10.0, "altitude_ft": 29000, "speed_kt": 240},
            {"callsign": "QTR505", "aircraft_type": "B77W", "lat": 10.21540, "lon": 105.67407, "heading_deg": 70.0, "altitude_ft": 38000, "speed_kt": 280},
            {"callsign": "SIA606", "aircraft_type": "A359", "lat": 11.28182, "lon": 105.84693, "heading_deg": 160.0, "altitude_ft": 31000, "speed_kt": 255},
        ],
    },
    "thesis_converging_hotspot": {
        "key": "thesis_converging_hotspot",
        "label": "Thesis: converging hotspot",
        "description": (
            "4-aircraft symmetric converging cross -- primary thesis demo "
            "(starts ~44 pts, forecast onset, ranked resolution). Identical "
            "traffic to scenarios/thesis_converging_hotspot.scn and this "
            "app's own --mock default traffic."
        ),
        "aircraft": [
            {"callsign": "HVN301", "aircraft_type": "A320", "lat": 10.96655, "lon": 106.70000, "heading_deg": 180.0, "altitude_ft": 30000, "speed_kt": 120},
            {"callsign": "VJC302", "aircraft_type": "B738", "lat": 10.63345, "lon": 106.70000, "heading_deg": 0.0, "altitude_ft": 30000, "speed_kt": 130},
            {"callsign": "PIC303", "aircraft_type": "A319", "lat": 10.79995, "lon": 106.86956, "heading_deg": 270.0, "altitude_ft": 30500, "speed_kt": 115},
            {"callsign": "AXJ304", "aircraft_type": "B77W", "lat": 10.79995, "lon": 106.53044, "heading_deg": 90.0, "altitude_ft": 30000, "speed_kt": 125},
        ],
    },
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
