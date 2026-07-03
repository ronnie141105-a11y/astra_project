# Milestone 5 — 4DARHAC Detection / Tracking (`astra.tracking`)

## Scope

Links the per-instant `ComplexityRegion`s that Milestone 4 recomputes
from scratch every poll cycle into a persistent `FourDArhac`: a stable
identity that survives across poll cycles, with a mechanically-derived
lifecycle status. `TrackerEngine` is the first genuinely **stateful**
component in the pipeline — every earlier engine (`ClusterEngine`,
`ComplexityEngine`) is stateless after construction and safe to share;
`TrackerEngine` owns mutable state (the current set of open tracks) for
its entire lifetime, and each `update()` call is seeded by the previous
call's result — the self-loop in `docs/architecture.md` §6.4.

This milestone follows the build plan recorded in `docs/architecture.md`
§6.5. Three concrete design decisions were made while implementing it,
documented below.

## Design decision — horizon 0 only drives identity

`TrackerEngine.update()` accepts `regions_by_horizon: Dict[int,
List[ComplexityRegion]]`, matching the API sketch in §6.5 exactly.
However, only `horizon_min == 0` (the observed snapshot) is consumed for
track identity, membership, and lifecycle status in this milestone.

Rationale: a `FourDArhac`'s wall-clock identity should be anchored to
what was actually observed, not to a same-cycle *prediction* of where
traffic might be in 5/10/30/60 minutes — those predictions are
recomputed from scratch every poll cycle by `TrajectoryEngine` and
`ClusterEngine`/`ComplexityEngine` (as demonstrated by `demo_hotspot.py`
and `demo_complexity.py`), so folding them into a track's permanent
history would mix genuine observed history with disposable forecasts
that get overwritten next cycle. The explicit non-goals in §6.5 ("no
onset/peak/dissipation *time* prediction", "no confidence modelling
beyond a placeholder field") point the same way: reasoning about
predicted horizons belongs to Milestone 6 (4DARHAC forecast), which will
consume a *confirmed* track's observed history plus the current cycle's
predicted horizons to estimate onset/peak/dissipation times.

Non-zero horizons are still accepted in the `update()` signature — kept
schema-stable now, per the same rationale `ASTRAConfig` uses for
not-yet-consumed fields — so Milestone 6 can extend `TrackerEngine`
without changing its call sites in `main.py`/demos. Milestone 5 simply
does not read them yet. See `test_tracker_ignores_non_zero_horizons_for_identity`
in `tests/test_tracking.py`.

## Design decision — association: Jaccard primary, centroid/extent fallback

`astra/tracking/association.py` implements exactly the heuristic
proposed in §6.2, as two pure functions plus a selector, mirroring
`astra.hotspot.distance`'s pattern of independently-testable pure math
feeding a stateful engine:

- `jaccard_similarity(a, b)` — `|intersection| / |union|` of two
  callsign sets.
- `centroid_extent_overlap(cluster_a, cluster_b)` — `True` if the
  great-circle distance between centroids is no greater than the sum of
  both clusters' `horizontal_extent_nm` (their bounding circles touch or
  overlap).
- `best_track_match(new_cluster, candidate_tracks, jaccard_threshold)` —
  for each candidate, scores Jaccard similarity against the *most
  recent* entry on that track. Any candidate clearing
  `tracking_jaccard_threshold` is eligible; among those, highest
  Jaccard wins, ties broken by smaller centroid distance. If nothing
  clears the threshold, candidates whose centroid/extent circles overlap
  the new cluster are considered instead, smallest centroid distance
  wins. No match on either signal → `None` (a new track is opened).

`TrackerEngine.update()` assigns matches **greedily and one-to-one per
cycle**: it processes this cycle's observed clusters in the order given,
and once a track is claimed it is removed from the candidate pool for
the rest of that cycle. This is a documented simplification, not a
globally-optimal assignment (e.g. the Hungarian algorithm) — reasonable
at DBSCAN cluster counts in the tens, and consistent with the
thesis-scale scope of the rest of the prototype.

## Design decision — status lifecycle from trend, not thresholds

`ArhacStatus` is `CANDIDATE → CONFIRMED → GROWING → PEAK → DISSIPATING →
CLOSED`, exactly as specified in §6.2/§6.5. `TrackerEngine._next_status`
derives it mechanically from two consecutive `complexity_score` values
plus the track's current status (a tiny finite-state machine, not a
lookup table):

| Condition | Next status |
|---|---|
| Fewer than `tracking_confirm_cycles` entries so far | `CANDIDATE` |
| Just reached `tracking_confirm_cycles` entries | `CONFIRMED` |
| Score rose by more than `tracking_trend_tolerance` | `GROWING` |
| Score fell by more than `tracking_trend_tolerance`, previous status was `GROWING` | `PEAK` |
| Score fell by more than `tracking_trend_tolerance`, otherwise | `DISSIPATING` |
| Roughly flat (within `tracking_trend_tolerance`), previous status was `GROWING` | `PEAK` |
| Roughly flat, otherwise | unchanged (holds current phase) |

