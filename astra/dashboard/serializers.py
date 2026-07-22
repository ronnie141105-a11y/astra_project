"""
Pure serialization functions (Milestone 8).

Per the design review's proposed module layout, this is the *only* new
"logic" module the dashboard introduces: every function here is a pure,
side-effect-free transform from an existing Milestone 1-7 domain object
(`AircraftState`, `TrafficSnapshot`, `PredictionResult`, `Cluster`,
`ComplexityRegion`, `FourDArhac`, `ResolutionSet`) to a JSON-safe
`dict`/`list`. No prediction, clustering, complexity, tracking,
forecasting, or resolution math is performed here -- the dashboard is a
read-only consumer, never a recomputation of what the pipeline already
produced.

`astra.dashboard.routes` is the only caller of `serialize_cycle_result`;
the smaller per-type functions are exposed individually because
`tests/test_dashboard.py` exercises them directly against hand-built
objects, following the Milestone 3-7 test pattern.
"""

from typing import Dict, List, Optional

from astra.complexity.models import ComplexityRegion
from astra.complexity.sector import SectorComplexitySample
from astra.dashboard.models import DashboardSnapshot
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import AircraftState, TrafficSnapshot
from astra.pipeline import CycleResult
from astra.resolution.models import (
    JointResolutionCandidate,
    ResolutionCandidate,
    ResolutionLeg,
    ResolutionSet,
)
from astra.tracking.models import FourDArhac
from astra.trajectory.models import PredictionResult
from astra.utils.config import ASTRAConfig


def serialize_aircraft(aircraft: AircraftState) -> Dict:
    """One `AircraftState` -> a JSON-safe dict for the traffic-map panel."""
    return {
        "callsign": aircraft.callsign,
        "lat": aircraft.lat,
        "lon": aircraft.lon,
        "altitude_ft": aircraft.altitude_ft,
        "ground_speed_kt": aircraft.ground_speed_kt,
        "heading_deg": aircraft.heading_deg,
        "vertical_speed_fpm": aircraft.vertical_speed_fpm,
        "aircraft_type": aircraft.aircraft_type,
    }


def serialize_snapshot(snapshot: TrafficSnapshot) -> Dict:
    """The observed `TrafficSnapshot` -> the traffic-map panel's live layer."""
    return {
        "timestamp_s": snapshot.timestamp_s,
        "aircraft": [serialize_aircraft(ac) for ac in snapshot.as_list()],
    }


def serialize_prediction(prediction: PredictionResult) -> Dict[str, List[Dict]]:
    """`PredictionResult` -> one predicted-position path per aircraft.

    Reshaped from the engine's own `{horizon_min: PredictedSnapshot}`
    layout (grouped by horizon) into `{callsign: [points...]}` (grouped
    by aircraft), because the map panel draws one predicted-trajectory
    polyline per aircraft, not one marker layer per horizon. Each point
    carries its own `horizon_min` so the frontend can still label
    individual horizons along the line.

    Returns:
        `{callsign: [{"horizon_min", "lat", "lon", "altitude_ft"}, ...]}`,
        each aircraft's points ordered by ascending `horizon_min`. An
        aircraft absent from a given horizon (e.g. it left the area of
        interest in a future scenario) simply has fewer points.
    """
    paths: Dict[str, List[Dict]] = {}
    for horizon_min in prediction.horizon_list():
        predicted_snapshot = prediction.at(horizon_min)
        for aircraft in predicted_snapshot:
            paths.setdefault(aircraft.callsign, []).append(
                {
                    "horizon_min": horizon_min,
                    "lat": aircraft.lat,
                    "lon": aircraft.lon,
                    "altitude_ft": aircraft.altitude_ft,
                }
            )
    for points in paths.values():
        points.sort(key=lambda point: point["horizon_min"])
    return paths


