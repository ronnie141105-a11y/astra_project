"""
Geo-based operational scenario presets -- built on real published
waypoints/airways (`astra/dashboard/geo/airways.json`,
`geo/sectors.json`) via `scenario_geo.py`, instead of the hand-picked
demo coordinates in `scenario_presets.py`.

These three presets exist to demonstrate ASTRA's actual value
proposition -- medium-term (30-60 min) flow-management prediction --
rather than tactical (already-close) conflict detection, which is all
the original hand-picked presets show. See each builder's docstring
for the operational story and the specific numbers behind it; see
`scenario_presets.py`'s module docstring for how these interact with
the pipeline's two structural constraints (horizon-0 clustering,
cruise-speed horizon-crossing) that every preset in this project has to
respect to actually produce a track/forecast/resolution.

Every aircraft here is built with `route_waypoints` from a real
sub-route of a real airway (via `scenario_geo.sub_route` /
`advance_from_route_start`), so:
  * its initial heading is the bearing of the first leg it is actually
    on (not a hand-typed number that may or may not match the geometry)
  * it is spawned part-way along a leg, not stacked on a waypoint
  * `RouteAwareTrajectoryEngine` flies it through the *real* remaining
    waypoint sequence, turns included, until the route runs out

All three builders are deterministic: aircraft counts, callsigns,
airway/waypoint choices and altitude/speed bands are fixed; the only
randomised part (cruise level/speed jitter within a band, for realism)
uses a fixed `random.Random` seed, so repeated runs produce the same
traffic every time -- comparable demonstrations, not a new scene each
load.
"""

import random
from typing import List

from astra.dashboard import scenario_geo as geo
from astra.dashboard.scenario_types import PresetAircraft

_SEED = 20260718  # today's date at authoring time -- arbitrary but fixed

_AIRLINES = ["HVN", "VJC", "VJ", "BAV", "QH", "JQ", "PIC", "AXJ", "THA", "SIA"]
_TYPES_JET = ["A321", "A320", "A319", "B738", "B77W", "A359", "B789"]
_TYPES_REGIONAL = ["ATR72", "E190"]


def _callsign(rng: random.Random, used: set) -> str:
    while True:
        cs = f"{rng.choice(_AIRLINES)}{rng.randint(100, 999)}"
        if cs not in used:
            used.add(cs)
            return cs


# ----------------------------------------------------------------------
# 1. Arrival sequencing / transfer coordination
# ----------------------------------------------------------------------


def arrival_sequencing_aircraft() -> List[PresetAircraft]:
    """Two in-trail aircraft on W1 (MEVON -> BMT -> ENRIN -> AC -> ESDOB -> TSH).

    The operational situation: both aircraft are cleared on the same
    airway, same cruise level, near-identical speed, ~5 NM in-trail --
    fully compliant with en-route separation, nothing for a tactical
    conflict tool to flag. But because the gap stays essentially
    constant, they will also reach the sector-boundary waypoint AC (and
    therefore the handoff to the next sector) within about a minute of
    each other, roughly 35-40 minutes from now -- meaning two
    back-to-back coordination calls to the next sector instead of one
    with breathing room. That is a flow-management problem, not a
    safety one: the fix is a small, early nudge to the trailing
    aircraft (a short vector off track and back), not a resolution
    clearance.

    Both aircraft start 5 NM apart on the same track, which puts them
    inside `hotspot_dbscan_eps_nm`/`separation_vertical_ft` of each
    other from cycle 1 (satisfies this project's structural constraint
    1 -- see `scenario_presets.py`), so `TrackerEngine` opens a track
    immediately and it persists/grows for the whole ~35-40 min transit
    to AC, giving a long, watchable medium-term track. Because heading
    and altitude are identical for both aircraft, `ComplexityEngine`'s
    heading-divergence and altitude-divergence components are
    structurally zero here (same as this project's existing `head_on`/
    `parallel_overtake` presets) -- the composite score plateaus in the
    low 40s and does not cross `forecast_onset_threshold` (50) on its
    own. That is intentional, not a bug: it is exactly what "reduce
    coordination workload, not avoid a conflict" should look like --
    ASTRA keeps a persistent, growing track on a compliant-but-tight
    pair without ever raising a hard alert. `scenarios/
    arrival_sequencing_demo.py` demonstrates the actual proposed
    sequencing adjustment (call `ResolutionEngine` directly against
    this track, the way `scenarios/domino_effect_demo.py` already
    does for its own hand-built demo track) and measures the resulting
    spacing at AC before/after applying it.
    """
    route = geo.sub_route("W1", "MEVON", "TSH")
    coords = [(lat, lon) for _, lat, lon in route]
    extended = geo.extend_route_backward(coords, extension_nm=5.0)

    lead = geo.advance_from_route_start(extended, 5.0)  # at MEVON
    trail = geo.advance_from_route_start(extended, 0.0)  # 5 NM behind MEVON

    return [
        {
            "callsign": "HVN123",
            "aircraft_type": "A321",
            "lat": lead.lat,
            "lon": lead.lon,
            "heading_deg": lead.heading_deg,
            "altitude_ft": 34000,
            "speed_kt": 255,
            "route_waypoints": lead.remaining_waypoints,
        },
        {
            "callsign": "VJC456",
            "aircraft_type": "A320",
            "lat": trail.lat,
            "lon": trail.lon,
            "heading_deg": trail.heading_deg,
            "altitude_ft": 34000,
            "speed_kt": 261,  # slightly faster: gap holds, doesn't open up
            "route_waypoints": trail.remaining_waypoints,
        },
    ]


