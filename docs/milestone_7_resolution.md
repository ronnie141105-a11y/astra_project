# Milestone 7 — AI Resolution Framework (`astra.resolution`)

## Scope

Closes the gap Milestone 6 deliberately left open: for each open,
urgency-ranked `FourDArhac`, generate a small set of candidate ATC
clearances (speed / flight-level / heading), estimate each candidate's
effect on that track's complexity by replaying the existing kinematic
and complexity machinery on a hypothetical snapshot, and rank the
candidates — using `TrajectoryEngine` → `ClusterEngine` →
`ComplexityEngine` (Milestones 2–4) unchanged, applied hypothetically
rather than to the live-observed traffic. `ResolutionEngine` is
stateless — it does not own tracks; it is called once per eligible
track, per poll cycle, after `ForecastEngine.forecast_many()` has
already run in the same cycle.

This document supersedes the original Milestone 7 engineering design
review (approved and implemented as recommended; the review document
itself has been superseded by this as-built record). The five open
design questions the review raised (OQ-1 through OQ-5, referenced
throughout below) were all resolved as recommended.

## Design decision — composed `ResolutionSet`, not a `FourDArhac` field (OQ-1)

A new `ResolutionSet` (`astra/resolution/models.py`) *has-a* `FourDArhac`
(`track`) plus its ranked `List[ResolutionCandidate]`, rather than a
field bolted directly onto `FourDArhac`. Mirrors `ComplexityRegion`'s
composition over `Cluster` (Milestone 4): a track can have several
ranked candidates at once, a fundamentally different shape from
Milestone 6's single scalar fields, and bolting a list onto `FourDArhac`
would make `astra.tracking`/`astra.forecast`'s already-tested mutation
contract ambiguous about who is allowed to write to it. `FourDArhac`
itself is untouched by this milestone — no new fields, no schema
changes, `tests/test_tracking.py` (44/44) and `tests/test_forecast.py`
(47/47) are unaffected.

`ResolutionSet.best()` returns the top-ranked candidate (or `None` if
none were generated); `__len__` returns the candidate count.

## Design decision — speed / flight-level / heading only, no direct-to (OQ-2)

