# Milestone 7 — AI Resolution Framework: Engineering Design Review

**Status: DRAFT**
This document is a design review only, prepared per the standing project
convention (see Milestone 6's design review, `docs/milestone_6_forecast_design_review.md`,
which was written and approved before Milestone 6 was implemented).
Implementation must not begin until this review is explicitly approved
and this status line is updated.

---

## 1. Purpose and recap

Milestone 6 (`astra.forecast`) gives every open, confirmed `FourDArhac`
an estimated `predicted_onset_s`, `predicted_dissipation_s`,
`predicted_peak_time_s`, a composite `confidence`, and a
`forecast_urgency_rank` — but it only ever *describes* the problem. It
does not suggest anything an FMP (Flow Management Position) could act
on to reduce a track's complexity or delay/avoid its predicted onset.

Milestone 7 closes that gap: for each open, forecastable `FourDArhac`,
generate a small set of candidate ATC clearances (speed / flight-level /
heading / direct-to), estimate each candidate's effect on that track's
complexity, and produce a ranked list — using the same kinematic and
complexity machinery the rest of the pipeline (Milestones 2–4) already
trusts, applied hypothetically rather than to the live simulation.

## 2. What Milestone 7 has to work with (already built, unchanged)

- `FourDArhac.track`, `.member_aircraft`, `.status`, `.peak_complexity`,
  `.predicted_onset_s` / `.predicted_dissipation_s` /
  `.predicted_peak_time_s` / `.confidence` / `.forecast_urgency_rank` —
  everything Milestone 6 already computes, identifying *which* tracks
  are worth generating resolutions for and *how urgently*.