def serialize_cluster(cluster: Cluster) -> Dict:
    """One `Cluster` -> a JSON-safe dict (centroid + extent + membership)."""
    return {
        "cluster_id": cluster.cluster_id,
        "source": cluster.source,
        "horizon_min": cluster.horizon_min,
        "valid_at_s": cluster.valid_at_s,
        "member_callsigns": sorted(cluster.member_callsigns),
        "centroid_lat": cluster.centroid_lat,
        "centroid_lon": cluster.centroid_lon,
        "centroid_alt_ft": cluster.centroid_alt_ft,
        "horizontal_extent_nm": cluster.horizontal_extent_nm,
    }


def serialize_region(region: ComplexityRegion) -> Dict:
    """One `ComplexityRegion` -> its `Cluster` plus score/components, for the heatmap."""
    return {
        "cluster": serialize_cluster(region.cluster),
        "complexity_score": region.complexity_score,
        "components": dict(region.components),
        "computed_at_s": region.computed_at_s,
    }


def serialize_regions_by_horizon(
    regions_by_horizon: Dict[int, List[ComplexityRegion]]
) -> Dict[int, List[Dict]]:
    """`{horizon_min: [ComplexityRegion, ...]}` -> the same shape, JSON-safe.

    Horizon `0` (observed) is what the initial heatmap panel renders
    (design review OQ-4(A)); the other horizons are included too since
    they cost nothing extra to serialize and let the frontend offer a
    "predicted hotspot" horizon selector later without any backend change.
    """
    return {
        horizon_min: [serialize_region(region) for region in regions]
        for horizon_min, regions in regions_by_horizon.items()
    }


def serialize_track(track: FourDArhac) -> Dict:
    """One `FourDArhac` -> the hotspot table/timeline panel's row.

    Includes a `history` series (`[{time_s, complexity_score}]`, oldest
    first) taken directly from `track.track` -- the same data the
    Milestone 5/6 lifecycle is built from -- for the timeline panel's
    onset/peak/dissipation plot. The most recent entry's `Cluster`
    supplies the track's current centroid for the map/table panels.

    A `"PROVISIONAL"` track (see astra.tracking.engine's module
    docstring) has no real observations yet, so `track.track` is empty
    and every field above stays at its existing "nothing observed"
    default (`current_complexity_score`/`centroid`: `null`, `history`:
    `[]`) -- unchanged, so this remains exactly backward compatible for
    any consumer built before provisional tracks existed. The
    `provisional_*` fields below are purely additive: predicted-only
    equivalents of the same information, present whenever
    `track.provisional_track` is non-empty (which includes *after*
    promotion too -- it is kept as a historical record, see that
    field's docstring -- so `provisional_lead_time_s` remains available
    even once a track is fully real).
    """
    latest_region = track.track[-1] if track.track else None
    latest_provisional = track.provisional_track[-1] if track.provisional_track else None
    return {
        "arhac_id": track.arhac_id,
        "status": track.status,
        "member_aircraft": sorted(track.member_aircraft),
        "priority": track.priority,
        "confidence": track.confidence,
        "peak_complexity": track.peak_complexity,
        "peak_time_s": track.peak_time_s,
        "predicted_onset_s": track.predicted_onset_s,
        "predicted_dissipation_s": track.predicted_dissipation_s,
        "predicted_peak_time_s": track.predicted_peak_time_s,
        "forecast_urgency_rank": track.forecast_urgency_rank,
        "first_detected_cycle_s": track.first_detected_cycle_s,
        "last_updated_cycle_s": track.last_updated_cycle_s,
        "current_complexity_score": (
            latest_region.complexity_score if latest_region is not None else None
        ),
        "centroid": (
            {
                "lat": latest_region.cluster.centroid_lat,
                "lon": latest_region.cluster.centroid_lon,
                "alt_ft": latest_region.cluster.centroid_alt_ft,
            }
            if latest_region is not None
            else None
        ),
        "history": [
            {"time_s": region.computed_at_s, "complexity_score": region.complexity_score}
            for region in track.track
        ],
        "provisional_current_complexity_score": (
            latest_provisional.complexity_score if latest_provisional is not None else None
        ),
        "provisional_centroid": (
            {
                "lat": latest_provisional.cluster.centroid_lat,
                "lon": latest_provisional.cluster.centroid_lon,
                "alt_ft": latest_provisional.cluster.centroid_alt_ft,
            }
            if latest_provisional is not None
            else None
        ),
        "provisional_history": [
            {"time_s": region.computed_at_s, "complexity_score": region.complexity_score}
            for region in track.provisional_track
        ],
        "provisional_lead_time_s": (
            (track.track[0].computed_at_s - track.first_detected_cycle_s)
            if track.track and track.provisional_track
            else None
        ),
    }


