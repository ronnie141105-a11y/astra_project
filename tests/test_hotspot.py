"""
Regression tests — Milestone 3 (Cluster detection, `astra.hotspot`).

Run with:
    python3 tests/test_hotspot.py

No BlueSky process and no third-party test framework required. Exits
non-zero if any check fails (see `tests/_runner.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from astra.hotspot.distance import build_distance_matrix
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.trajectory.engine import TrajectoryEngine
from astra.utils.config import ASTRAConfig
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


def test_distance_matrix(r: Runner) -> None:
    """`build_distance_matrix` enforces the vertical gate correctly."""
    aircraft = [
        _ac("A1", 47.0, 8.0, 35000.0),
        _ac("A2", 47.0, 8.1, 35000.0),   # ~4.1 NM east, same alt
        _ac("A3", 47.0, 8.1, 37000.0),   # same position as A2, 2000 ft above
    ]
    matrix = build_distance_matrix(aircraft, vertical_separation_ft=1000.0)

    r.check("distance matrix is symmetric", np.allclose(matrix, matrix.T))
    r.check("diagonal is zero", np.allclose(np.diag(matrix), 0.0))
    r.check(
        "A1-A2 (same alt) is a small finite NM distance",
        0.0 < matrix[0, 1] < 10.0,
    )
    r.check(
        "A1-A3 (2000 ft apart) exceeds the vertical gate -> huge distance",
        matrix[0, 2] > 1.0e6,
    )
    r.check(
        "A2-A3 (same lat/lon, 2000 ft apart) also exceeds the vertical gate",
        matrix[1, 2] > 1.0e6,
    )


def test_cluster_engine_basic(r: Runner) -> None:
    """Two nearby aircraft cluster; a third, far-away aircraft is noise."""
    config = ASTRAConfig()
    engine = ClusterEngine(config)

    snapshot = TrafficSnapshot(
        timestamp_s=100.0,
        aircraft={
            "AC1": _ac("AC1", 47.00, 8.00, 35000.0, t=100.0),
            "AC2": _ac("AC2", 47.00, 8.05, 35000.0, t=100.0),  # ~2 NM away
            "AC3": _ac("AC3", 10.00, 10.00, 35000.0, t=100.0),  # far away -> noise
        },
    )
    clusters = engine.detect(snapshot)

    r.check("exactly one cluster detected", len(clusters) == 1)
    if clusters:
        cluster = clusters[0]
        r.check(
            "cluster contains AC1 and AC2, not AC3",
            cluster.member_callsigns == frozenset({"AC1", "AC2"}),
        )
        r.check("cluster source is 'observed'", cluster.source == "observed")
        r.check("cluster horizon_min is 0", cluster.horizon_min == 0)
        r.check_close("cluster valid_at_s matches snapshot", cluster.valid_at_s, 100.0)
        r.check(
            "cluster_id encodes source:horizon:label",
            cluster.cluster_id.startswith("observed:0:"),
        )
        r.check("len(cluster) == 2", len(cluster) == 2)
        expected_lat = (47.00 + 47.00) / 2.0
        r.check_close("centroid latitude is the mean", cluster.centroid_lat, expected_lat)


def test_cluster_engine_vertical_gate(r: Runner) -> None:
    """Two horizontally-close aircraft 2000 ft apart do NOT cluster."""
    config = ASTRAConfig()
    engine = ClusterEngine(config)
    snapshot = TrafficSnapshot(
        timestamp_s=0.0,
        aircraft={
            "AC1": _ac("AC1", 47.0, 8.0, 33000.0),
            "AC2": _ac("AC2", 47.0, 8.01, 35000.0),  # very close horizontally, 2000ft above
        },
    )
    clusters = engine.detect(snapshot)
    r.check("no cluster forms across the vertical gate", len(clusters) == 0)


def test_cluster_engine_empty_and_singleton(r: Runner) -> None:
    """Empty snapshots and lone aircraft produce no clusters, not errors."""
    config = ASTRAConfig()
    engine = ClusterEngine(config)

    empty = TrafficSnapshot(timestamp_s=0.0, aircraft={})
    r.check("empty snapshot -> empty cluster list", engine.detect(empty) == [])

    lone = TrafficSnapshot(timestamp_s=0.0, aircraft={"AC1": _ac("AC1", 47.0, 8.0, 35000.0)})
    r.check(
        "single aircraft -> no cluster (min_samples=2)",
        engine.detect(lone) == [],
    )


def test_cluster_engine_min_samples_config(r: Runner) -> None:
    """`dbscan_min_samples` is read from config, not hardcoded."""
    config = ASTRAConfig(dbscan_min_samples=3)
    engine = ClusterEngine(config)
    snapshot = TrafficSnapshot(
        timestamp_s=0.0,
        aircraft={
            "AC1": _ac("AC1", 47.00, 8.00, 35000.0),
            "AC2": _ac("AC2", 47.00, 8.02, 35000.0),
        },
    )
    r.check(
        "min_samples=3 with only 2 nearby aircraft -> no cluster",
        engine.detect(snapshot) == [],
    )


def test_cluster_engine_rejects_wrong_type(r: Runner) -> None:
    """`detect()` raises TypeError for unsupported snapshot types."""
    config = ASTRAConfig()
    engine = ClusterEngine(config)
    r.check_raises(
        "detect(None) raises TypeError",
        lambda: engine.detect(None),  # type: ignore[arg-type]
        TypeError,
    )


def test_cluster_engine_predicted_snapshots(r: Runner) -> None:
    """`detect_all()` clusters every predicted horizon independently."""
    config = ASTRAConfig(prediction_horizons_min=[5, 10])
    reader = StateReader.for_mock(config, sim_step_s=1.0)
    reader.connect()
    # Two aircraft converging head-on, 400 kt each (800 kt closing speed).
    # Initial separation chosen (~130 NM, along the 47 deg N parallel) so
    # that they are still >15 NM apart at T+5 min but have crossed to
    # within 15 NM by T+10 min -- i.e. NOT clustered at horizon 5,
    # clustered at horizon 10. See tests/test_hotspot.py docstring math:
    # separation(t_min) = D0 - (800/60)*t_min.
    reader.create_aircraft("CV1", "A320", 47.00, 8.00, 90.0, 35000.0, 400.0)
    reader.create_aircraft("CV2", "A320", 47.00, 11.178, 270.0, 35000.0, 400.0)
    reader.send_command("OP")
    snapshot = reader.poll() or reader.current()

    trajectory_engine = TrajectoryEngine(config)
    cluster_engine = ClusterEngine(config)

    prediction = trajectory_engine.predict(snapshot)
    clusters_by_horizon = cluster_engine.detect_all(prediction)

    r.check(
        "detect_all covers every configured horizon",
        set(clusters_by_horizon.keys()) == {5, 10},
    )
    for horizon_min, clusters in clusters_by_horizon.items():
        for cluster in clusters:
            r.check(
                f"horizon {horizon_min}: cluster.source == 'predicted'",
                cluster.source == "predicted",
            )
            r.check(
                f"horizon {horizon_min}: cluster.horizon_min matches",
                cluster.horizon_min == horizon_min,
            )
    r.check(
        "still >15 NM apart at T+5 min -> not yet clustered",
        len(clusters_by_horizon[5]) == 0,
    )
    r.check(
        "converged to within 15 NM by T+10 min -> now clustered",
        len(clusters_by_horizon[10]) == 1
        and clusters_by_horizon[10][0].member_callsigns == frozenset({"CV1", "CV2"}),
    )


def test_cluster_engine_observed_vs_predicted_api_parity(r: Runner) -> None:
    """`ClusterEngine.detect()` treats TrafficSnapshot and PredictedSnapshot identically."""
    config = ASTRAConfig(prediction_horizons_min=[5])
    reader = StateReader.for_mock(config, sim_step_s=1.0)
    reader.connect()
    # Same heading and speed (parallel, non-converging) so the pair's
    # separation is preserved from T+0 through T+5 -- isolates the
    # "does detect() treat both snapshot types identically" question from
    # any kinematics, unlike the converging scenario above.
    reader.create_aircraft("P1", "A320", 47.00, 8.00, 90.0, 35000.0, 300.0)
    reader.create_aircraft("P2", "A320", 47.00, 8.02, 90.0, 35000.0, 300.0)
    reader.send_command("OP")
    snapshot = reader.poll() or reader.current()

    trajectory_engine = TrajectoryEngine(config)
    cluster_engine = ClusterEngine(config)

    observed_clusters = cluster_engine.detect(snapshot)
    predicted_clusters = cluster_engine.detect(
        trajectory_engine.predict(snapshot).at(5)
    )
    r.check(
        "same code path clusters both observed and predicted snapshots",
        len(observed_clusters) == 1 and len(predicted_clusters) == 1,
    )


def main() -> None:
    r = Runner("Milestone 3 — Cluster detection (astra.hotspot)")
    test_distance_matrix(r)
    test_cluster_engine_basic(r)
    test_cluster_engine_vertical_gate(r)
    test_cluster_engine_empty_and_singleton(r)
    test_cluster_engine_min_samples_config(r)
    test_cluster_engine_rejects_wrong_type(r)
    test_cluster_engine_predicted_snapshots(r)
    test_cluster_engine_observed_vs_predicted_api_parity(r)
    r.summary()


if __name__ == "__main__":
    main()