`astra/resolution/candidates.py` generates a fixed, discrete set per
track (one step size per lever, not a continuous optimizer, consistent
with every prior milestone's thesis-appropriate scope):

- **Speed** — `+resolution_speed_step_kt` on the track's
  highest-conflict-contributing aircraft (see `select_target_aircraft`
  below). Always generated.
- **Flight level** — `+resolution_altitude_step_ft` on the same
  aircraft. Always generated.
- **Heading** — `+resolution_heading_step_deg` on the same aircraft.
  Only generated if `heading_lever_applicable()` — i.e. the track's
  matched region's complexity has a nonzero MTCA/LTCA component, since
  heading is the most direct lever on a predicted conflict specifically,
  not on density/diversity drivers.

Direct-to candidates are explicitly out of scope: `MockConnector` has no
`DCT`-equivalent stack command (only `CRE/DEL/OP/HOLD/SPD/ALT/HDG/VS`),
so a direct-to candidate could not be demonstrated end-to-end offline
the way every other feature in this project has been.

`select_target_aircraft()` is a documented proxy for "highest-
complexity-score-contributing aircraft": `ComplexityRegion` has no
per-aircraft score breakdown, so it reuses the same pairwise MTCA/LTCA
machinery `ComplexityEngine` already runs (`astra.complexity.conflict`)
and picks the cluster member involved in the most conflict pairs. If no
member is in any conflict pair (a density/diversity-only cluster), it
falls back to the alphabetically first callsign — a deterministic,
documented simplification, since no other per-aircraft contribution
signal exists anywhere in the pipeline.

## Design decision — replay the existing pipeline on a hypothetical snapshot (OQ-3)

`ResolutionEngine._evaluate()` applies one candidate's clearance to a
**copy** of the current observed `TrafficSnapshot` (never the live one
— `astra/resolution/candidates.py::_apply_clearance` builds a new
`AircraftState` via `dataclasses.replace` and a new `TrafficSnapshot`
dict, following the Milestone 1 frozen-state convention), then re-runs
`TrajectoryEngine.predict()` → `ClusterEngine.detect()` on the resulting
prediction at the track's evaluated horizon, and re-associates the
result back to the track's cluster via
`astra.tracking.association.best_cluster_match` — the same primitive
`astra.forecast.horizon_series` already uses for matching. If the
hypothetical cluster cannot be re-associated (the modelled clearance
moved the aircraft out of the cluster entirely), the candidate is scored
with `complexity_after = None` and `complexity_delta_norm = 0.0` rather
than discarded, so it still surfaces its deviation/fuel cost. No new
complexity or trajectory math was written — `ComplexityEngine`,
`ClusterEngine`, and `TrajectoryEngine` are reused completely unchanged.

## Design decision — weighted multi-objective `resolution_score` (OQ-4)

```
resolution_score = w_complexity * complexity_delta_norm
                  - w_deviation * deviation_cost_norm
                  - w_fuel      * fuel_cost_proxy_norm
```

- `complexity_delta_norm` — `(before - after) / before`, clipped to
  `[0, 1]`; `0.0` if `before` is `0` or `after` is `None`.
- `deviation_cost_norm` — the clearance's own magnitude normalised
  against its configured step (`|Δvalue| / step`), a proxy for
  operational cost since no flight-plan leg data exists to compute a
  real route-deviation distance (the same "no ADS-C/EPP data"
  limitation already documented in `Developer_Handover.md`).
- `fuel_cost_proxy_norm` — altitude-change magnitude for flight-level
  candidates, `0.0` otherwise; explicitly not a real fuel-burn model.
- Weights (`resolution_weight_complexity/deviation/fuel`) are validated
  to sum to `1.0` in `ASTRAConfig.__post_init__`, following
  `complexity_weight_*`'s existing pattern exactly.

This is a documented heuristic, not a claim of operational validity —
the same caveat Milestone 4/6 already carry for their own scoring
formulas.

## Design decision — urgency-bounded, single-horizon evaluation (OQ-5)

Only tracks with `status in {CONFIRMED, GROWING, PEAK, DISSIPATING}`
**and** `forecast_urgency_rank is not None` **and** `predicted_onset_s
is not None` **and** a non-empty `track` history are eligible
(`ResolutionEngine._eligible`) — mirroring `ForecastEngine`'s own
`_FORECASTABLE_STATUSES` plus requiring an actual predicted onset worth
acting on. Each eligible candidate is evaluated at exactly one horizon —
the configured horizon closest to `track.predicted_onset_s`
(`_closest_horizon`), not all five — bounding the cost of re-running the
pipeline per candidate. `resolve_many()` additionally sorts eligible
tracks by `forecast_urgency_rank` and caps the count resolved per cycle
at `resolution_max_tracks_per_cycle`, an explicit safety valve mirroring
`history_length`'s role as a bounded-resource config value.

## Config additions (`ASTRAConfig`, Phase 7 section)

| Field | Default | Meaning |
|---|---|---|
| `resolution_speed_step_kt` | `20.0` | Magnitude of the speed candidate's adjustment. Validated `> 0`. |
| `resolution_altitude_step_ft` | `1000.0` | Magnitude of the flight-level candidate's adjustment. Validated `> 0`. |
| `resolution_heading_step_deg` | `15.0` | Magnitude of the heading candidate's adjustment. Validated `> 0`. |
| `resolution_weight_complexity` | `0.6` | Weight on complexity-delta in `resolution_score`. |
| `resolution_weight_deviation` | `0.25` | Weight on deviation cost (penalty). |
| `resolution_weight_fuel` | `0.15` | Weight on the fuel-cost proxy (penalty). |
| `resolution_max_tracks_per_cycle` | `5` | Safety cap on tracks resolved per poll cycle (OQ-5). Validated `>= 1`. |

`resolution_weight_*` are validated to sum to `1.0` in
`ASTRAConfig.__post_init__`, following the same fail-fast pattern as
`complexity_weight_*`.

## Verification

`tests/test_resolution.py` (39 checks): `ResolutionSet.best()`/`__len__`
on populated and empty candidate lists; `select_target_aircraft`
single-member, no-resolvable-member, conflict-based selection, and the
alphabetical no-conflict fallback; `heading_lever_applicable` true/false
on conflict vs. non-conflict components; `generate_candidates` producing
2 candidates (no conflict driver), 3 candidates (with one), and `[]`
(no resolvable target); hypothetical-snapshot construction verified for
all three levers (including heading's modulo-360 wraparound) and that
the original snapshot is never mutated; `ResolutionEngine._eligible`
rejecting `CANDIDATE`/`CLOSED` status, missing urgency rank, and missing
predicted onset; a missing matched region yielding an empty (not
`None`) `ResolutionSet`; `_closest_horizon` selecting the nearest
configured horizon to a track's predicted onset; `resolve_many`
filtering, urgency-ordering, and capping tracks; and a full end-to-end
happy path — a real converging 3-aircraft geometry (observed complexity
below the onset threshold, 5-minute predicted horizon above it) run
through the real `TrajectoryEngine`/`ClusterEngine`/`ComplexityEngine`,
producing ranked, scored candidates via `resolve()` and `resolve_many()`.
Combined with Milestones 3–6 (24/24, 42/42, 44/44, 47/47), the full
suite is 196/196.

`demo_resolution.py` extends `demo_forecast.py`'s scripted-cycle style:
three aircraft on a converging geometry (tuned so the observed cluster
starts below `forecast_onset_threshold` but the 5-minute predicted
horizon already crosses it) go `CANDIDATE → CONFIRMED`, at which point
`ForecastEngine` assigns a predicted onset and urgency rank and
`ResolutionEngine` immediately generates and prints ranked candidate
clearances (clearance type, target aircraft, delta, before/after
complexity, and score) for the most urgent track each cycle.

## Smoke test performed before writing the formal suite

Before writing `tests/test_resolution.py`, a standalone end-to-end
script drove the full pipeline (`StateReader.for_mock` →
`TrajectoryEngine` → `ClusterEngine` → `ComplexityEngine` →
`TrackerEngine` → `ForecastEngine` → `ResolutionEngine`) across several
manual `poll()` cycles, first with the Milestone 5/6 demo's
near-stationary geometry (which never crosses the forecast onset
threshold from below, since it starts *above* it — so
`forecast_urgency_rank` stays `None` and `ResolutionEngine` never has an
eligible track) and then with a converging geometry tuned so the
observed complexity starts below `forecast_onset_threshold` while the
5-minute predicted horizon crosses above it. The tuned scenario produced
non-trivial, correctly-ranked candidates with no exceptions anywhere in
the six-stage pipeline, confirming the wiring was sound before any
formal tests were written; the same tuned geometry was then reused (via
a shared `_build_regions_by_horizon`/`_converging_snapshot` helper
pattern) for both `tests/test_resolution.py`'s end-to-end checks and
`demo_resolution.py`. No production code changes were required as a
result of the smoke test — `astra/resolution/{models,candidates,engine}.py`
were correct as implemented.

## Explicit non-goals (carried over from the design review)

No dashboard/HMI changes (Milestone 8) — candidates are computed and
ranked, not displayed or issued as live clearances. No automatic
clearance issuance to BlueSky/`MockConnector` — every candidate is
advisory only. No real fuel-burn or route-deviation-distance model —
proxies only, documented above. Direct-to candidates deferred pending a
`MockConnector` capability gap (OQ-2). No change to `ForecastEngine`,
`TrackerEngine`, or any earlier package's public API.

## `main.py` — deliberately not integrated

Same reasoning as Milestones 5/6's "`main.py` — deliberately not
integrated": each milestone's functionality is demonstrated through its
own `demo_*.py` script, and `main.py` stays a stable Phase 1 reference
point until the Dashboard milestone (8) gives the full pipeline an
actual live-loop consumer.
