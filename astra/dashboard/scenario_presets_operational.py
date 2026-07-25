"""
Geo-based operational scenario presets -- built on real published
waypoints/airways (`astra/dashboard/geo/airways.json`,
`geo/sectors.json`) via `scenario_geo.py`, instead of the hand-picked
demo coordinates in `scenario_presets.py`.

These builders exist to demonstrate ASTRA's actual value
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

All builders are deterministic: aircraft counts, callsigns,
airway/waypoint choices and altitude/speed bands are fixed; the only
randomised part (cruise level/speed jitter within a band, for realism,
and -- for `nominal_sector_traffic_aircraft`/`sector_overload_aircraft`
-- the separation retry jitter) uses a fixed `random.Random` seed, so
repeated runs produce the same traffic every time -- comparable
demonstrations, not a new scene each load. Every builder in this
module sets `flight_type` per aircraft (`"LANDING"`/`"OVERFLIGHT"`,
see `MockConnector.create_aircraft`) according to what its route
actually represents: aircraft whose final waypoint is a real
destination airport (e.g. TSH, PLK) spawn with `flight_type="LANDING"`
and genuinely descend and stop there; aircraft whose final waypoint is
only a crossing/reporting fix (e.g. AC) spawn with
`flight_type="OVERFLIGHT"` and hold cruise level through it before
auto-despawning; aircraft representing a departure spawn directly at
their intended departure altitude with `flight_type=None` (no forced
profile) -- see each builder's own docstring for its specific role
mix.
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
    (right-now) conflict tool to flag. But because the second aircraft
    is very slightly faster, that gap will close to inside MTCA minima
    somewhere in the second half of the ~35-40 minute transit to the
    sector-boundary waypoint AC, meaning two back-to-back coordination
    calls to the next sector instead of one with breathing room. That is
    a flow-management problem more than a safety one: the fix is a
    small, early nudge to the trailing aircraft, not an emergency
    clearance -- but see below for why ASTRA now treats it as a real,
    if distant, predicted conflict rather than only a workload signal.

    Both aircraft start 5 NM apart on the same track, which puts them
    inside `hotspot_dbscan_eps_nm`/`separation_vertical_ft` of each
    other from cycle 1 (satisfies this project's structural constraint
    1 -- see `scenario_presets.py`), so `TrackerEngine` opens a track
    immediately. Because heading and altitude are identical for both
    aircraft, `ComplexityEngine`'s heading-divergence and
    altitude-divergence components are structurally zero here (same as
    this project's existing `head_on`/`parallel_overtake` presets) --
    but unlike an earlier version of this docstring claimed, the
    composite score does *not* stay capped below `forecast_onset_threshold`
    (50): once the slow overtake closes the gap enough to fall inside
    MTCA minima (predicted ~40-50 min out, well within this scenario's
    own transit time), `ComplexityEngine`'s conflict sub-score reaches
    its full weighted contribution for a 2-aircraft cluster -- see
    `ComplexityEngine._effective_conflict_reference` and
    docs/backend_improvements_backlog.md item 2, a fix made specifically
    because this preset's original tuning surfaced the gap it closes.
    In practice this means `ResolutionEngine` now proposes a real
    speed-adjustment candidate automatically, from the normal pipeline,
    typically within the first couple of poll cycles -- no hand-built
    track needed. `scenarios/arrival_sequencing_demo.py` still also
    demonstrates a hand-built-track call as a fallback technique (kept
    for scenarios where the automatic path genuinely doesn't fire), but
    for this preset specifically the automatic resolution is now the
    interesting, representative result: a workload-motivated pair that
    ASTRA also correctly recognises will become a genuine, if distant
    (40-50 min out), separation concern if left unmanaged.

    Both aircraft spawn with `flight_type="LANDING"` (see
    `MockConnector.create_aircraft`): TSH is their real destination
    airport, so both genuinely descend over the final 40 NM of the
    route and stop there, rather than flying on past it forever.
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
            "flight_type": "LANDING",
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
            "flight_type": "LANDING",
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
            # Role-based Scenario Builder spawn profile (same convention as
            # `nominal_sector_traffic_aircraft` below): "overflight" holds
            # cruise level through its final waypoint then auto-despawns,
            # "arrival" genuinely descends and lands at its final waypoint,
            # "departure" spawns level with no forced profile (this
            # project's connector has no "climb away" profile to model).
            flight_type = {"overflight": "OVERFLIGHT", "arrival": "LANDING", "departure": None}[role]

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
                    "flight_type": flight_type,
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
                "flight_type": "OVERFLIGHT",
            }
        )
        aircraft.append(
            {
                "callsign": cs_trail, "aircraft_type": type_trail,
                "lat": trail.lat, "lon": trail.lon, "heading_deg": trail.heading_deg,
                "altitude_ft": alt_ft, "speed_kt": trail_speed,
                "route_waypoints": trail.remaining_waypoints,
                "flight_type": "OVERFLIGHT",
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
            "flight_type": "OVERFLIGHT",
        }
    )
    aircraft.append(
        {
            "callsign": "THA706", "aircraft_type": "B789",
            "lat": far_start.lat, "lon": far_start.lon, "heading_deg": far_start.heading_deg,
            "altitude_ft": alt_ft, "speed_kt": far_speed,
            "route_waypoints": far_start.remaining_waypoints,
            "flight_type": "OVERFLIGHT",
        }
    )
    return aircraft


# ----------------------------------------------------------------------
# 4. Nominal sector traffic
# ----------------------------------------------------------------------

#: (designator, from_wp, to_wp, count, role). 30 flows across 22 of the
#: 70 real airways, deliberately spanning routes this file's earlier
#: presets never touch (L625, L628, L642, M765, M771, N639, N892, Q1,
#: Q15, A1, A206, G221 and more) as well as reusing a few TSH corridors
#: (W1, W2, L637/L644, W8/W9/W17/W19, N500, Q2, M768) in both directions
#: for genuine departure/arrival pairs, not just one-way overflights.
#: `role` picks the altitude/speed/vertical-profile band below --
#: "arrival"/"departure" both fly toward/away from TSH specifically
#: (this project's one major airport in the current dataset), everything
#: else is "overflight" (level cruise, no vertical motion).
_NOMINAL_FLOWS = [
    ("W1", "NOB", "TSH", 4, "arrival"),
    ("W1", "TSH", "NOB", 2, "departure"),
    ("W2", "NAH", "TSH", 3, "arrival"),
    ("W2", "TSH", "NAH", 2, "departure"),
    ("Q2", "TSH", "VPH", 3, "departure"),
    ("N500", "TSH", "PANDI", 2, "departure"),
    ("L644", "TSH", "DUDIS", 3, "departure"),
    ("M768", "AKMON", "TSH", 3, "arrival"),
    ("L637", "BITOD", "TSH", 3, "arrival"),
    ("W8", "TSH", "PQU", 2, "departure"),
    ("W19", "TSH", "CN", 2, "departure"),
    ("W9", "TSH", "CN", 2, "departure"),
    ("W17", "TSH", "TUNPO", 2, "departure"),
    ("L642", "EGEMU", "ESPOB", 3, "overflight"),
    ("M765", "PANDI", "IGARI", 3, "overflight"),
    ("Q1", "NOB", "AC", 3, "overflight"),
    ("N892", "MIGUG", "MELAS", 3, "overflight"),
    ("M771", "DUDIS", "DONDA", 3, "overflight"),
    ("L625", "AKMON", "ARESI", 2, "overflight"),
    ("L628", "PCA", "ARESI", 2, "overflight"),
    ("W12", "PCA", "TRN", 2, "overflight"),
    ("W10", "HUE", "CBI", 2, "overflight"),
    ("Q15", "CRA", "MESOX", 2, "overflight"),
    ("A206", "NALAO", "ASSAD", 2, "overflight"),
    ("W4", "CBI", "DBN", 2, "overflight"),
    ("N639", "NAH", "VILAO", 2, "overflight"),
    ("G221", "PCA", "BUNTA", 2, "overflight"),
    ("A1", "PAPRA", "BUNTA", 2, "overflight"),
    ("W15", "AC", "CRA", 2, "overflight"),
    ("W6", "NOB", "LAOCAI", 2, "overflight"),
]

_NOMINAL_ALT_BANDS_FT = {
    "overflight": [29000, 31000, 33000, 35000, 37000, 39000, 41000],
    "arrival": [20000, 24000, 28000, 30000],
    "departure": [2000, 3000, 4000, 5000],
}
_NOMINAL_SPEED_BAND_KT = {
    "overflight": (280, 420),
    "arrival": (250, 320),
    "departure": (250, 320),
}
#: Role-based Scenario Builder spawn profile (see
#: `MockConnector.create_aircraft`'s `flight_type`): "arrival" aircraft
#: spawn with `flight_type="LANDING"` (a real descent to FL000 and stop
#: at the hub waypoint, TSH); "overflight" aircraft spawn with
#: `flight_type="OVERFLIGHT"` (cruise level held throughout, then
#: auto-despawn once past the final waypoint); "departure" aircraft
#: spawn directly at their departure-band altitude with no forced
#: profile (`flight_type=None`) -- this project's connector has no
#: "climb away from an airport" profile, only "descend into one"
#: (LANDING) or "hold level across one" (OVERFLIGHT), so a departure is
#: simply presented already at its initial climb-band altitude rather
#: than animating an indefinite climb.
_NOMINAL_MIN_SEPARATION_NM = 10.0


def nominal_sector_traffic_aircraft(count: int = 72) -> List[PresetAircraft]:
    """40-100 aircraft (default 72) spread across 22 real airways, no pair closer than 10 NM.

    Each of `_NOMINAL_FLOWS`'s 30 (route, direction, role) segments gets
    its aircraft placed at a jittered fractional distance along that
    segment via `scenario_geo.advance_from_route_start`, exactly like
    `sector_overload_aircraft` above -- but this preset additionally
    enforces a **global** minimum separation (`_NOMINAL_MIN_SEPARATION_NM`,
    10 NM) across *every* pair of aircraft, not just within one flow's
    own in-trail spacing: several of these routes converge on the same
    hub waypoints (AC, TSH, PCA, CRA, NAH, NOB...) that
    `sector_overload_aircraft`'s smaller 11-flow set mostly avoided
    stacking up on, so an aircraft from one flow landing within 10 NM of
    one from an unrelated flow near a shared hub is a real possibility
    here and is checked for, not assumed away. Each candidate position
    gets up to 25 jitter attempts before that slot is silently dropped
    (logged via the returned list simply being shorter than planned) --
    at the default `count`/flow mix this has not been observed to drop
    any slot (see this module's own validation run), but the retry loop
    exists because the flow list was hand-tuned for 30 slots at the
    default `count`, not proven to always succeed at arbitrary `count`
    values in [40, 100].

    Every aircraft also gets a role-appropriate spawn profile (see
    `_NOMINAL_ALT_BANDS_FT`/`_NOMINAL_SPEED_BAND_KT` above): overflights
    spawn level and cruise straight through (`flight_type="OVERFLIGHT"`);
    arrivals spawn already at an arrival-band altitude and genuinely
    descend-and-land at the hub waypoint they're flying toward
    (`flight_type="LANDING"`); departures spawn level at a departure-band
    altitude with no forced profile. All ground speeds are drawn from
    [250, 450] kt.

    Args:
        count: Total aircraft across all flows, proportionally scaled
            from `_NOMINAL_FLOWS`'s 72-aircraft default mix. Must be
            reachable by scaling every flow's `count` by the same
            factor and rounding -- extreme values far from 72 will
            distort the role mix (rounding dominates small counts).
    """
    scale = count / sum(f[3] for f in _NOMINAL_FLOWS)
    rng = random.Random(_SEED)
    used_callsigns: set = set()
    placed: List[tuple] = []
    aircraft: List[PresetAircraft] = []

    def far_enough(lat: float, lon: float) -> bool:
        return all(
            geo.haversine_distance_nm(lat, lon, plat, plon) >= _NOMINAL_MIN_SEPARATION_NM
            for plat, plon in placed
        )

    for designator, from_wp, to_wp, base_count, role in _NOMINAL_FLOWS:
        flow_count = max(1, round(base_count * scale))
        route = geo.sub_route(designator, from_wp, to_wp)
        coords = [(lat, lon) for _, lat, lon in route]
        total_nm = geo.polyline_length_nm(coords)
        slot = total_nm / (flow_count + 1)

        for i in range(1, flow_count + 1):
            start = None
            for _attempt in range(25):
                jitter = rng.uniform(-0.3, 0.3) * slot
                distance_nm = max(3.0, min(total_nm - 3.0, i * slot + jitter))
                candidate = geo.advance_from_route_start(coords, distance_nm)
                if far_enough(candidate.lat, candidate.lon):
                    start = candidate
                    break
            if start is None:
                continue  # could not clear 10 NM from existing traffic; drop this slot
            placed.append((start.lat, start.lon))

            if role == "overflight":
                alt_ft = float(rng.choice(_NOMINAL_ALT_BANDS_FT["overflight"]))
                flight_type = "OVERFLIGHT"
            elif role == "arrival":
                alt_ft = float(rng.choice(_NOMINAL_ALT_BANDS_FT["arrival"]))
                flight_type = "LANDING"
            else:  # departure
                alt_ft = float(rng.choice(_NOMINAL_ALT_BANDS_FT["departure"]))
                flight_type = None

            speed_lo, speed_hi = _NOMINAL_SPEED_BAND_KT[role]
            ac_type = rng.choice(
                _TYPES_REGIONAL if role != "overflight" and rng.random() < 0.15 else _TYPES_JET
            )
            aircraft.append(
                {
                    "callsign": _callsign(rng, used_callsigns),
                    "aircraft_type": ac_type,
                    "lat": start.lat, "lon": start.lon, "heading_deg": start.heading_deg,
                    "altitude_ft": alt_ft, "speed_kt": rng.randint(speed_lo, speed_hi),
                    "route_waypoints": start.remaining_waypoints,
                    "flight_type": flight_type,
                }
            )

    return aircraft


# ----------------------------------------------------------------------
# 5. Convergence hotspot -- 5 aircraft, 5 routes, one waypoint, 30 min
# ----------------------------------------------------------------------

#: (designator, far_wp, converge_wp, speed_kt, callsign, aircraft_type).
#: AC is referenced by more distinct airways (5: L644, Q1, W1, W15, W2)
#: than any other waypoint except the TSH/NOB/NAH/CN hub group (see this
#: module's dev notes) -- and unlike TSH (an airport, i.e. every route
#: through it is naturally an arrival/departure funnel, not 5 genuinely
#: different approach *directions*), AC's 5 routes approach from 5
#: distinct real bearings (207-271 deg for four of them, 77 deg -- the
#: opposite side entirely -- for the fifth via L644/TSH), a real
#: multi-directional convergence rather than 5 named designators sharing
#: one physical corridor (checked: e.g. Q1 and W1 both pass through DAN
#: with *identical* point sequences up to that waypoint, which would
#: have made DAN a poor choice for "5 different routes" despite also
#: having 5 designators reference it).
_CONVERGENCE_TARGET_MIN = 30.0
_CONVERGENCE_FLOWS = [
    ("W1", "NOB", "AC", 290, "HVN101", "A321"),
    ("Q1", "NOB", "AC", 300, "VJC202", "A320"),
    ("W2", "NAH", "AC", 280, "QH303", "A319"),
    ("W15", "CRA", "AC", 260, "BAV404", "B738"),
]
#: L644's own TSH -> AC leg is only ~32 NM -- far short of the ~135 NM
#: a 270 kt aircraft covers in 30 min -- so this fifth flow is padded
#: backward past TSH via `scenario_geo.extend_route_backward` (same
#: technique `arrival_sequencing_aircraft` above uses for in-trail
#: spacing, here used for approach-leg *length* instead): a straight
#: continuation of L644's own filed TSH->AC bearing, not a detour
#: through the airport itself.
_CONVERGENCE_FIFTH = ("L644", "TSH", "AC", 270, "PIC505", "B77W")


def convergence_hotspot_aircraft() -> List[PresetAircraft]:
    """5 aircraft on 5 real routes, all reaching AC at exactly t=30 min.

    Each of `_CONVERGENCE_FLOWS`'s four aircraft is placed via
    `scenario_geo.advance_from_route_start` at precisely `speed_kt *
    30/60` NM back from AC along its own real sub-route (`from_wp` ->
    `AC`); the fifth (`_CONVERGENCE_FIFTH`, on L644) is placed the same
    way after first padding its too-short real leg backward past TSH
    (see that constant's docstring). All five therefore arrive at AC's
    exact real coordinates simultaneously at t=30 min -- verified by
    direct `MockConnector` simulation to 0.00 NM residual for all five
    at t=1800s (see this module's validation notes / thesis backlog
    entry for the run).

    All five start well outside `hotspot_dbscan_eps_nm` of each other
    (100+ NM apart at t=0, on five different bearings into AC), so
    unlike `arrival_sequencing_aircraft`/`crossing_airways_aircraft`
    above, no track exists from cycle 1 -- the early cycles instead show
    the normal tracker noise of five otherwise-unrelated aircraft
    occasionally passing within clustering range of each other en route
    (confirmed against a real pipeline run: several short-lived
    PROVISIONAL/CANDIDATE tracks open and dissipate well before AC), not
    a hotspot yet. As the five genuinely close in on AC, a real
    horizon-0 cluster does eventually form (again confirmed by that same
    run, well before t=30 min) and `ResolutionEngine` proposes both
    single- and joint-aircraft candidates from it -- so this preset
    demonstrates the ordinary tracked-cluster path maturing into a
    resolution, same as every preset above, just with a deliberately
    late, single, sharp convergence rather than an already-close pair.
    All five share the same cruise level (35000 ft, level, no vertical
    motion), so the eventual encounter is a genuine 5-way lateral
    conflict at a single point and level, not resolved for free by
    altitude staggering.
    """
    aircraft: List[PresetAircraft] = []

    for designator, far_wp, converge_wp, speed_kt, callsign, ac_type in _CONVERGENCE_FLOWS:
        route = geo.sub_route(designator, far_wp, converge_wp)
        coords = [(lat, lon) for _, lat, lon in route]
        total_nm = geo.polyline_length_nm(coords)
        need_nm = speed_kt * _CONVERGENCE_TARGET_MIN / 60.0
        start = geo.advance_from_route_start(coords, total_nm - need_nm)
        aircraft.append(
            {
                "callsign": callsign, "aircraft_type": ac_type,
                "lat": start.lat, "lon": start.lon, "heading_deg": start.heading_deg,
                "altitude_ft": 35000.0, "speed_kt": speed_kt,
                "route_waypoints": start.remaining_waypoints,
                "flight_type": "OVERFLIGHT",
            }
        )

    designator, far_wp, converge_wp, speed_kt, callsign, ac_type = _CONVERGENCE_FIFTH
    route = geo.sub_route(designator, far_wp, converge_wp)
    coords = [(lat, lon) for _, lat, lon in route]
    total_nm = geo.polyline_length_nm(coords)
    need_nm = speed_kt * _CONVERGENCE_TARGET_MIN / 60.0
    extended = geo.extend_route_backward(coords, need_nm - total_nm)
    start = geo.advance_from_route_start(extended, 0.0)
    aircraft.append(
        {
            "callsign": callsign, "aircraft_type": ac_type,
            "lat": start.lat, "lon": start.lon, "heading_deg": start.heading_deg,
            "altitude_ft": 35000.0, "speed_kt": speed_kt,
            "route_waypoints": start.remaining_waypoints,
            "flight_type": "OVERFLIGHT",
        }
    )
    return aircraft


# ----------------------------------------------------------------------
# 6. Solvable conflict -- TSH departure x PLK arrival, crossing at BMT, 20 min
# ----------------------------------------------------------------------

_SOLVABLE_TARGET_MIN = 20.0
_SOLVABLE_DEPARTURE_SPEED_KT = 250.0
_SOLVABLE_ARRIVAL_SPEED_KT = 270.0
_SOLVABLE_LEVEL_OFF_FT = 10000.0  # FL100 -- both aircraft meet here, not just laterally


def solvable_conflict_aircraft() -> List[PresetAircraft]:
    """A TSH departure and a PLK arrival, both reaching BMT (and FL100) at exactly t=20 min.

    **Departure** (`VJC777`): placed on W1 flown TSH -> BMT (the real
    airway's own filed order is MEVON -> BMT -> ... -> TSH; `sub_route`
    auto-reverses -- see its docstring) at the point exactly
    `_SOLVABLE_DEPARTURE_SPEED_KT` * 20/60 NM short of BMT. Spawns
    directly at FL100 -- this connector has no "climb away from an
    airport, level at cruise" profile (only "descend into an airport"
    via `flight_type="LANDING"`, or "hold level across a route" via
    `flight_type="OVERFLIGHT"`), so rather than animate an indefinite
    climb this preset simply presents the departure already established
    at the conflict altitude, holding FL100 through the conflict.

    **Arrival** (`HVN888`): its final destination is PLK, but BMT and
    PLK are not connected by any single filed airway in the current
    dataset (checked: no `airways.json` route lists both) -- W1 passes
    through BMT, a *different* set of routes (G474, L628, B202) touch
    PLK, and none bridge the two directly. This leg is therefore a
    **straight-line approximation** between BMT and PLK's own real
    coordinates (both genuine published points, just not linked by a
    filed segment between them), built with `scenario_geo.
    extend_route_backward([BMT, PLK], ...)` -- flagged here rather than
    silently presented as a real airway leg. Also spawns directly at
    FL100 (same reasoning as the departure above) and holds it through
    BMT and on toward PLK (`route_waypoints` ends at PLK, but this
    preset does not model the final FL100 -> FL000 landing segment --
    same simplification as `nominal_sector_traffic_aircraft`'s
    arrivals not being flown all the way to a landing here either).

    Both aircraft are at the *same* FL100 from spawn, reach BMT's exact
    real coordinates simultaneously at t=1200s (0.00 NM residual,
    verified by direct `MockConnector` simulation), and start ~66 NM
    (departure) / 90 NM (arrival) apart at t=0 -- outside
    `hotspot_dbscan_eps_nm`, so like `convergence_hotspot_aircraft` this
    exercises the proactive multi-horizon forecast path, not
    horizon-0 clustering. A genuine lateral conflict at a known
    time/place/level: `ResolutionEngine` has a real encounter to
    resolve, with both a heading-based and an altitude-based fix
    equally available (see this preset's own validation run for what
    the engine actually proposes).
    """
    route1 = geo.sub_route("W1", "TSH", "BMT")
    coords1 = [(lat, lon) for _, lat, lon in route1]
    total1 = geo.polyline_length_nm(coords1)
    need1 = _SOLVABLE_DEPARTURE_SPEED_KT * _SOLVABLE_TARGET_MIN / 60.0
    spawn1 = geo.advance_from_route_start(coords1, total1 - need1)

    bmt = geo.waypoint_latlon("W1", "BMT")
    plk = geo.waypoint_latlon("W1", "PLK")
    need2 = _SOLVABLE_ARRIVAL_SPEED_KT * _SOLVABLE_TARGET_MIN / 60.0
    extended2 = geo.extend_route_backward([bmt, plk], need2)
    spawn2 = geo.advance_from_route_start(extended2, 0.0)

    return [
        {
            "callsign": "VJC777", "aircraft_type": "A320",
            "lat": spawn1.lat, "lon": spawn1.lon, "heading_deg": spawn1.heading_deg,
            "altitude_ft": _SOLVABLE_LEVEL_OFF_FT, "speed_kt": _SOLVABLE_DEPARTURE_SPEED_KT,
            "route_waypoints": spawn1.remaining_waypoints,
            "flight_type": None,
        },
        {
            "callsign": "HVN888", "aircraft_type": "A321",
            "lat": spawn2.lat, "lon": spawn2.lon, "heading_deg": spawn2.heading_deg,
            "altitude_ft": _SOLVABLE_LEVEL_OFF_FT, "speed_kt": _SOLVABLE_ARRIVAL_SPEED_KT,
            "route_waypoints": spawn2.remaining_waypoints,
            "flight_type": "LANDING",
        },
    ]
