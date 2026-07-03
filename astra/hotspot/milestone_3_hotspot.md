# Milestone 3 — Cluster Detection (`astra.hotspot`)

## Scope

Detects purely spatial groupings of aircraft — a `Cluster` — from a
single traffic snapshot: either the current observed `TrafficSnapshot`
or one predicted horizon's `PredictedSnapshot` (Milestone 2 output).

`ClusterEngine` is stateless and knows nothing beyond the one snapshot
it is given. It does not compare clusters across horizons or poll
cycles, and assigns no persistent identity to a cluster.

## Why `Cluster` is not the 4DARHAC

The July 2026 architecture review (`architecture.md` §6) identified that
the original "hotspot detection" phase conflated two different concerns:

- **Spatial clustering** (this milestone) — stateless, pure, re-derived
  fresh from a single snapshot every time.
- **Temporal linkage** — deciding whether a cluster observed at one
  horizon/poll is "the same" persistent area as one observed earlier.
  This is a stateful tracking problem, out of scope here and reserved
  for **Milestone 5** (4DARHAC detection).

Consequently `Cluster.cluster_id` (`"{source}:{horizon_min}:{label}"`) is
only unique *within one* `ClusterEngine.detect()` call and is rebuilt
from scratch every call. It must never be read as "the same cluster as
last poll cycle" — that comparison doesn't exist yet.

## Neighbourhood definition and the distance matrix

DBSCAN takes one scalar distance and one `eps`. ASTRA's neighbourhood
definition is two-dimensional: two aircraft are neighbours only if
within **both** a horizontal threshold (`separation_horizontal_nm`,
15 NM, great-circle) **and** a vertical threshold
(`separation_vertical_ft`, 1000 ft) simultaneously — same 15 NM/1000 ft
definition used in both reference ASTRA documents.

Blending horizontal and vertical distance into one scalar would let a
large vertical separation be "compensated" by horizontal closeness (or
vice versa) — physically wrong for airspace separation. Instead,
`astra.hotspot.distance.build_distance_matrix` precomputes a pairwise
matrix: haversine NM distance if within the vertical gate, else a large
finite sentinel (`1e9`; must be finite because `sklearn.cluster.DBSCAN`
rejects `inf` in a precomputed matrix, and must exceed any realistic
`eps` — Earth's circumference is ~21,600 NM). DBSCAN then runs with
`metric="precomputed"` and `eps=separation_horizontal_nm`: the
horizontal threshold is enforced by DBSCAN, the vertical threshold by
the matrix, and neither can substitute for the other.

## Centroid and extent

`centroid_lat`/`centroid_lon` are simple arithmetic means — an adequate
approximation at the sub-tens-of-NM cluster extents this system operates
on, not a proper spherical centroid. `horizontal_extent_nm` is the
maximum great-circle distance from centroid to any member, used as a
simple cluster "radius" by Milestone 4 (density) and reserved for
Milestone 5 (track association) and Milestone 8 (HMI rendering).

## Verification

`tests/test_hotspot.py` (24 checks): distance-matrix symmetry and
vertical-gate enforcement; basic 2-of-3-aircraft clustering; vertical
gate rejecting an otherwise-close pair; empty/singleton snapshots;
`dbscan_min_samples` respected; `TypeError` on unsupported input;
`detect_all()` producing independent per-horizon results on a
converging-then-diverging synthetic scenario; and API parity between
`TrafficSnapshot` and `PredictedSnapshot` inputs.

Demonstration: `demo_hotspot.py`.