- `TrajectoryEngine.predict()` — deterministic constant-velocity
  projection from any `TrafficSnapshot`, already used to build the
  predicted horizons that drive Milestones 3–6. Milestone 7's only new
  use of it is applying it to a **hypothetically modified** snapshot
  (one aircraft's speed/heading/altitude changed) rather than the
  live-observed one.
- `ClusterEngine.detect()` / `ComplexityEngine.assess()` — the same
  pure, stateless functions already used every cycle; re-runnable on a
  hypothetical snapshot with no new code, since neither holds state.
- `astra.tracking.association.jaccard_similarity` /
  `centroid_extent_overlap` — the exact primitives needed to answer "is
  the region in this hypothetical snapshot still the same track?",
  exactly as Milestone 6 reused them for predicted horizons.
- `AircraftState` (frozen) — `ground_speed_kt`, `heading_deg`,
  `altitude_ft`, `vertical_speed_fpm` — the fields a candidate clearance
  actually changes.

No new upstream data is required. Everything above is either read
directly or re-run on a modified copy of data the pipeline already
produces every cycle.

## 3. Proposed scope

For every track with `status` in `{CONFIRMED, GROWING, PEAK,
DISSIPATING}` and `forecast_urgency_rank` not `None` (mirrors
Milestone 6's own `_FORECASTABLE_STATUSES` filter, plus requiring an
actual predicted onset to react to — see OQ-5):

1. Generate a bounded set of candidate clearances against the track's
   member aircraft (see OQ-2 for the exact set).
2. For each candidate, build a hypothetical `TrafficSnapshot` with that
   clearance applied to the relevant aircraft's state, and re-run
   `TrajectoryEngine` → `ClusterEngine` → `ComplexityEngine` on it to
   get a hypothetical `ComplexityRegion`.
3. Score each candidate on a multi-objective basis (complexity
   reduction, deviation/fuel cost — see §6) and rank candidates for
   that track.
4. Attach the ranked candidate list to the track for the Dashboard
   (Milestone 8) to display — see OQ-1 for where.

## 4. Open design questions requiring sign-off

### OQ-1: Where do resolution candidates live?

Two options, directly analogous to Milestone 6's OQ-1:

- **(A) Populate in place** — add a `resolution_candidates: List[...]`
  field directly to `FourDArhac`.
- **(B) Composed object** — a new `ResolutionSet` (or similar) that
  *has-a* `FourDArhac` plus its ranked candidates, mirroring
  `ComplexityRegion`'s composition over `Cluster`.

**Recommendation: (B).** Milestone 6's own OQ-1 reasoned that a composed
type is only justified when multiple, alternative results exist per
track — that is exactly the case here: a track can have several ranked
candidate clearances at once, which is a fundamentally different shape
from Milestone 6's single scalar fields (`predicted_onset_s`, etc.).
Bolting a `List[ResolutionCandidate]` directly onto `FourDArhac` would
also make `astra.tracking`/`astra.forecast`'s already-tested mutation
contract ambiguous about who is allowed to write to it. Composition
keeps `FourDArhac` exactly as Milestone 6 shipped it.

### OQ-2: What candidate clearances are generated?

Proposed fixed, discrete set per track (not a continuous optimizer —
consistent with the thesis-scale scope of every prior milestone):

- **Speed** — ± a configurable step (e.g. ±20 kt) on the track's
  highest-`complexity_score`-contributing aircraft.
- **Flight level** — ± a configurable step (e.g. ±1000 ft) on the same
  aircraft.
- **Heading** — ± a configurable offset (e.g. ±15°), only for
  MTCA/LTCA-driven complexity (see `docs/milestone_4_complexity.md`),
  since heading changes are the most direct lever on predicted
  conflicts.
- **Direct-to** — proposed but flagged **not implementable as scoped**:
  `MockConnector` has no `DCT`-equivalent stack command today (only
  `CRE/DEL/OP/HOLD/SPD/ALT/HDG/VS`), so a direct-to candidate could be
  *scored* against a hand-computed great-circle heading but could not
  be *demonstrated* end-to-end offline the way every other Milestone
  1–6 feature has been. Recommend deferring direct-to candidates to a
  follow-up once `MockConnector` gains route-leg support, and scoping
  Milestone 7 to speed/FL/heading only — flagged explicitly since the
  README's Milestone 7 description ("speed / FL / direct-to
  clearances") would need updating if this is approved.

**Recommendation:** speed/FL/heading now; direct-to out of scope pending
the `MockConnector` gap above, unless this review is revised to approve
adding `DCT` support to `MockConnector` first as a prerequisite.

### OQ-3: How is a candidate's effect actually evaluated?

**Recommendation:** apply the clearance to a **copy** of the current
`TrafficSnapshot` (never the live one), re-run
`TrajectoryEngine.predict()` → `ClusterEngine.detect()` (only the
horizons still relevant to the track's `predicted_onset_s`, not all
five, to bound cost — see OQ-5) → `ComplexityEngine.assess()` on the
resulting region matched back to the track via
`astra.tracking.association.jaccard_similarity`. This reuses three
already-verified, pure/stateless engines unchanged — no new complexity
math, no new trajectory math. The only genuinely new logic is
constructing the hypothetical snapshot and re-associating the result
back to the originating track.

### OQ-4: Multi-objective scoring — how are candidates ranked?

`ComplexityRegion.complexity_score` before vs. after gives a
straightforward complexity-delta signal, but a clearance that reduces
complexity by moving an aircraft far off its flight-planned route has an
operational cost that a pure complexity-delta score would ignore.

**Recommendation:** a weighted composite, directly analogous to
`ComplexityEngine`'s existing pattern:

```
resolution_score = w_complexity * complexity_delta_norm
                  - w_deviation * deviation_cost_norm
                  - w_fuel      * fuel_cost_proxy_norm
```

- `complexity_delta_norm` — `(before - after) / before`, clipped to
  `[0, 1]`.
- `deviation_cost_norm` — normalized magnitude of the clearance itself
  (e.g. `|Δheading| / max_heading_offset`, `|Δspeed| / max_speed_step`),
  not a real route-deviation distance (no flight-plan leg data is
  available — same "no ADS-C/EPP data" limitation already documented in
  Developer_Handover.md).
- `fuel_cost_proxy_norm` — a crude proxy only (e.g. altitude change
  magnitude), explicitly **not** a real fuel-burn model — flagged as a
  documented simplification, same spirit as Milestone 4/6's own
  heuristics.
- Weights (`resolution_weight_*`) validated to sum to `1.0`, following
  `ASTRAConfig.complexity_weight_*`'s existing pattern exactly.

This is presented for approval as a **documented heuristic**, not a
claim of operational validity — same caveat Milestone 6 already carries
for its confidence formula.

### OQ-5: How many candidates/horizons, to bound cost?

Each candidate evaluation re-runs three pipeline stages; naively
evaluating 3 candidate types × several step sizes × every predicted
horizon × every open track, every poll cycle, does not scale even at
thesis scope.

**Recommendation:**
- Only generate candidates for tracks with `forecast_urgency_rank` not
  `None` (i.e. Milestone 6 already found a predicted onset worth acting
  on) — bounds the track count directly.
- One step size per candidate type (not a sweep) — 3 candidates per
  track, not dozens.
- Evaluate only the single horizon closest to that track's
  `predicted_onset_s`, not all five — the one point in time the
  resolution is actually meant to affect.
- Cap the number of tracks resolved per cycle
  (`resolution_max_tracks_per_cycle`) as an explicit, configurable
  safety valve, mirroring `history_length`'s role as a bounded-resource
  config value.

## 5. Proposed module layout

```
astra/resolution/
    models.py       ResolutionCandidate (frozen: clearance type/value,
                     complexity_delta, deviation_cost, fuel_cost_proxy,
                     resolution_score) and ResolutionSet (composes
                     FourDArhac + ranked List[ResolutionCandidate], per
                     OQ-1(B)).
    candidates.py    Pure function(s): given a track + snapshot, build
                     the fixed candidate set from §OQ-2 as hypothetical
                     AircraftState copies. Mirrors astra.hotspot.distance
                     / astra.tracking.association / astra.forecast.*'s
                     pattern of small, pure, independently-testable
                     modules feeding a stateful/orchestrating engine.
    engine.py        ResolutionEngine.resolve(track, snapshot,
                     regions_by_horizon) -> ResolutionSet. Stateless,
                     called once per eligible track per poll cycle,
                     after ForecastEngine.forecast_many() has already
                     run in the same cycle.
```

`astra/resolution` depends on `astra/forecast` (reads
`forecast_urgency_rank`/`predicted_onset_s`), `astra/tracking` (reuses
association primitives), `astra/complexity`, and `astra/trajectory` —
one direction only, consistent with the existing layered dependency
graph. No changes to `astra/forecast`, `astra/tracking`, or any earlier
package are required; every existing public API and regression suite
(287/287 through Milestone 6) stays untouched.

## 6. Config additions (`ASTRAConfig`, Phase 7 section, proposed)

| Field | Proposed default | Meaning |
|---|---|---|
| `resolution_speed_step_kt` | `20.0` | Magnitude of the speed candidate's ± adjustment. |
| `resolution_altitude_step_ft` | `1000.0` | Magnitude of the flight-level candidate's ± adjustment. |
| `resolution_heading_step_deg` | `15.0` | Magnitude of the heading candidate's ± adjustment. |
| `resolution_weight_complexity` | `0.6` | Weight on complexity-delta in `resolution_score`. |
| `resolution_weight_deviation` | `0.25` | Weight on deviation cost (penalty). |
| `resolution_weight_fuel` | `0.15` | Weight on the fuel-cost proxy (penalty). |
| `resolution_max_tracks_per_cycle` | `5` | Safety cap on how many tracks are resolved per poll cycle (OQ-5). |

`resolution_weight_*` validated to sum to `1.0` in
`ASTRAConfig.__post_init__`, following the same fail-fast pattern as
`complexity_weight_*`. All step sizes validated `> 0`.

## 7. Testing plan (for when implementation is approved)

Following the Milestone 3–6 pattern exactly:

- `tests/test_resolution.py` — hand-built tracks/snapshots exercising:
  each candidate type's hypothetical-state construction in isolation;
  complexity-delta computed correctly against a known before/after pair;
  `resolution_score` combination and its weight-sum config validation;
  ranking order across a multi-candidate set; tracks with
  `forecast_urgency_rank is None` skipped entirely; the
  `resolution_max_tracks_per_cycle` cap enforced.
- `demo_resolution.py` — extends `demo_forecast.py`'s scripted scenario:
  once a track is `GROWING`/`PEAK` with a predicted onset, run
  `ResolutionEngine` alongside `ForecastEngine` and print each
  candidate's clearance, complexity delta, and score, ranked.
- Regression: Milestones 3–6 (157/157; 287/287 including Milestones
  1–2) must remain green and untouched — no existing public API changes.

## 8. Explicit non-goals for Milestone 7

- No dashboard/HMI changes (Milestone 8) — candidates are computed and
  ranked, not displayed or issued as live clearances.
- No automatic clearance issuance to BlueSky/`MockConnector` — every
  candidate is advisory only; an FMP/human remains the actor who issues
  a real clearance. Automating that is explicitly out of scope for a
  thesis-scale prototype and raises safety-approval questions well
  beyond this project.
- No real fuel-burn or route-deviation-distance model — proxies only
  (§6, OQ-4), documented as such.
- Direct-to candidates deferred pending a `MockConnector` capability gap
  (OQ-2), unless this review is revised.
- No change to `ForecastEngine`, `TrackerEngine`, or any earlier
  package's public API.
- No `main.py` integration — same reasoning as Milestones 5/6's
  "deliberately not integrated" decision.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Re-running the full trajectory/cluster/complexity pipeline per candidate per track is expensive | Bounded by OQ-5: urgency-ranked tracks only, one step size per candidate type, single closest horizon, explicit per-cycle cap |
| Deviation/fuel costs are proxies, not real operational costs, so `resolution_score` could look more authoritative than it is | Documented explicitly as a simplification in the as-built doc and Known Limitations, matching precedent from Milestones 4/6 |
| Direct-to candidates were an explicit part of the original Milestone 7 description in `README.md`/`PROJECT_STATUS.md` | Flagged in OQ-2 as a scope reduction requiring explicit sign-off; README/status docs would be updated to match once this review is approved |
| Hypothetical-snapshot construction could accidentally mutate the live `TrafficSnapshot` (frozen dataclasses should prevent this, but the interaction is new) | `AircraftState`/`TrafficSnapshot` are already frozen (Milestone 1 convention); `candidates.py` must construct new instances via `dataclasses.replace`, never mutate — to be enforced by `tests/test_resolution.py` |
| Composed `ResolutionSet` (OQ-1) is a new public shape consumers must learn, unlike Milestone 6's in-place mutation | One new, narrowly-scoped type; Dashboard (Milestone 8) is the only planned consumer and does not exist yet, so there is no existing call site to break |