def serialize_resolution_candidate(candidate: ResolutionCandidate) -> Dict:
    """One `ResolutionCandidate` -> a JSON-safe dict for the resolution table."""
    return {
        "clearance_type": candidate.clearance_type,
        "target_callsign": candidate.target_callsign,
        "delta_value": candidate.delta_value,
        "complexity_before": candidate.complexity_before,
        "complexity_after": candidate.complexity_after,
        "complexity_delta_norm": candidate.complexity_delta_norm,
        "deviation_cost_norm": candidate.deviation_cost_norm,
        "fuel_cost_proxy_norm": candidate.fuel_cost_proxy_norm,
        "resolution_score": candidate.resolution_score,
        "domino_cost_norm": candidate.domino_cost_norm,
        "maneuver_kind": candidate.maneuver_kind,
        "vector_duration_s": candidate.vector_duration_s,
        "complexity_after_components": (
            dict(candidate.complexity_after_components)
            if candidate.complexity_after_components is not None
            else None
        ),
        "complexity_before_components": (
            dict(candidate.complexity_before_components)
            if candidate.complexity_before_components is not None
            else None
        ),
        "hypothetical_path": (
            serialize_prediction(candidate.hypothetical_prediction).get(
                candidate.target_callsign, []
            )
            if candidate.hypothetical_prediction is not None
            else []
        ),
    }


def serialize_resolution_leg(leg: ResolutionLeg) -> Dict:
    """One `ResolutionLeg` (a joint candidate's per-aircraft clearance) -> a JSON-safe dict."""
    return {
        "target_callsign": leg.target_callsign,
        "clearance_type": leg.clearance_type,
        "delta_value": leg.delta_value,
        "maneuver_kind": leg.maneuver_kind,
        "vector_duration_s": leg.vector_duration_s,
    }


def serialize_joint_resolution_candidate(candidate: JointResolutionCandidate) -> Dict:
    """One `JointResolutionCandidate` -> a JSON-safe dict for the resolution table.

    Same score/complexity fields as `serialize_resolution_candidate`, but
    `legs` (2-3 simultaneous per-aircraft clearances) replaces the
    single `clearance_type`/`target_callsign`/`delta_value` a
    single-aircraft candidate has -- see
    `ResolutionEngine._build_joint_candidate`.
    """
    return {
        "legs": [serialize_resolution_leg(leg) for leg in candidate.legs],
        "complexity_before": candidate.complexity_before,
        "complexity_after": candidate.complexity_after,
        "complexity_delta_norm": candidate.complexity_delta_norm,
        "deviation_cost_norm": candidate.deviation_cost_norm,
        "fuel_cost_proxy_norm": candidate.fuel_cost_proxy_norm,
        "resolution_score": candidate.resolution_score,
        "domino_cost_norm": candidate.domino_cost_norm,
        "complexity_after_components": (
            dict(candidate.complexity_after_components)
            if candidate.complexity_after_components is not None
            else None
        ),
        "complexity_before_components": (
            dict(candidate.complexity_before_components)
            if candidate.complexity_before_components is not None
            else None
        ),
    }