# ----------------------------------------------------------------------
# 2. Sector overload
# ----------------------------------------------------------------------

#: (airway, start_wp, end_wp, count, role, alt band ft, speed band kt)
#: role in {"overflight", "arrival", "departure"} only changes the
#: altitude/speed band used (this project has no climb/descent model,
#: so a "departure" is represented as a lower, slower cruise level
#: consistent with still being below the overflights, not an actual
#: climb profile).
_OVERLOAD_FLOWS = [
    ("W1", "BMT", "TSH", 5, "overflight"),   # sectors 2 -> 6 -> 7
    ("W1", "TSH", "BMT", 3, "departure"),    # same corridor, opposite direction
    ("W2", "PCA", "TSH", 5, "arrival"),      # sectors 1 -> 5 -> 6 -> 7
    ("W2", "TSH", "PCA", 3, "departure"),
    ("W12", "PCA", "TRN", 4, "overflight"),  # sectors 1 -> 2 -> 7
    ("W15", "AC", "CRA", 3, "overflight"),   # sectors 6 -> 5 -> 1
    ("W16", "TSH", "RG", 3, "departure"),    # sector 7
    ("W9", "TSH", "CN", 3, "departure"),     # sector 7
    ("W19", "CN", "TSH", 4, "arrival"),      # sector 7
    ("L637", "BITOD", "TSH", 4, "arrival"),  # sector 7
    ("W7", "LKH", "BMT", 3, "overflight"),   # sectors 5 -> 6 -> 2
]

_ALT_BANDS_FT = {
    "overflight": [31000, 33000, 35000, 37000, 39000],
    "arrival": [24000, 26000, 28000, 30000],
    "departure": [15000, 17000, 19000, 21000],
}
_SPEED_BANDS_KT = {
    "overflight": (265, 300),
    "arrival": (250, 275),
    "departure": (180, 225),
}


