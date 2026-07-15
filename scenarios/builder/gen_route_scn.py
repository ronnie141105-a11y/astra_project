#!/usr/bin/env python3
"""
Generate a BlueSky .scn route block (DEFWPT + CRE + ADDWPT + LNAV/VNAV)
for one aircraft flying a leg of a real published airway.

Why this exists
----------------
ASTRA already ships the real Vietnam AIP airway geometry (ENR 3.1) at
``astra/dashboard/geo/airways.json`` -- it's used by the dashboard's
Scenario Builder for the offline `MockConnector`. This script reuses the
exact same data to build hand-checkable BlueSky stack commands for a
*live* BlueSky session, so you don't have to manually look up and retype
lat/lon pairs for every waypoint on a route.

Nothing here talks to ASTRA's engines or BlueSky's network layer -- it
only reads the static JSON and prints/writes plain-text .scn lines. Feed
the output into a scenario file (played with `IC scenfile`) or paste it
directly into the BlueSky console.

Examples
--------
Arrival, flying W1 southbound from XAQUA, over PLK, ending at BMT,
landing at VVTS (Tan Son Nhat)::

    python3 scripts/gen_route_scn.py \\
        --airway W1 --from XAQUA --to BMT \\
        --callsign VJC123 --type A320 --alt 30000 --spd 280 \\
        --start-lat 12.90 --start-lon 108.30 --start-hdg 200 \\
        --dest VVTS

Departure, flying W1 northbound out of the HCM area toward PLK
(reversed order -- the script detects this automatically from
--from/--to's positions in the published waypoint list)::

    python3 scripts/gen_route_scn.py \\
        --airway W1 --from BMT --to PLK \\
        --callsign HVN204 --type A321 --alt 31000 --spd 270 \\
        --start-lat 10.90 --start-lon 106.30 --start-hdg 20

Append straight into a scenario file instead of printing to stdout::

    python3 scripts/gen_route_scn.py ... >> scenarios/my_scenario.scn

Notes
-----
* ``--start-lat/--start-lon/--start-hdg`` is the aircraft's initial `CRE`
  position -- typically NOT the first named waypoint (e.g. an arrival
  spawned still short of the airway, en route to intercept it). If you
  want it to spawn exactly on the first named waypoint instead, just
  pass that waypoint's own lat/lon (printed in the script's stderr
  summary) as the start position and a heading toward the second one.
* ``--dest`` (an ICAO airport code) is optional. If given, it's issued
  as a plain `DEST acid,ICAO` *after* the last `ADDWPT` -- BlueSky's own
  FMS logic re-engages LNAV/VNAV on `DEST`, so this also acts as a
  reconfirmation, not just a landing clearance.
* All waypoint coordinates come straight from ``airways.json``, which is
  itself sourced from the official Vietnam AIP (ENR 3.1) -- see that
  file's ``source`` property. `DEFWPT` (not the bare waypoint name) is
  used so the route doesn't depend on whichever nav database your local
  BlueSky install happens to ship with.
"""

import argparse
import json
import os
import sys
from typing import List, Tuple

_AIRWAYS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "astra",
    "dashboard",
    "geo",
    "airways.json",
)


