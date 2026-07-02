"""
Regression tests — Milestone 4 (Complexity assessment, `astra.complexity`).

Run with:
    python3 tests/test_complexity.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra.complexity.conflict import (
    ClosestApproach,
    classify_conflict,
    closest_point_of_approach,
    count_conflicts,
)
from astra.complexity.engine import ComplexityEngine
from astra.complexity.stats import circular_std_dev_deg, population_std_dev
from astra.hotspot.engine import ClusterEngine
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import local_tangent_plane_nm
from tests._runner import Runner


def _ac(callsign, lat, lon, alt_ft, hdg=0.0, gs_kt=300.0, vs_fpm=0.0, actype="A320", t=0.0):
    """Build an `AircraftState` with sensible defaults for test brevity."""
    return AircraftState(
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=alt_ft,
        ground_speed_kt=gs_kt,
        heading_deg=hdg,
        vertical_speed_fpm=vs_fpm,
        aircraft_type=actype,
        timestamp_s=t,
    )


# ----------------------------------------------------------------------
# astra.utils.geodesy.local_tangent_plane_nm
# ----------------------------------------------------------------------

def test_local_tangent_plane(r: Runner) -> None:
    """Tangent-plane projection matches hand-computed offsets."""
    x, y = local_tangent_plane_nm(47.0, 8.0, 47.0, 8.0)
    r.check_close("origin projects to (0, 0) — x", x, 0.0)
    r.check_close("origin projects to (0, 0) — y", y, 0.0)

    # 1 degree of latitude is ~60 NM (matches _EARTH_RADIUS_NM convention).
    x, y = local_tangent_plane_nm(47.0, 8.0, 48.0, 8.0)
    r.check_close("+1 deg latitude -> ~60 NM north", y, 60.045, tol=0.1)
    r.check_close("+1 deg latitude -> zero east offset", x, 0.0, tol=1e-9)


# ----------------------------------------------------------------------
# astra.complexity.conflict — closest point of approach
# ----------------------------------------------------------------------

def test_cpa_head_on(r: Runner) -> None:
    """Two aircraft closing head-on meet at the midpoint, on time."""
    lat0, lon0 = 47.0, 8.0
    ac1 = _ac("A1", lat0, lon0, 35000.0, hdg=0.0, gs_kt=300.0)
    # ~20 NM north (1/3 deg lat), heading south, closing at 600 kt combined.
    ac2 = _ac("A2", lat0 + 20.0 / 60.0, lon0, 35000.0, hdg=180.0, gs_kt=300.0)

    approach = closest_point_of_approach(lat0, lon0, ac1, ac2)
    r.check_close("head-on: CPA distance is ~0 NM", approach.distance_nm, 0.0, tol=0.05)
    r.check_close("head-on: time-to-CPA is ~2.0 min", approach.time_to_cpa_min, 2.0, tol=0.05)


def test_cpa_parallel_non_converging(r: Runner) -> None:
    """Two aircraft on parallel tracks never close -> CPA is now."""
    lat0, lon0 = 47.0, 8.0
    ac1 = _ac("A1", lat0, lon0, 35000.0, hdg=90.0, gs_kt=400.0)
    ac2 = _ac("A2", lat0, lon0 + 0.5, 35000.0, hdg=90.0, gs_kt=400.0)  # same track, offset

    approach = closest_point_of_approach(lat0, lon0, ac1, ac2)
    r.check("parallel tracks: time-to-CPA is 0 (no future convergence)", approach.time_to_cpa_min == 0.0)
    r.check("parallel tracks: CPA distance equals current separation", approach.distance_nm > 10.0)


def test_cpa_diverging(r: Runner) -> None:
    """Two aircraft already moving apart: CPA is now, at current separation."""
    ac1 = _ac("A1", 47.0, 8.0, 35000.0, hdg=270.0, gs_kt=300.0)  # moving away
    ac2 = _ac("A2", 47.0, 8.1, 35000.0, hdg=90.0, gs_kt=300.0)   # moving away
    approach = closest_point_of_approach(47.0, 8.0, ac1, ac2)
    r.check("diverging aircraft: time-to-CPA is 0", approach.time_to_cpa_min == 0.0)
    r.check("diverging aircraft: CPA distance > 0", approach.distance_nm > 0.0)


def test_cpa_perpendicular_crossing(r: Runner) -> None:
    """Two aircraft on perpendicular crossing courses meet at the crossing point."""
    lat0, lon0 = 47.0, 8.0
    # A1 10 NM west of origin, heading east at 300 kt.
    lat1, lon1 = local_tangent_plane_to_geo(lat0, lon0, -10.0, 0.0)
    ac1 = _ac("A1", lat1, lon1, 35000.0, hdg=90.0, gs_kt=300.0)
    # A2 10 NM south of origin, heading north at 300 kt.
    lat2, lon2 = local_tangent_plane_to_geo(lat0, lon0, 0.0, -10.0)
    ac2 = _ac("A2", lat2, lon2, 35000.0, hdg=0.0, gs_kt=300.0)

    approach = closest_point_of_approach(lat0, lon0, ac1, ac2)
    r.check_close("crossing paths: CPA distance ~0 NM", approach.distance_nm, 0.0, tol=0.1)
    r.check_close("crossing paths: time-to-CPA ~2.0 min", approach.time_to_cpa_min, 2.0, tol=0.05)


def local_tangent_plane_to_geo(lat0_deg, lon0_deg, x_nm, y_nm):
    """Inverse of `local_tangent_plane_nm`, for constructing test fixtures."""
    import math

    lat0 = math.radians(lat0_deg)
    lat_deg = lat0_deg + math.degrees(y_nm / 3440.065)
    lon_deg = lon0_deg + math.degrees(x_nm / (3440.065 * math.cos(lat0)))
    return lat_deg, lon_deg


def test_classify_conflict(r: Runner) -> None:
    """MTCA/LTCA classification against `ASTRAConfig` thresholds."""
    config = ASTRAConfig()
    r.check(
        "within MTCA thresholds -> 'MTCA'",
        classify_conflict(ClosestApproach(3.0, 1.0), config) == "MTCA",
    )
    r.check(
        "within LTCA but outside MTCA thresholds -> 'LTCA'",
        classify_conflict(ClosestApproach(7.0, 10.0), config) == "LTCA",
    )
    r.check(
        "outside both thresholds -> None",
        classify_conflict(ClosestApproach(20.0, 30.0), config) is None,
    )
    r.check(
        "close distance but far in time -> None",
        classify_conflict(ClosestApproach(1.0, 30.0), config) is None,
    )


def test_count_conflicts(r: Runner) -> None:
    """`count_conflicts` tallies pairwise MTCA/LTCA over a group."""
    config = ASTRAConfig()
    lat0, lon0 = 47.0, 8.0
    # Two aircraft in a tight MTCA-range head-on geometry, a third far away
    # and diverging (no conflict contribution).
    ac1 = _ac("A1", lat0, lon0, 35000.0, hdg=0.0, gs_kt=300.0)
    ac2 = _ac("A2", lat0 + 3.0 / 60.0, lon0, 35000.0, hdg=180.0, gs_kt=300.0)
    ac3 = _ac("A3", lat0, lon0 + 2.0, 35000.0, hdg=90.0, gs_kt=300.0)

    mtca_count, ltca_count = count_conflicts([ac1, ac2, ac3], lat0, lon0, config)
    r.check("exactly one MTCA pair (A1-A2)", mtca_count == 1)
    r.check("no LTCA pairs beyond the MTCA pair", ltca_count == 0)


# ----------------------------------------------------------------------
# astra.complexity.stats
# ----------------------------------------------------------------------

def test_circular_std_dev(r: Runner) -> None:
    """Circular standard deviation matches hand-computed cases."""
    r.check_close("identical headings -> 0 spread", circular_std_dev_deg([90.0, 90.0, 90.0]), 0.0, tol=1e-6)
    r.check_close(
        "small spread [80,90,100] -> ~8.18 deg", circular_std_dev_deg([80.0, 90.0, 100.0]), 8.175, tol=0.01
    )
    r.check_close(
        "wrap-around [350,0,10] matches non-wrapped [−10,0,10] spread",
        circular_std_dev_deg([350.0, 0.0, 10.0]),
        circular_std_dev_deg([80.0, 90.0, 100.0]),
        tol=1e-6,
    )
    r.check(
        "opposite headings [0,180] -> capped at 180 deg",
        circular_std_dev_deg([0.0, 180.0]) == 180.0,
    )
    r.check("empty sequence -> 0", circular_std_dev_deg([]) == 0.0)


def test_population_std_dev(r: Runner) -> None:
    """Population standard deviation matches hand-computed cases."""
    r.check_close("constant values -> 0", population_std_dev([35000.0, 35000.0]), 0.0)
    r.check_close("[2,4,4,4,5,5,7,9] -> std dev 2.0", population_std_dev([2, 4, 4, 4, 5, 5, 7, 9]), 2.0, tol=1e-6)
    r.check("single value -> 0", population_std_dev([35000.0]) == 0.0)
    r.check("empty sequence -> 0", population_std_dev([]) == 0.0)


# ----------------------------------------------------------------------
# astra.complexity.engine — end-to-end ComplexityEngine
# ----------------------------------------------------------------------

def test_complexity_engine_end_to_end(r: Runner) -> None:
    """Full Cluster -> ComplexityRegion pipeline on a synthetic scenario."""
    config = ASTRAConfig()
    cluster_engine = ClusterEngine(config)
    complexity_engine = ComplexityEngine(config)

    snapshot = TrafficSnapshot(
        timestamp_s=0.0,
        aircraft={
            "AC1": _ac("AC1", 47.0, 8.0, 35000.0, hdg=0.0, gs_kt=300.0, actype="A320"),
            "AC2": _ac("AC2", 47.0 + 3.0 / 60.0, 8.0, 35000.0, hdg=180.0, gs_kt=300.0, actype="A320"),
            "AC3": _ac("AC3", 47.02, 8.02, 35100.0, hdg=90.0, gs_kt=280.0, actype="B738"),
        },
    )
    clusters = cluster_engine.detect(snapshot)
    r.check("one cluster of all 3 aircraft forms", len(clusters) == 1 and len(clusters[0]) == 3)

    regions = complexity_engine.assess_many(clusters, snapshot)
    r.check("one ComplexityRegion per cluster", len(regions) == 1)

    region = regions[0]
    r.check("complexity_score is within [0, 100]", 0.0 <= region.complexity_score <= 100.0)
    r.check(
        "components contains all six expected keys",
        set(region.components.keys())
        == {
            "density_ac_per_nm2",
            "mtca_count",
            "ltca_count",
            "heading_div_deg",
            "alt_div_ft",
            "type_mix_count",
        },
    )
    r.check("mtca_count reflects the AC1/AC2 head-on geometry", region.components["mtca_count"] >= 1.0)
    r.check("type_mix_count is 2 (A320, B738)", region.components["type_mix_count"] == 2.0)
    r.check_close("computed_at_s matches the cluster's valid_at_s", region.computed_at_s, clusters[0].valid_at_s)
    r.check(
        "ComplexityRegion.cluster is the same object passed in",
        region.cluster is clusters[0],
    )


def test_complexity_engine_saturation(r: Runner) -> None:
    """Component normalisation saturates at 100, never exceeds it."""
    config = ASTRAConfig()
    engine = ComplexityEngine(config)
    r.check_close("normalise(0, ref) == 0", engine._normalise(0.0, 10.0), 0.0)
    r.check_close("normalise(ref, ref) == 100", engine._normalise(10.0, 10.0), 100.0)
    r.check_close("normalise(1000*ref, ref) saturates at 100", engine._normalise(10000.0, 10.0), 100.0)
    r.check_close("normalise(x, 0) is defined as 0 (guards div-by-zero)", engine._normalise(5.0, 0.0), 0.0)


def test_complexity_engine_missing_callsign_raises(r: Runner) -> None:
    """`assess()` raises KeyError if the snapshot doesn't match the cluster."""
    config = ASTRAConfig()
    cluster_engine = ClusterEngine(config)
    complexity_engine = ComplexityEngine(config)

    snapshot = TrafficSnapshot(
        timestamp_s=0.0,
        aircraft={
            "AC1": _ac("AC1", 47.0, 8.0, 35000.0),
            "AC2": _ac("AC2", 47.0, 8.02, 35000.0),
        },
    )
    clusters = cluster_engine.detect(snapshot)
    empty_snapshot = TrafficSnapshot(timestamp_s=0.0, aircraft={})
    r.check_raises(
        "assess() against a mismatched snapshot raises KeyError",
        lambda: complexity_engine.assess(clusters[0], empty_snapshot),
        KeyError,
    )


def test_config_weight_validation(r: Runner) -> None:
    """`ASTRAConfig` rejects complexity weights that don't sum to 1.0."""
    r.check_raises(
        "complexity_weight_* not summing to 1.0 raises ValueError",
        lambda: ASTRAConfig(complexity_weight_density=0.9),
        ValueError,
    )
    r.check_raises(
        "conflict sub-weights not summing to 1.0 raises ValueError",
        lambda: ASTRAConfig(complexity_mtca_weight_in_conflict=0.9),
        ValueError,
    )


def main() -> None:
    r = Runner("Milestone 4 — Complexity assessment (astra.complexity)")
    test_local_tangent_plane(r)
    test_cpa_head_on(r)
    test_cpa_parallel_non_converging(r)
    test_cpa_diverging(r)
    test_cpa_perpendicular_crossing(r)
    test_classify_conflict(r)
    test_count_conflicts(r)
    test_circular_std_dev(r)
    test_population_std_dev(r)
    test_complexity_engine_end_to_end(r)
    test_complexity_engine_saturation(r)
    test_complexity_engine_missing_callsign_raises(r)
    test_config_weight_validation(r)
    r.summary()


if __name__ == "__main__":
    main()
