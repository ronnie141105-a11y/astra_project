"""
Sector complexity (Milestone 9).

Reuses `ComplexityEngine` unchanged, per the reference ASTRA documents:
sector complexity is computed by treating every aircraft within a
sector's boundary as one big cluster and running the same scoring
pipeline as a 4DARHAC. A `Cluster` is synthesised here rather than
detected by `ClusterEngine` (DBSCAN has no notion of a fixed named
boundary).

Simplification (documented, not hidden): sectors are modelled as
circles (`center_lat/lon` + `radius_nm`), not the arbitrary polygons a
real ANSP would define. This keeps membership/extent computation a
single haversine check, appropriate for a thesis-scale prototype -- a
real polygon sectorization could replace `SectorDefinition` without
changing `SectorComplexityEngine`'s public API.

`SectorComplexityEngine` is the module's only stateful piece: it keeps
a rolling per-sector history of 5-minute-bucketed complexity samples
(the "complexity charts" page's data source), independent of
`TrackerEngine`'s 4DARHAC tracks.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List

from astra.complexity.engine import ComplexityEngine
from astra.complexity.models import ComplexityRegion
from astra.hotspot.models import Cluster
from astra.interface.traffic_state import TrafficSnapshot
from astra.utils.config import ASTRAConfig, SectorDefinition
from astra.utils.geodesy import haversine_distance_nm


@dataclass(frozen=True)
class SectorComplexitySample:
    """One rolling-history entry: a sector's complexity at one bucket time.

    Attributes:
        bucket_start_s: Start of the bucket this sample was recorded in.
        complexity_score: The sector's `ComplexityRegion.complexity_score`.
        aircraft_count: Aircraft inside the sector at sampling time.
    """

    bucket_start_s: float
    complexity_score: float
    aircraft_count: int


def _sector_cluster(sector: SectorDefinition, snapshot: TrafficSnapshot) -> Cluster:
    """Build a `Cluster` covering every aircraft within `sector`'s circle."""
    members = frozenset(
        ac.callsign
        for ac in snapshot.as_list()
        if haversine_distance_nm(sector.center_lat, sector.center_lon, ac.lat, ac.lon)
        <= sector.radius_nm
    )
    return Cluster(
        cluster_id=f"sector:{sector.name}",
        source="observed",
        horizon_min=0,
        valid_at_s=snapshot.timestamp_s,
        member_callsigns=members,
        centroid_lat=sector.center_lat,
        centroid_lon=sector.center_lon,
        centroid_alt_ft=0.0,
        horizontal_extent_nm=sector.radius_nm,
    )


class SectorComplexityEngine:
    """Assesses fixed sectors each cycle and keeps a rolling history per sector.

    Stateful (owns the rolling-history buffers) -- like `TrackerEngine`,
    one instance must persist across poll cycles. No-op (returns `{}`) if
    `config.sectors` is empty, so this is entirely opt-in and does not
    change behaviour for a config with no sectors defined.
    """

    def __init__(self, config: ASTRAConfig) -> None:
        """Initialise from shared config; owns one `ComplexityEngine`."""
        self._config = config
        self._complexity_engine = ComplexityEngine(config)
        self._history: Dict[str, Deque[SectorComplexitySample]] = {
            sector.name: deque(maxlen=config.sector_history_buckets)
            for sector in config.sectors
        }
        self._bucket_start_s: Dict[str, float] = {}

    def update(self, snapshot: TrafficSnapshot) -> Dict[str, ComplexityRegion]:
        """Assess every configured sector against `snapshot` and record history.

        Args:
            snapshot: This cycle's observed `TrafficSnapshot`.

        Returns:
            `{sector_name: ComplexityRegion}` for this cycle (empty if no
            sectors are configured).
        """
        regions: Dict[str, ComplexityRegion] = {}
        for sector in self._config.sectors:
            cluster = _sector_cluster(sector, snapshot)
            region = self._complexity_engine.assess(cluster, snapshot)
            regions[sector.name] = region
            self._record(sector.name, region, snapshot.timestamp_s)
        return regions

    def history(self, sector_name: str) -> List[SectorComplexitySample]:
        """Return `sector_name`'s rolling history, oldest first."""
        return list(self._history.get(sector_name, ()))

    def _record(self, sector_name: str, region: ComplexityRegion, now_s: float) -> None:
        """Append a new bucket, or overwrite the current one, for `sector_name`.

        Buckets are `sector_bucket_s` wide; a cycle landing in the same
        bucket as the previous cycle overwrites that bucket's sample
        (latest-in-bucket wins) rather than appending, keeping the
        history's time axis regularly spaced regardless of poll rate.
        """
        bucket_s = self._config.sector_bucket_s
        bucket_start = (now_s // bucket_s) * bucket_s
        sample = SectorComplexitySample(
            bucket_start_s=bucket_start,
            complexity_score=region.complexity_score,
            aircraft_count=len(region.cluster),
        )
        history = self._history.setdefault(
            sector_name, deque(maxlen=self._config.sector_history_buckets)
        )
        if history and history[-1].bucket_start_s == bucket_start:
            history[-1] = sample
        else:
            history.append(sample)