def serialize_resolution_set(resolution_set: ResolutionSet, max_candidates: int) -> Dict:
    """One `ResolutionSet` -> its track id plus its top-ranked candidates.

    Per design review OQ-3(B), the full ranked list is shown (not just
    `.best()`), bounded by `max_candidates`
    (`ASTRAConfig.dashboard_max_resolution_candidates_shown`, a generous
    safety cap -- see that field's docstring for why this should very
    rarely trim anything in practice, and why fixed-size pagination for
    display belongs in the frontend, not here). A track with only 1 or 2
    real candidates is returned with just that many -- this never pads
    the list out to a fixed count. `joint_candidate` (present only for
    3+ member clusters -- see `ResolutionEngine._build_joint_candidate`)
    is never capped/truncated: it is always exactly one candidate or
    absent.
    """
    return {
        "arhac_id": resolution_set.track.arhac_id,
        "evaluated_horizon_min": resolution_set.evaluated_horizon_min,
        "candidates": [
            serialize_resolution_candidate(candidate)
            for candidate in resolution_set.candidates[:max_candidates]
        ],
        "joint_candidate": (
            serialize_joint_resolution_candidate(resolution_set.joint_candidate)
            if resolution_set.joint_candidate is not None
            else None
        ),
    }


def serialize_sector_regions(sector_regions: Dict[str, ComplexityRegion]) -> Dict[str, Dict]:
    """`{sector_name: ComplexityRegion}` -> the same shape, JSON-safe."""
    return {name: serialize_region(region) for name, region in sector_regions.items()}


def serialize_sector_history(
    sector_history: Dict[str, List[SectorComplexitySample]]
) -> Dict[str, List[Dict]]:
    """`{sector_name: [SectorComplexitySample, ...]}` -> the complexity-charts series."""
    return {
        name: [
            {
                "bucket_start_s": sample.bucket_start_s,
                "complexity_score": sample.complexity_score,
                "aircraft_count": sample.aircraft_count,
            }
            for sample in samples
        ]
        for name, samples in sector_history.items()
    }


def serialize_cycle_result(result: CycleResult, config: ASTRAConfig) -> Dict:
    """The full `CycleResult` -> the `/api/state` endpoint's payload body.

    Args:
        result: One pipeline cycle's output.
        config: Only read for `dashboard_max_resolution_candidates_shown`
            (OQ-3's display cap).
    """
    return {
        "snapshot": serialize_snapshot(result.snapshot),
        "prediction": {
            "source_time_s": result.prediction.source_time_s,
            "horizons_min": list(result.prediction.horizons_min),
            "paths": serialize_prediction(result.prediction),
        },
        "regions_by_horizon": serialize_regions_by_horizon(result.regions_by_horizon),
        "tracks": [serialize_track(track) for track in result.tracks],
        "resolution_sets": [
            serialize_resolution_set(
                resolution_set, config.dashboard_max_resolution_candidates_shown
            )
            for resolution_set in result.resolution_sets
        ],
        "sector_regions": serialize_sector_regions(result.sector_regions),
        "sector_history": serialize_sector_history(result.sector_history),
    }


def serialize_dashboard_snapshot(snapshot: DashboardSnapshot, config: ASTRAConfig) -> Dict:
    """A `DashboardSnapshot` (possibly empty) -> the `/api/state` payload.

    This is the top-level function `astra.dashboard.routes` calls. It
    handles the "no cycle has run yet" case that `serialize_cycle_result`
    does not need to know about.
    """
    return {
        "cycle_count": snapshot.cycle_count,
        "updated_at_s": snapshot.updated_at_s,
        "poll_interval_s": config.poll_interval_s,
        "has_data": snapshot.cycle_result is not None,
        "cycle": (
            serialize_cycle_result(snapshot.cycle_result, config)
            if snapshot.cycle_result is not None
            else None
        ),
    }
