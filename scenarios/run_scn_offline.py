#!/usr/bin/env python3
"""
Offline .scn scenario runner (thesis data collection).

Drives a BlueSky-format `.scn` file through the full ASTRA pipeline
(Trajectory -> Cluster -> Complexity -> Tracking -> Forecast ->
Resolution) using the offline `MockConnector` -- no live BlueSky
process required. `MockConnector.send_command()` understands the same
CRE / SPD / ALT / HDG / VS / OP / HOLD / DEL stack-command vocabulary
BlueSky itself does (see astra/interface/mock_connector.py), so any
`.scn` file written for this project's scenarios (which deliberately
avoid route/waypoint commands MockConnector doesn't implement) runs
identically offline and against a live BlueSky node -- this script is
a reproducibility aid, not a different scenario.

Usage:
    python3 scripts/run_scn_offline.py scenarios/thesis_converging_hotspot.scn \\
        --duration-min 20 --sim-step-s 15 --out-prefix converging

Produces, under /mnt/user-data/outputs/:
    <out-prefix>_cycles.csv    -- one row per poll cycle, headline metrics
    <out-prefix>_detail.json   -- full per-cycle track/resolution detail
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra.pipeline import Pipeline
from astra.interface.state_reader import StateReader
from astra.utils.config import ASTRAConfig

# Matches a leading BlueSky scenario timestamp, e.g. "00:00:00.00>".
_TIMESTAMP_PREFIX = re.compile(r"^\s*\d{2}:\d{2}:\d{2}(\.\d+)?\s*>\s*")


def load_scn_commands(scn_path: Path) -> List[str]:
    """Strip BlueSky's leading timestamp from each non-blank .scn line."""
    commands = []
    for raw_line in scn_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        command = _TIMESTAMP_PREFIX.sub("", line).strip()
        if command:
            commands.append(command)
    return commands


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scn_path", type=Path)
    parser.add_argument("--duration-min", type=float, default=20.0)
    parser.add_argument("--sim-step-s", type=float, default=15.0)
    parser.add_argument("--out-prefix", type=str, default=None)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("/mnt/user-data/outputs")
    )
    args = parser.parse_args()

    out_prefix = args.out_prefix or args.scn_path.stem
    args.out_dir.mkdir(parents=True, exist_ok=True)

    config = ASTRAConfig()
    reader = StateReader.for_mock(config, sim_step_s=args.sim_step_s)
    reader.connect()

    commands = load_scn_commands(args.scn_path)
    print(f"Loaded {len(commands)} stack commands from {args.scn_path}")
    for command in commands:
        reader.send_command(command)

    pipeline = Pipeline(config, route_provider=reader.get_route)
    n_steps = max(1, int(round(args.duration_min * 60.0 / args.sim_step_s)))

    csv_rows = []
    detail_log = []

    for step_index in range(n_steps):
        snapshot = reader.poll()
        if snapshot is None:
            continue
        result = pipeline.run_cycle(snapshot)

        observed_regions = result.regions_by_horizon.get(0, [])
        max_observed = max((r.complexity_score for r in observed_regions), default=0.0)
        n_clusters_observed = len(observed_regions)

        status_counts = {}
        for track in result.tracks:
            status_counts[track.status] = status_counts.get(track.status, 0) + 1

        n_resolved = sum(1 for rs in result.resolution_sets if rs.candidates)
        best_scores = [
            rs.candidates[0].resolution_score for rs in result.resolution_sets if rs.candidates
        ]

        csv_rows.append(
            {
                "cycle": step_index,
                "sim_time_s": snapshot.timestamp_s,
                "n_aircraft": len(snapshot.aircraft),
                "n_clusters_observed": n_clusters_observed,
                "max_complexity_observed": round(max_observed, 2),
                "n_open_tracks": len(result.tracks),
                "n_confirmed_tracks": status_counts.get("CONFIRMED", 0)
                + status_counts.get("GROWING", 0)
                + status_counts.get("PERSISTENT", 0)
                + status_counts.get("DISSIPATING", 0),
                "n_resolved_tracks": n_resolved,
                "best_resolution_score": round(max(best_scores), 4) if best_scores else "",
            }
        )

        detail_log.append(
            {
                "cycle": step_index,
                "sim_time_s": snapshot.timestamp_s,
                "observed_regions": [
                    {
                        "score": round(r.complexity_score, 2),
                        "members": sorted(r.cluster.member_callsigns),
                        "components": {k: round(v, 3) for k, v in r.components.items()},
                    }
                    for r in observed_regions
                ],
                "tracks": [
                    {
                        "arhac_id": t.arhac_id,
                        "status": t.status,
                        "member_aircraft": sorted(t.member_aircraft),
                        "peak_complexity": round(t.peak_complexity, 2),
                        "predicted_onset_s": t.predicted_onset_s,
                        "forecast_urgency_rank": t.forecast_urgency_rank,
                    }
                    for t in result.tracks
                ],
                "resolution_sets": [
                    {
                        "arhac_id": rs.track.arhac_id,
                        "evaluated_horizon_min": rs.evaluated_horizon_min,
                        "candidates": [
                            {
                                "clearance_type": c.clearance_type,
                                "target_callsign": c.target_callsign,
                                "delta_value": c.delta_value,
                                "domino_cost_norm": round(c.domino_cost_norm, 3),
                                "complexity_delta_norm": round(c.complexity_delta_norm, 3),
                                "deviation_cost_norm": round(c.deviation_cost_norm, 3),
                                "fuel_cost_proxy_norm": round(c.fuel_cost_proxy_norm, 3),
                                "resolution_score": round(c.resolution_score, 4),
                            }
                            for c in rs.candidates
                        ],
                    }
                    for rs in result.resolution_sets
                    if rs.candidates
                ],
            }
        )

    csv_path = args.out_dir / f"{out_prefix}_cycles.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()) if csv_rows else [])
        writer.writeheader()
        writer.writerows(csv_rows)

    json_path = args.out_dir / f"{out_prefix}_detail.json"
    json_path.write_text(json.dumps(detail_log, indent=2))

    print(f"Ran {len(csv_rows)} cycles ({args.duration_min} sim-min at {args.sim_step_s}s/step)")
    print(f"  summary -> {csv_path}")
    print(f"  detail  -> {json_path}")

    max_seen = max((row["max_complexity_observed"] for row in csv_rows), default=0.0)
    total_resolved_cycles = sum(1 for row in csv_rows if row["n_resolved_tracks"])
    print(f"  peak observed complexity: {max_seen}")
    print(f"  cycles with >=1 resolved track: {total_resolved_cycles}/{len(csv_rows)}")


if __name__ == "__main__":
    main()