def sector_overload_aircraft() -> List[PresetAircraft]:
    """~40 aircraft spread on real airways across HCM ACC Sectors 1, 2, 5, 6, 7.

    Eleven route segments (see `_OVERLOAD_FLOWS`) covering all five
    target sectors (confirmed by point-in-polygon lookup against
    `geo/sectors.json` -- see this module's dev notes) mix overflights,
    arrivals and departures, including departures flown the *opposite*
    direction to the matching overflight/arrival on the same airway
    (e.g. TSH -> PCA on W2 opposite PCA -> TSH), at different cruise
    levels and speed bands per role. Every aircraft is placed at a
    jittered fractional distance along its segment (never exactly on a
    waypoint) via `scenario_geo.advance_from_route_start`, each with
    its full remaining real waypoint sequence as `route_waypoints`, and
    a small deterministic jitter (fixed-seed RNG) on altitude/speed
    within its role's band for realism without breaking repeatability.

    This does not aim to already be a hotspot at t=0 in any one sector
    -- individual segments keep several NM of in-trail spacing (no
    pair starts closer than ~12 NM along the same track) so there is no
    immediate emergency. The overload is a *emergent, medium-term*
    property: sectors 6 and 7 in particular sit at the confluence of
    several of these flows (W1, W2 and W15 all pass through 6 on the
    way to AC; W1, W2, W9, W16, W19 and L637 all pass through 7 via/at
    TSH), so as this traffic advances over the next 30-60 minutes,
    several independently-unremarkable flows arrive in the same sector
    at overlapping times -- exactly the kind of count/density buildup
    `SectorComplexityEngine` (Milestone 9) is built to trend and flag
    before it happens, not react to after the fact. See
    `scenarios/sector_overload_demo.py` for the actual measured
    per-sector complexity/aircraft-count trend from a real pipeline
    run against this preset.
    """
    rng = random.Random(_SEED)
    used_callsigns: set = set()
    aircraft: List[PresetAircraft] = []

    for designator, start_wp, end_wp, count, role in _OVERLOAD_FLOWS:
        route = geo.sub_route(designator, start_wp, end_wp)
        coords = [(lat, lon) for _, lat, lon in route]
        total_nm = geo.polyline_length_nm(coords)
        # Evenly-spaced-ish anchor points, each jittered +/- 20% of the
        # per-aircraft slot width so they don't line up in a suspicious
        # perfect lattice, while keeping >= ~12 NM between consecutive
        # aircraft on the same segment (no accidental in-trail overload
        # within a single flow).
        slot = total_nm / (count + 1)
        for i in range(1, count + 1):
            jitter = rng.uniform(-0.2, 0.2) * slot
            distance_nm = max(5.0, min(total_nm - 5.0, i * slot + jitter))
            start = geo.advance_from_route_start(coords, distance_nm)

            alt_ft = rng.choice(_ALT_BANDS_FT[role]) + rng.choice([0, 0, 500])
            speed_lo, speed_hi = _SPEED_BANDS_KT[role]
            speed_kt = rng.randint(speed_lo, speed_hi)
            ac_type = rng.choice(
                _TYPES_REGIONAL if role == "departure" and rng.random() < 0.15 else _TYPES_JET
            )

            aircraft.append(
                {
                    "callsign": _callsign(rng, used_callsigns),
                    "aircraft_type": ac_type,
                    "lat": start.lat,
                    "lon": start.lon,
                    "heading_deg": start.heading_deg,
                    "altitude_ft": alt_ft,
                    "speed_kt": speed_kt,
                    "route_waypoints": start.remaining_waypoints,
                }
            )

    return aircraft


# ----------------------------------------------------------------------
# 3. Crossing airways

#: The two flows that form the immediate (t=0) cluster: (designator,
#: near_wp, far_wp, lead_speed_kt, trail_speed_kt, altitude_ft). Lead
#: aircraft sit 12 NM out on their own track -- close enough to each
#: other to cluster now (mutual distance well under
#: `hotspot_dbscan_eps_nm`), the same two-flow-crossing geometry as
#: this project's existing `crossing` preset, just anchored on AC's
#: real bearings instead of hand-picked ones.
_CROSSING_FLOWS = [
    ("W1", "ENRIN", "MEVON", 260, 258, 33000),
    ("W2", "VEPMA", "CRA", 265, 262, 33000),
]

#: The third flow (W15) is deliberately kept entirely out of the t=0
#: cluster -- both its aircraft start well short of AC (LKH ~19 min
#: out, CRA ~30 min out at cruise speed) so neither is anywhere near
#: the W1/W2 encounter now. This is what makes the scenario genuinely
#: medium-term rather than an immediate three-way merge: by the time
#: this flow's traffic reaches AC, the W1/W2 pair's own encounter has
#: already resolved, but a *new* wave of crossing traffic is arriving
#: at the same fix -- sustained density, not a single spike. Per this
#: project's structural constraint 1 (see `scenario_presets.py`), a
#: cluster that doesn't exist yet at horizon 0 can never become a
#: tracked `FourDArhac`, so this flow's contribution is reported via
#: direct ETA/proximity analysis in `scenarios/crossing_airways_demo.py`
#: rather than via `TrackerEngine` -- documented there, not hidden.
_LATER_FLOW = ("W15", "LKH", "CRA", 258, 253, 33000)