def _load_airway(designator: str) -> Tuple[List[str], List[Tuple[float, float]]]:
    """Return `(waypoint_names, [(lat, lon), ...])` for one airway designator."""
    with open(_AIRWAYS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for feature in data.get("features", []):
        if feature.get("properties", {}).get("designator") == designator:
            names = feature["properties"].get("waypoints", [])
            coords = [(lat, lon) for lon, lat in feature["geometry"]["coordinates"]]
            if len(names) != len(coords):
                raise ValueError(
                    f"Airway {designator!r}: {len(names)} waypoint names but "
                    f"{len(coords)} coordinates -- data mismatch, check airways.json."
                )
            return names, coords
    available = sorted(
        f["properties"].get("designator", "?") for f in data.get("features", [])
    )
    raise SystemExit(
        f"Airway {designator!r} not found. Available: {', '.join(available)}"
    )


def _leg(names: List[str], coords, from_wp: str, to_wp: str):
    """Slice `names`/`coords` to the inclusive leg between two named waypoints.

    Direction is inferred from whichever index comes first -- flying the
    airway "backwards" (e.g. a departure leaving in the opposite sense to
    how the airway is published) is handled automatically.
    """
    try:
        i_from = names.index(from_wp)
        i_to = names.index(to_wp)
    except ValueError as exc:
        raise SystemExit(
            f"{exc}. Waypoints on this airway: {', '.join(names)}"
        ) from exc
    if i_from <= i_to:
        idx_range = range(i_from, i_to + 1)
    else:
        idx_range = range(i_from, i_to - 1, -1)
    return [(names[i], coords[i][0], coords[i][1]) for i in idx_range]


def build_scn_lines(
    leg: List[Tuple[str, float, float]],
    callsign: str,
    actype: str,
    alt_ft: float,
    spd_kt: float,
    start_lat: float,
    start_lon: float,
    start_hdg: float,
    dest: str = None,
    timestamp: str = "00:00:00.00",
) -> List[str]:
    """Build the ordered list of .scn lines for one aircraft's route.

    `leg[0]` is treated as the first FMS waypoint (added via ADDWPT, not
    as the spawn point) -- `start_lat/lon/hdg` is where `CRE` actually
    places the aircraft, which is normally short of the airway itself.
    """
    lines = []
    ts = timestamp

    # DEFWPT for every named point on this leg -- guarantees the route
    # matches the AIP data exactly regardless of the local nav database.
    for name, lat, lon in leg:
        lines.append(f"{ts}>DEFWPT {name},{lat:.6f},{lon:.6f},FIX")

    lines.append(
        f"{ts}>CRE {callsign},{actype},{start_lat:.6f},{start_lon:.6f},"
        f"{start_hdg:.1f},{alt_ft:.0f},{spd_kt:.0f}"
    )
    for name, _lat, _lon in leg:
        lines.append(f"{ts}>ADDWPT {callsign},{name}")
    if dest:
        lines.append(f"{ts}>DEST {callsign},{dest}")
    lines.append(f"{ts}>LNAV {callsign},ON")
    lines.append(f"{ts}>VNAV {callsign},ON")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--airway", required=True, help="Airway designator, e.g. W1")
    parser.add_argument("--from", dest="from_wp", required=True, help="First waypoint of the leg (inclusive)")
    parser.add_argument("--to", dest="to_wp", required=True, help="Last waypoint of the leg (inclusive)")
    parser.add_argument("--callsign", required=True)
    parser.add_argument("--type", dest="actype", required=True, help="ICAO aircraft type, e.g. A320")
    parser.add_argument("--alt", dest="alt_ft", type=float, required=True, help="Altitude in feet")
    parser.add_argument("--spd", dest="spd_kt", type=float, required=True, help="Speed in knots")
    parser.add_argument("--start-lat", type=float, required=True)
    parser.add_argument("--start-lon", type=float, required=True)
    parser.add_argument("--start-hdg", type=float, required=True, help="Initial heading in degrees")
    parser.add_argument("--dest", default=None, help="Optional destination airport ICAO code")
    parser.add_argument("--time", default="00:00:00.00", help="Scenario timestamp for every line")
    args = parser.parse_args()

    names, coords = _load_airway(args.airway)
    leg = _leg(names, coords, args.from_wp, args.to_wp)

    lines = build_scn_lines(
        leg=leg,
        callsign=args.callsign,
        actype=args.actype,
        alt_ft=args.alt_ft,
        spd_kt=args.spd_kt,
        start_lat=args.start_lat,
        start_lon=args.start_lon,
        start_hdg=args.start_hdg,
        dest=args.dest,
        timestamp=args.time,
    )
    print("\n".join(lines))

    print(
        f"# {args.callsign}: {args.airway} leg {args.from_wp} -> {args.to_wp} "
        f"({len(leg)} waypoints)",
        file=sys.stderr,
    )
    for name, lat, lon in leg:
        print(f"#   {name:8s} {lat:.6f}, {lon:.6f}", file=sys.stderr)


if __name__ == "__main__":
    main()
