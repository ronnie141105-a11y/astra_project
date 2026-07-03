# Milestone 4 — Complexity Assessment (`astra.complexity`)

## Scope

Takes the `Cluster` objects from Milestone 3 and, for each one, computes
a 0–100 `complexity_score` plus a dict of raw diagnostic components,
returned as an immutable `ComplexityRegion`. `ComplexityEngine` is
stateless and per-instant — like `ClusterEngine`, it retains no memory of
a region's score across horizons or poll cycles. Tracking a trend over
time is Milestone 5/6's job, which will consume a *sequence* of
`ComplexityRegion` objects this engine produces one at a time.

`ComplexityRegion` uses **composition, not inheritance**: it *has* a
`Cluster` rather than extending it, keeping spatial clustering fully
decoupled from complexity scoring.

## Raw components

| Key | Meaning | Reference-literature analogue |
|---|---|---|
| `density_ac_per_nm2` | aircraft count / (π · extent²) | `ρ` / DENSITY |
| `mtca_count` | pairwise Medium-Term Conflict Alerts | `NMTCA` |
| `ltca_count` | pairwise Long-Term Conflict Alerts (excl. MTCA) | `NLTCA` |
| `heading_div_deg` | circular std. dev. of member headings | `σ_hdg` / HDGSTDDEV |
| `alt_div_ft` | population std. dev. of member altitudes | — (extension) |
| `type_mix_count` | distinct aircraft types in the cluster | `Ncat` / NOAT |

## MTCA/LTCA via closest point of approach (CPA)

Both reference documents define MTCA (`dmin < 5.5 NM`, `t_conf < 2.5 min`)
and LTCA (`dmin < 7.9 NM`, `t_conf < 15 min`, excluding MTCA pairs) over a
pair's predicted closest point of approach.

Two aircraft at constant velocity have a closed-form CPA. Great-circle
geometry has no simple closed form for two moving points, so
`astra.utils.geodesy.local_tangent_plane_nm` projects both onto a local
flat-Earth East/North tangent plane anchored at the cluster centroid
first (equirectangular, longitude scaled by `cos(lat0)`); the
approximation error is negligible at cluster scale (≪ 100 NM).
`astra.complexity.conflict.closest_point_of_approach` then minimises
`|r + rv·t|²` over `t`, giving `t = -(r·rv)/|rv|²` (clamped to `t ≥ 0`:
if already diverging, CPA is "now", at the current separation). Verified
against four hand-computable cases in `tests/test_complexity.py`
(head-on, parallel/non-converging, diverging, perpendicular crossing).

This uses each aircraft's *instantaneous* heading/speed at the assessed
snapshot, not a full re-prediction — an intentional simplification
consistent with `TrajectoryEngine`'s constant-velocity model.

## Circular statistics for heading diversity

Ordinary standard deviation doesn't understand wrap-around (350° and 10°
are 20° apart on a compass, ~340° apart arithmetically).
`astra.complexity.stats.circular_std_dev_deg` uses the standard
circular-statistics definition (Mardia & Jupp): headings as unit
vectors, averaged; the mean resultant length `R ∈ [0,1]` converts to
spread via `sqrt(-2·ln(R))`. This diverges as `R → 0` (headings spread
uniformly around the compass), so it is capped at 180°, the maximum
meaningful angular spread — a diversity signal, not a precise angle in
that regime.

## Score combination

The reference ASTRA system decorrelates its metric set with PCA fitted
on a multi-year historical reference dataset, then combines with a
quadratic mean (`framework_for_predict_and_resolve_hotspot.md` §2.4.2).
That calibration needs historical data this thesis-scale prototype does
not have.

This engine instead normalises each raw component linearly against a
fixed reference (saturation) value from `ASTRAConfig`
(`complexity_*_reference_*`) and combines the five resulting 0–100
sub-scores with configurable weights (`complexity_weight_*`, sum to 1.0,
enforced in `ASTRAConfig.__post_init__`). MTCA and LTCA counts are first
folded into one "conflict" sub-score
(`complexity_mtca_weight_in_conflict` / `..._ltca_...`, sum to 1.0)
before entering the weighted combination, since both represent the same
underlying driver at different time horizons.

This is a **documented simplification**, not a claim of equivalence to
the literature's PCA/quadratic-mean method — see "Known limitations" in
`Developer_Handover.md`.

## Verification

`tests/test_complexity.py` (42 checks): tangent-plane projection against
hand-computed offsets; four CPA geometries; MTCA/LTCA classification and
pairwise counting; circular/linear standard deviation edge cases
(identical, wrap-around, uniform-cap, empty); end-to-end
`Cluster → ComplexityRegion` on a synthetic 3-aircraft/2-type scenario;
normalisation saturation; mismatched-snapshot `KeyError`; and config
weight-sum validation.

Demonstration: `demo_complexity.py`.
