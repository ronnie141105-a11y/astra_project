"""
Unit conversion helpers.

Why this module exists
-----------------------
BlueSky's *internal* aircraft arrays (bs.traf.alt, bs.traf.tas, ...) are
stored in SI units (metres, metres/second). ATM practice -- and the rest of
this thesis project, the dissertation text, and the reference ASTRA
documents -- works in feet, knots and feet-per-minute. The BlueSky adapter
layer (astra.interface.bluesky_connector) is the only place that ever sees
raw BlueSky values, and it converts everything to ATM units immediately
using the functions below, so every other module in the codebase (and
every later phase) can assume feet / knots / fpm / nautical miles
throughout and never has to think about BlueSky's internal representation.

Keeping these as plain functions (rather than importing BlueSky's own
`bluesky.tools.aero` constants) also means `astra.utils` has zero
dependency on BlueSky being installed, which matters because this package
is documented as importable in isolation (e.g. by unit tests that never
touch the network layer).
"""

#: 1 metre in feet.
METERS_TO_FEET: float = 3.280839895
#: 1 metre/second in knots.
MPS_TO_KNOTS: float = 1.9438444924
#: 1 metre/second in feet-per-minute.
MPS_TO_FPM: float = 196.850393701
#: 1 nautical mile in metres (definitional, exact).
NM_TO_METERS: float = 1852.0


def meters_to_feet(value_m: float) -> float:
    """Convert a length/altitude from metres to feet.

    Args:
        value_m: Value in metres.

    Returns:
        The equivalent value in feet.
    """
    return value_m * METERS_TO_FEET


def feet_to_meters(value_ft: float) -> float:
    """Convert a length/altitude from feet to metres.

    Args:
        value_ft: Value in feet.

    Returns:
        The equivalent value in metres.
    """
    return value_ft / METERS_TO_FEET


def mps_to_knots(value_mps: float) -> float:
    """Convert a speed from metres/second to knots.

    Args:
        value_mps: Value in metres per second.

    Returns:
        The equivalent value in knots.
    """
    return value_mps * MPS_TO_KNOTS


def knots_to_mps(value_kt: float) -> float:
    """Convert a speed from knots to metres/second.

    Args:
        value_kt: Value in knots.

    Returns:
        The equivalent value in metres per second.
    """
    return value_kt / MPS_TO_KNOTS


def mps_to_fpm(value_mps: float) -> float:
    """Convert a vertical speed from metres/second to feet-per-minute.

    Args:
        value_mps: Value in metres per second.

    Returns:
        The equivalent value in feet per minute.
    """
    return value_mps * MPS_TO_FPM


def nm_to_meters(value_nm: float) -> float:
    """Convert a distance from nautical miles to metres.

    Args:
        value_nm: Value in nautical miles.

    Returns:
        The equivalent value in metres.
    """
    return value_nm * NM_TO_METERS


def meters_to_nm(value_m: float) -> float:
    """Convert a distance from metres to nautical miles.

    Args:
        value_m: Value in metres.

    Returns:
        The equivalent value in nautical miles.
    """
    return value_m / NM_TO_METERS