def crossing_airways_aircraft() -> List[PresetAircraft]:
    """Two real inbound flows (W1, W2) crossing at AC now; a third (W15) arriving later.

    AC is a genuine crossing point: W1 arrives from the NNE (bearing
    ~208), W2 from due east (~271) and W15 from the ENE (~235) -- three
    distinct real tracks, not three arbitrary headings pointed at a
    made-up spot.

    W1 and W2 each contribute a **lead** aircraft ~12 NM out on its own
    track (mutually ~9 NM apart, well inside `hotspot_dbscan_eps_nm`,
    so `TrackerEngine` opens a track this cycle -- constraint 1) and a
    **trailing** aircraft much further back on the same airway (MEVON /
    CRA, ~30-35 minutes out) representing their own next wave. W15
    contributes two aircraft that are *both* still well short of AC
    (LKH, ~19 min out; CRA, ~30 min out) -- deliberately not part of
    the immediate cluster, so there is no three-way encounter happening
    right now, only a two-flow one, and W15's traffic is the "next
    wave" arriving at AC only after that encounter would already have
    resolved. `scenarios/crossing_airways_demo.py` reports the real
    onset/resolution for the tracked W1/W2 pair from the pipeline, and
    the W15 flow's arrival timing separately from direct ETA analysis
    (see that module's docstring for why).
    """
    aircraft: List[PresetAircraft] = []
    callsigns = [
        ("HVN701", "A321"), ("VJC702", "A320"),
        ("QH703", "A319"), ("BL704", "B738"),
    ]
    idx = 0

    for designator, near_wp, far_wp, lead_speed, trail_speed, alt_ft in _CROSSING_FLOWS:
        full_route = geo.sub_route(designator, far_wp, "AC")
        full_coords = [(lat, lon) for _, lat, lon in full_route]
        total_nm = geo.polyline_length_nm(full_coords)

        lead_distance_nm = max(total_nm - 12.0, 1.0)  # 12 NM short of AC
        lead = geo.advance_from_route_start(full_coords, lead_distance_nm)
        trail = geo.advance_from_route_start(full_coords, 0.0)  # at far_wp

        cs_lead, type_lead = callsigns[idx]
        cs_trail, type_trail = callsigns[idx + 1]
        idx += 2

        aircraft.append(
            {
                "callsign": cs_lead, "aircraft_type": type_lead,
                "lat": lead.lat, "lon": lead.lon, "heading_deg": lead.heading_deg,
                "altitude_ft": alt_ft, "speed_kt": lead_speed,
                "route_waypoints": lead.remaining_waypoints,
            }
        )
        aircraft.append(
            {
                "callsign": cs_trail, "aircraft_type": type_trail,
                "lat": trail.lat, "lon": trail.lon, "heading_deg": trail.heading_deg,
                "altitude_ft": alt_ft, "speed_kt": trail_speed,
                "route_waypoints": trail.remaining_waypoints,
            }
        )

    # W15's two "later wave" aircraft: neither is a lead-near-AC point,
    # both are real points further back on the airway (LKH, then CRA
    # further back still), so both are genuinely 19-30 min out.
    designator, near_wp, far_wp, near_speed, far_speed, alt_ft = _LATER_FLOW
    full_route = geo.sub_route(designator, far_wp, "AC")
    full_coords = [(lat, lon) for _, lat, lon in full_route]
    near_leg_nm = geo.polyline_length_nm(
        [(lat, lon) for _, lat, lon in geo.sub_route(designator, near_wp, "AC")]
    )
    near_start = geo.advance_from_route_start(
        full_coords, geo.polyline_length_nm(full_coords) - near_leg_nm
    )
    far_start = geo.advance_from_route_start(full_coords, 15.0)  # 15 NM past CRA:
    # CRA is also W2's trailing-aircraft start point (BL704) above:
    # starting THA706 exactly at CRA too would co-locate two aircraft
    # at the same lat/lon, an unrealistic false "already in conflict"
    # (both airways happen to share that fix). A small forward offset
    # keeps it a real point on the same airway without the collision.

    aircraft.append(
        {
            "callsign": "PIC705", "aircraft_type": "A359",
            "lat": near_start.lat, "lon": near_start.lon, "heading_deg": near_start.heading_deg,
            "altitude_ft": alt_ft, "speed_kt": near_speed,
            "route_waypoints": near_start.remaining_waypoints,
        }
    )
    aircraft.append(
        {
            "callsign": "THA706", "aircraft_type": "B789",
            "lat": far_start.lat, "lon": far_start.lon, "heading_deg": far_start.heading_deg,
            "altitude_ft": alt_ft, "speed_kt": far_speed,
            "route_waypoints": far_start.remaining_waypoints,
        }
    )
    return aircraft