`tracking_confirm_cycles` damps single-cycle DBSCAN noise from spawning
a spurious track, as specified. This is **trend classification only** —
no time-based forecasting. `predicted_onset_s`, `predicted_dissipation_s`
stay `None`, and `confidence` is a simple placeholder
(`min(1.0, detections / tracking_confirm_cycles)`, ramping to 1.0 as a
track survives its confirmation window) rather than a calibrated
forecast confidence. Both are explicit non-goals of this milestone (see
`docs/architecture.md` §6.5) and are reserved for Milestone 6.

`priority` (FMP triage rank, 1 = highest `peak_complexity`) is
recomputed across every open track on every `update()` call —
inexpensive at prototype scale, and avoids the staleness bugs a
cached/incremental ranking would risk.

## Staleness and closing

Each open track's missed-cycle count increments whenever it is not
matched in a given `update()` call, and resets to zero on a match.
Once a track's missed-cycle count reaches `tracking_stale_cycles`, it is
marked `CLOSED`, evicted from the engine's open-track set, and returned
**once** — the `update()` call that closes it — after which it is gone
entirely (not returned as `CLOSED` again on later calls, since the
engine no longer holds a reference to it). This matches §6.5: *"number
of poll cycles a track may go un-refreshed before being closed"*.

## Config additions (`ASTRAConfig`, Phase 5 section)

| Field | Default | Meaning |
|---|---|---|
| `tracking_jaccard_threshold` | `0.5` | Minimum Jaccard similarity to accept a primary association match. Validated to `(0.0, 1.0]`. |
| `tracking_stale_cycles` | `3` | Consecutive un-refreshed poll cycles before closing. Validated `>= 1`. |
| `tracking_confirm_cycles` | `2` | Consecutive detections required for `CANDIDATE → CONFIRMED`. Validated `>= 1`. |
| `tracking_trend_tolerance` | `1.0` | Score delta (0–100 scale) below which two entries count as "flat". Validated `>= 0`. |

All four are validated in `ASTRAConfig.__post_init__`, following the
same fail-fast pattern as the Phase 4 weight-sum checks.

## Verification

`tests/test_tracking.py` (44 checks): `jaccard_similarity` and
`centroid_extent_overlap` on hand-built cases; `best_track_match`
primary/fallback/no-match selection; single-track creation and field
seeding; `CANDIDATE → CONFIRMED` promotion; stable `arhac_id` and
`member_aircraft` union across cycles; a full scripted multi-cycle
lifecycle (`CANDIDATE → CONFIRMED → GROWING → PEAK → DISSIPATING →
CLOSED`) with exact status assertions at every cycle; two independent
tracks ranked by `priority`; confirmation that non-zero horizons do not
affect identity/peak; and `ASTRAConfig` validation for all four new
fields. Combined with Milestones 3–4 (24/24, 42/42), the full suite is
110/110.

`demo_tracking.py` drives `MockConnector` through nine-plus manual
`poll()` cycles (30 s steps), using near-stationary aircraft so that
scripted `HDG`/`ALT`/`SPD` stack commands — not incidental kinematics —
produce a deterministic, observable lifecycle end to end, including the
formation genuinely dissolving (verified dynamically each cycle, not
assumed on a fixed schedule) and the resulting `CLOSED` transition.

## Explicit non-goals (carried over from `docs/architecture.md` §6.5)

No onset/peak/dissipation *time* prediction, no calibrated confidence
model, no resolution suggestions, no dashboard/HMI changes. All belong
to later milestones — see `docs/milestone_6_forecast_design_review.md`
for the Milestone 6 review this milestone's completion unblocks.

## `main.py` — deliberately not integrated

Milestones 2–4 did not wire `TrajectoryEngine`, `ClusterEngine`, or
`ComplexityEngine` into `main.py`'s live loop either — each milestone's
functionality is demonstrated through its own `demo_*.py` script instead
(`demo_trajectory.py`, `demo_hotspot.py`, `demo_complexity.py`), leaving
`main.py` as a Phase 1 (data interface) demonstration only. Milestone 5
follows the same, already-established precedent for the same reasons:
`main.py`'s live loop is a thin, stable, always-working reference point
("keep previous demos working" applies to it too), and premature
wiring-together of the full pipeline there is properly the Dashboard
milestone's job (Milestone 8), once there is a consumer for the combined
output. Revisit this once Milestone 6 (forecast) or Milestone 7
(resolution) exist and a genuine end-to-end live-loop consumer is
justified.
