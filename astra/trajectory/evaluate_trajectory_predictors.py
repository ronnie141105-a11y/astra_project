#!/usr/bin/env python3
"""
Baseline (dead reckoning) vs route-aware trajectory prediction --
evaluated against independently-simulated ground truth.

Methodology (why this is not circular)
---------------------------------------
1.  At t=0, a set of aircraft is created in ``MockConnector``, some with
    a known route (``route_waypoints``), some without (plain constant
    heading -- the control group).
2.  Both predictors are run *once*, at t=0, from the t=0 snapshot:
        - ``TrajectoryEngine``           (baseline, constant velocity)
        - ``RouteAwareTrajectoryEngine`` (proposed, follows known routes)
    Both only ever see information available at t=0: current kinematic
    state, plus -- for the route-aware engine -- each aircraft's current
    route via ``StateReader.get_route()``. Neither predictor is given,
    or ever reads, any future simulated state.
3.  *Afterwards*, and independently of step 2, the same ``MockConnector``
    instance is stepped forward in small time increments (its own
    internal kinematics -- the same ``advance_along_route`` function,
    but that is a statement about code reuse for physical correctness,
    not about the predictor seeing the future; see
    ``astra/trajectory/route_engine.py``'s module docstring) up to 60
    simulated minutes, recording the *actual* position at each of the
    five configured horizons (5, 10, 15, 30, 60 min).
4.  Each predictor's t=0 prediction for a horizon is compared against
    that horizon's actual recorded position -- a genuine held-out
    evaluation, exactly analogous to comparing a weather forecast made
    this morning against this afternoon's actual weather.

Two things are measured:
    A. Position error (horizontal, NM; vertical, ft) per aircraft per
       horizon, for both predictors.
    B. Hotspot-detection performance: for a pair of aircraft whose
       routes cause them to converge only *after* a turn dead reckoning
       cannot see coming, cluster/complexity is computed on (a) the
       baseline-predicted snapshot, (b) the route-aware-predicted
       snapshot, and (c) the true snapshot, all at the same horizon --
       showing whether each predictor would or would not have raised a
       hotspot warning that actually materialised.
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.complexity.engine import ComplexityEngine
from astra.hotspot.engine import ClusterEngine
from astra.interface.state_reader import StateReader
from astra.trajectory.engine import TrajectoryEngine
from astra.trajectory.route_engine import RouteAwareTrajectoryEngine
from astra.utils.config import ASTRAConfig
from astra.utils.geodesy import haversine_distance_nm


def build_scenario(reader: StateReader):
    """Create the aircraft used for both parts of the evaluation.

    Returns the list of (callsign, has_route) tuples for reference.
    """
    aircraft_specs = []

    # ---- Position-error aircraft: three single-turn "doglegs" ----
    # Each flies straight for a while, then turns onto a second leg at
    # a different angle -- dead reckoning is structurally wrong for all
    # three once the turn is reached; only the turn angle/timing differs.
    reader.create_aircraft(
        "DOGLEG1", "A320", 10.00, 106.00, 90.0, 35000.0, 280.0,
        route_waypoints=[(10.00, 106.467), (10.60, 106.467)],  # turn 90 deg north
    )
    aircraft_specs.append(("DOGLEG1", True))

    reader.create_aircraft(
        "DOGLEG2", "B738", 12.00, 105.50, 45.0, 34000.0, 260.0,
        route_waypoints=[(12.353, 105.853), (12.353, 106.400)],  # turn onto due-east leg
    )
    aircraft_specs.append(("DOGLEG2", True))

    reader.create_aircraft(
        "DOGLEG3", "A319", 9.50, 107.00, 200.0, 33000.0, 240.0,
        route_waypoints=[(9.164, 106.635), (8.700, 106.850)],  # turn ~40 deg left
    )
    aircraft_specs.append(("DOGLEG3", True))

    # ---- Control aircraft: no route, plain constant-velocity flight ----
    # Baseline should match ground truth here (near-)exactly -- confirms
    # the comparison harness itself is fair, not just favourable to the
    # route-aware engine by construction.
    reader.create_aircraft("STRAIGHT1", "B77W", 8.00, 104.50, 60.0, 37000.0, 300.0)
    aircraft_specs.append(("STRAIGHT1", False))

    # ---- Hotspot-detection pair: converge only after both turn ----
    # Dead reckoning has them crossing and diverging (never staying
    # close); their true routes turn them onto a sustained in-trail
    # convergence that stays a real hotspot from h=10min onward.
    reader.create_aircraft(
        "CONV1", "A320", 10.80, 106.20, 90.0, 35000.0, 250.0,
        route_waypoints=[(10.80, 106.55), (11.50, 106.55)],
    )
    aircraft_specs.append(("CONV1", True))
    reader.create_aircraft(
        "CONV2", "B738", 10.90, 106.90, 270.0, 34500.0, 250.0,
        route_waypoints=[(10.90, 106.55), (11.60, 106.55)],
    )
    aircraft_specs.append(("CONV2", True))

    return aircraft_specs


def main() -> None:
    config = ASTRAConfig()
    reader = StateReader.for_mock(config, sim_step_s=10.0)
    reader.connect()

    aircraft_specs = build_scenario(reader)
    reader.send_command("OP")

    snapshot0 = reader.poll()
    print(f"t=0: {len(snapshot0)} aircraft created, sim clock running.")

    baseline_engine = TrajectoryEngine(config)
    route_engine = RouteAwareTrajectoryEngine(config, route_provider=reader.get_route)

    baseline_prediction = baseline_engine.predict(snapshot0)
    route_prediction = route_engine.predict(snapshot0)

    horizons = config.prediction_horizons_min
    max_horizon_s = max(horizons) * 60.0

    # ---- Independently advance the simulation and record ground truth ----
    ground_truth = {}
    t = 0.0
    while t < max_horizon_s:
        snap = reader.poll()
        t = snap.timestamp_s
        for h in horizons:
            target_t = h * 60.0
            if h not in ground_truth and t >= target_t - 1e-6:
                ground_truth[h] = snap
    # Catch any horizon whose exact tick was overshot only by rounding.
    for h in horizons:
        if h not in ground_truth:
            ground_truth[h] = reader.poll()

    print(f"Ground truth captured at all {len(horizons)} horizons "
          f"(sim time {snapshot0.timestamp_s:.0f}s -> {max(ground_truth[h].timestamp_s for h in horizons):.0f}s).")

    # ======================================================================
    # Part A: position error
    # ======================================================================
    error_rows = []
    for callsign, has_route in aircraft_specs:
        for h in horizons:
            true_ac = ground_truth[h].get(callsign)
            base_ac = baseline_prediction.at(h).get(callsign)
            route_ac = route_prediction.at(h).get(callsign)
            if true_ac is None or base_ac is None or route_ac is None:
                continue
            base_err_nm = haversine_distance_nm(base_ac.lat, base_ac.lon, true_ac.lat, true_ac.lon)
            route_err_nm = haversine_distance_nm(route_ac.lat, route_ac.lon, true_ac.lat, true_ac.lon)
            base_err_ft = abs(base_ac.altitude_ft - true_ac.altitude_ft)
            route_err_ft = abs(route_ac.altitude_ft - true_ac.altitude_ft)
            error_rows.append(
                {
                    "callsign": callsign,
                    "has_route": has_route,
                    "horizon_min": h,
                    "baseline_horizontal_error_nm": round(base_err_nm, 3),
                    "route_aware_horizontal_error_nm": round(route_err_nm, 3),
                    "baseline_altitude_error_ft": round(base_err_ft, 1),
                    "route_aware_altitude_error_ft": round(route_err_ft, 1),
                }
            )

    print("\n" + "=" * 100)
    print("PART A -- Position error vs ground truth (NM horizontal / ft vertical)")
    print("=" * 100)
    header = f"{'callsign':10s} {'route?':7s} {'h_min':>6s} {'baseline_NM':>12s} {'route_aware_NM':>15s} {'baseline_ft':>12s} {'route_aware_ft':>15s}"
    print(header)
    print("-" * len(header))
    for row in error_rows:
        print(
            f"{row['callsign']:10s} {str(row['has_route']):7s} {row['horizon_min']:6d} "
            f"{row['baseline_horizontal_error_nm']:12.2f} {row['route_aware_horizontal_error_nm']:15.2f} "
            f"{row['baseline_altitude_error_ft']:12.1f} {row['route_aware_altitude_error_ft']:15.1f}"
        )

    # Aggregate: mean error at each horizon, split by route/no-route.
    print("\nMean horizontal error by horizon (route-following aircraft only, DOGLEG1-3/CONV1-2):")
    for h in horizons:
        route_rows = [r for r in error_rows if r["horizon_min"] == h and r["has_route"]]
        if not route_rows:
            continue
        mean_base = sum(r["baseline_horizontal_error_nm"] for r in route_rows) / len(route_rows)
        mean_route = sum(r["route_aware_horizontal_error_nm"] for r in route_rows) / len(route_rows)
        print(f"  h={h:2d} min:  baseline={mean_base:8.2f} NM   route-aware={mean_route:8.2f} NM")

    # ======================================================================
    # Part B: hotspot-detection performance (CONV1/CONV2)
    # ======================================================================
    clus = ClusterEngine(config)
    comp = ComplexityEngine(config)

    def assess(snapshot):
        clusters = clus.detect(snapshot)
        regions = comp.assess_many(clusters, snapshot)
        conv_region = next(
            (r for r in regions if {"CONV1", "CONV2"} <= set(r.cluster.member_callsigns)), None
        )
        return conv_region.complexity_score if conv_region else 0.0, conv_region is not None

    hotspot_rows = []
    for h in horizons:
        true_score, true_hit = assess(ground_truth[h])
        base_score, base_hit = assess(baseline_prediction.at(h))
        route_score, route_hit = assess(route_prediction.at(h))
        hotspot_rows.append(
            {
                "horizon_min": h,
                "true_complexity": round(true_score, 1),
                "true_cluster_detected": true_hit,
                "baseline_complexity": round(base_score, 1),
                "baseline_cluster_detected": base_hit,
                "route_aware_complexity": round(route_score, 1),
                "route_aware_cluster_detected": route_hit,
            }
        )

    print("\n" + "=" * 100)
    print("PART B -- Hotspot-detection performance (CONV1/CONV2 pair)")
    print("=" * 100)
    header2 = f"{'h_min':>6s} {'true_score':>11s} {'true_hit':>9s} {'baseline_score':>15s} {'baseline_hit':>13s} {'route_score':>12s} {'route_hit':>10s}"
    print(header2)
    print("-" * len(header2))
    for row in hotspot_rows:
        print(
            f"{row['horizon_min']:6d} {row['true_complexity']:11.1f} {str(row['true_cluster_detected']):>9s} "
            f"{row['baseline_complexity']:15.1f} {str(row['baseline_cluster_detected']):>13s} "
            f"{row['route_aware_complexity']:12.1f} {str(row['route_aware_cluster_detected']):>10s}"
        )

    # ---- Save results ----
    out_dir = Path("/mnt/user-data/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "trajectory_position_error.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(error_rows[0].keys()))
        writer.writeheader()
        writer.writerows(error_rows)

    with (out_dir / "trajectory_hotspot_detection.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(hotspot_rows[0].keys()))
        writer.writeheader()
        writer.writerows(hotspot_rows)

    (out_dir / "trajectory_evaluation_summary.json").write_text(
        json.dumps({"position_error": error_rows, "hotspot_detection": hotspot_rows}, indent=2)
    )

    print(f"\nSaved: {out_dir / 'trajectory_position_error.csv'}")
    print(f"Saved: {out_dir / 'trajectory_hotspot_detection.csv'}")
    print(f"Saved: {out_dir / 'trajectory_evaluation_summary.json'}")


if __name__ == "__main__":
    main()
