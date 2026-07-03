# Milestone 6 — 4DARHAC Forecast: Engineering Design Review

**Status: DRAFT — pending approval. No Milestone 6 code has been written.**
This document is a design review only, prepared per the standing project
convention (see Milestone 5's build plan in `docs/architecture.md` §6.5,
which was written and approved before Milestone 5 was implemented).
Implementation must not begin until this review is explicitly approved
and this status line is updated.

---

## 1. Purpose and recap

Milestone 5 (`astra.tracking`) gives every persistent 4D Area of
Relatively High ATC Complexity a stable identity (`FourDArhac`) and a
mechanically-derived *trend* status (`CANDIDATE → CONFIRMED → GROWING →
PEAK → DISSIPATING → CLOSED`) based on the observed `complexity_score`
history. It explicitly does **not** predict *when* a track will reach a
significant complexity level, when it will peak in the future, when it
will dissipate, or how much to trust that prediction — see the
"Explicit non-goals" note in `docs/architecture.md` §6.5 and
`docs/milestone_5_tracking.md`.

Milestone 6 closes that gap: for each open, confirmed `FourDArhac`,
estimate `predicted_onset_s`, refine `predicted_dissipation_s`, and
produce a calibrated-as-possible `confidence`, using the current poll
cycle's predicted horizons (already computed every cycle by
`TrajectoryEngine` → `ClusterEngine` → `ComplexityEngine`, but not yet
consumed by anything beyond `demo_hotspot.py`/`demo_complexity.py`).

## 2. What Milestone 6 has to work with (already built, unchanged)

- `FourDArhac.track: List[ComplexityRegion]` — the track's **observed**
  history (horizon 0 only, per Milestone 5's design decision).
- `FourDArhac.status`, `.peak_complexity`, `.peak_time_s` — trend state
  and the highest score **observed so far**.
- Every poll cycle, `regions_by_horizon: Dict[int, List[ComplexityRegion]]`
  already contains fresh `ComplexityRegion`s at horizons 5, 10, 15, 30,
  60 minutes (from `ClusterEngine.detect_all()` /
  `ComplexityEngine.assess_many()`) — computed today, discarded today.
  Milestone 6 is the first consumer of the non-zero-horizon entries.
- `astra.tracking.association.jaccard_similarity` /
  `centroid_extent_overlap` / `best_track_match` — the exact matching
  primitives needed to answer "which predicted-horizon region, if any,
  is *this* track's likely future state this cycle?"

No new upstream data is required. This is entirely a matter of
consuming data the pipeline already produces every cycle.

## 3. Proposed scope

For every track with `status` in `{CONFIRMED, GROWING, PEAK,
DISSIPATING}` (**not** `CANDIDATE` — forecasting an unconfirmed,
possibly-noise track is not useful and risks amplifying single-cycle
DBSCAN artifacts, the same reasoning `tracking_confirm_cycles` already
applies to promotion):

1. Associate the track's most recent observed entry against each
   horizon's regions this cycle, using
   `astra.tracking.association.best_track_match` (reused, not
   reimplemented — see §5).
2. From the resulting discrete series of `(time_s, complexity_score)`
   points — the observed history plus whichever predicted horizons
   matched — estimate:
   - `predicted_onset_s`: the first future time the score is
     interpolated to cross a configurable "significant" threshold, if
     it is not already above it.
   - `predicted_dissipation_s`: the first future time the score is
     interpolated to fall back below a configurable threshold, if the
     series is trending down or expected to.
3. Produce a `confidence` in `[0, 1]` reflecting how much to trust that
   estimate (see §6).
4. Leave `priority` as Milestone 5 left it, **pending decision** — see
   open question OQ-4.

## 4. Open design questions requiring sign-off

These are the decisions this review exists to get approved before any
code is written. Each has a recommendation, but none are final.

### OQ-1: Where do the forecast fields live?

The `FourDArhac` dataclass (Milestone 5, `astra/tracking/models.py`)
already has `predicted_onset_s`, `predicted_dissipation_s`, and
`confidence` fields, per the original `§6.2` domain-model sketch —
currently always `None` / placeholder, with docstrings already saying
"Reserved for Milestone 6". Two options:

- **(A) Populate in place** — a new `ForecastEngine` mutates the same
  `FourDArhac` objects `TrackerEngine` owns, filling in the reserved
  fields. Matches the schema as originally designed; no new dataclass.
- **(B) Composed forecast object** — a new `FourDArhacForecast` dataclass
  that *has-a* `FourDArhac` (mirroring `ComplexityRegion`'s composition
  over `Cluster`), leaving `FourDArhac` itself untouched by anything
  outside `astra.tracking`.

**Recommendation: (A).** The fields already exist in the approved
schema specifically for this purpose, and mutating them is consistent
with `FourDArhac` already being the one *mutable* domain object in the
system (unlike `Cluster`/`ComplexityRegion`, which are frozen precisely
*because* nothing should mutate them — see `docs/architecture.md` §6.2).
Introducing a second, composed forecast type would fragment "the current
state of one ARHAC" across two objects for no clear benefit. (B) would
only be preferable if we expected multiple, alternative forecasts per
track (e.g. comparing models) — out of scope here.

### OQ-2: Does "peak" need a new, distinct forecast field?

`peak_complexity` / `peak_time_s` currently mean "the highest score
**observed** so far" (Milestone 5, unchanged fact, computed from
`track`). A forecast asks a different question: "is a *higher* score
expected at some *future* predicted horizon than anything observed so
far, and if so, roughly when?" These are not the same thing, and the
current schema has no field for the second question.

**Recommendation:** add `predicted_peak_time_s: Optional[float]` (and
reuse `peak_complexity` as "observed-or-predicted, whichever is
higher" — i.e. `ForecastEngine` may *raise* `peak_complexity` if a
predicted horizon exceeds it, but `peak_time_s` only changes when
that happens, keeping "when was the highest score seen or expected"
coherent as one pair of fields). This is a schema addition beyond the
original §6.2 sketch — flagged explicitly because it is new, not because
it is risky; it needs sign-off like any schema change would.

### OQ-3: Onset/dissipation estimation method

Two candidate approaches:

- **(A) Trend extrapolation** — linear regression over the track's
  recent *observed* `complexity_score` points only, projected forward
  to a threshold crossing. Cheap, no dependency on predicted horizons,
  but ignores the kinematic model entirely and just extrapolates recent
  history in a straight line.
- **(B) Predicted-horizon interpolation** — use the discrete
  `(time_s, score)` series described in §3 (observed history plus
  matched predicted horizons at 5/10/15/30/60 min), linearly
  interpolating between the two points that bracket the configured
  threshold. Uses the actual `TrajectoryEngine` constant-velocity
  projection already computed for those aircraft, rather than a fresh
  statistical fit.

**Recommendation: (B).** It reuses the kinematic model the rest of the
pipeline already trusts (Milestones 2–4), rather than introducing a
second, statistical forecasting method with its own assumptions. Its
main weakness — constant-velocity prediction accuracy degrades with
horizon length — is already a documented, accepted limitation (see
Developer_Handover.md "Known limitations"), not a new one. (A) remains
worth prototyping later as a comparison/fallback for tracks where too
few predicted horizons matched (see OQ-5), but should not be the
primary method.

### OQ-4: Does `priority` change meaning?

Milestone 5's `priority` is a simple FMP triage rank by
`peak_complexity` (1 = highest), recomputed every `TrackerEngine.update()`
call. Milestone 6 could instead (or additionally) rank by predicted
*urgency* — e.g. soonest `predicted_onset_s` — which may reorder tracks
relative to pure severity.

**Recommendation:** leave `priority`'s definition and ownership exactly
as Milestone 5 built it (severity-only, owned by `TrackerEngine`) for
this milestone, and add a **separate**, explicitly-named
`forecast_urgency_rank` (or similar) computed by `ForecastEngine`,
rather than silently redefining an existing, tested, already-shipped
field. Resolution (Milestone 7) and Dashboard (Milestone 8) can then
choose which ranking — or a combination — to surface; that choice is
better made when there is an actual consumer, not preemptively here.
This avoids a breaking change to Milestone 5's contract and its existing
44 regression checks.

### OQ-5: What happens when too few predicted horizons match?

A track's most recent observed entry might not match *any* predicted
horizon this cycle (e.g. genuinely erratic traffic, or the association
threshold not clearing at longer horizons where drift is largest — the
exact scenario `centroid_extent_overlap`'s fallback exists for, but the
fallback can still fail). With zero or one matched points beyond the
observed history, threshold-crossing interpolation (OQ-3 option B) is
undefined or unreliable.

**Recommendation:** if fewer than a configurable minimum number of
horizons matched (proposed default: 2), leave `predicted_onset_s` /
`predicted_dissipation_s` / `predicted_peak_time_s` as `None` rather
than guessing, and cap `confidence` accordingly (see §6) — an explicit
"insufficient data" state is more honest than a low-quality forecast
presented with false precision. This mirrors Milestone 4's philosophy
of documented, principled simplifications over confident-looking
guesses.

## 5. Proposed module layout

```
astra/forecast/
    models.py       No new dataclass if OQ-1(A) is approved; otherwise
                     FourDArhacForecast per OQ-1(B).
    horizon_series.py   Pure function(s): build the (time_s, score)
                     series for one track this cycle, by calling
                     astra.tracking.association.best_track_match
                     against each horizon's regions. Mirrors
                     astra.hotspot.distance / astra.tracking.association's
                     pattern of small, pure, independently-testable
                     modules feeding a stateful/orchestrating engine.
    engine.py        ForecastEngine.forecast(track, regions_by_horizon)
                     -> FourDArhac (or FourDArhacForecast per OQ-1).
                     Stateless itself — it does not own tracks the way
                     TrackerEngine does; it is called once per open,
                     confirmed-or-later track, per poll cycle, after
                     TrackerEngine.update() has already run.
```

`astra/forecast` depends on `astra/tracking` (reuses its association
primitives) and `astra/complexity` (consumes `ComplexityRegion`), one
direction only — consistent with the existing layered dependency graph
in `docs/architecture.md`. No changes to `astra/tracking` are required;
`TrackerEngine`'s public API is untouched, so Milestone 5's tests and
demo keep working unmodified, satisfying the same backward-compatibility
bar Milestone 5 was held to.

## 6. Confidence — proposed heuristic (needs sign-off)

The project has no historical reference dataset to calibrate a genuine
statistical confidence model — the same constraint already documented
for complexity scoring (`docs/milestone_4_complexity.md` "Score
combination", carried into Developer_Handover.md's "Known limitations").
Milestone 6 should not pretend otherwise. Proposed composite, entirely
analogous in spirit to `ComplexityEngine`'s weighted-normalisation
approach:

```
confidence = detection_ramp * horizon_coverage * (1 - decay)
```

- `detection_ramp` — Milestone 5's existing placeholder
  (`min(1.0, len(track) / tracking_confirm_cycles)`), reused as-is: a
  track just barely confirmed should not receive a confident forecast.
- `horizon_coverage` — fraction of configured predicted horizons that
  successfully matched this cycle (see OQ-5); `0` if below the minimum.
- `decay` — a simple exponential penalty for how far out the estimated
  onset/dissipation time is (`1 - exp(-Δt / forecast_confidence_decay_s)`),
  reflecting the constant-velocity model's known accuracy degradation
  over longer horizons.

This is a **documented simplification**, presented for approval as such
— not a claim of statistical calibration. `docs/milestone_6_forecast_...`
(the as-built doc, once approved and implemented) would carry the same
kind of "Known limitations" entry Milestone 4/5 already do.

## 7. Proposed config additions (`ASTRAConfig`, Phase 6 section)

| Field | Proposed default | Meaning |
|---|---|---|
| `forecast_onset_threshold` | `50.0` | `complexity_score` above which an ARHAC counts as "active" for onset purposes. |
| `forecast_dissipation_threshold` | `30.0` | `complexity_score` below which an ARHAC counts as dissipated. (Deliberately lower than the onset threshold — hysteresis, avoiding onset/dissipation flapping right at one boundary value.) |
| `forecast_min_matched_horizons` | `2` | Minimum matched predicted horizons before attempting interpolation (OQ-5). |
| `forecast_confidence_decay_s` | `1800.0` (30 min) | Time-constant for the confidence decay term in §6. |

All four would be validated in `ASTRAConfig.__post_init__` following the
existing fail-fast pattern (e.g. `forecast_dissipation_threshold <
forecast_onset_threshold`, all thresholds in `[0, 100]`, decay constant
`> 0`).

## 8. Testing plan (for when implementation is approved)

Following the Milestone 3/4/5 pattern exactly:

- `tests/test_forecast.py` — hand-built `(time_s, score)` series
  exercising: onset crossing detected correctly; dissipation crossing
  detected correctly; already-above-threshold track (no onset to
  predict, it already happened); insufficient-matched-horizons →
  `None`s and capped confidence (OQ-5); confidence formula edge cases
  (zero coverage, maximum decay); config validation for all four new
  fields.
- `demo_forecast.py` — extends `demo_tracking.py`'s scripted scenario:
  after the track reaches `CONFIRMED`, run `ForecastEngine` alongside
  `TrackerEngine` each cycle and print the predicted onset/peak/
  dissipation times and confidence alongside the existing trend output.
- Regression: Milestones 3–5 (110/110) must remain green and untouched,
  per the same backward-compatibility bar this milestone (5) was held
  to. `TrackerEngine`'s public API does not change, so this should
  require no edits to `tests/test_tracking.py` or `demo_tracking.py`.

## 9. Explicit non-goals for Milestone 6

- No resolution suggestions (Milestone 7).
- No dashboard/HMI changes (Milestone 8).
- No genuine statistical/ML calibration of confidence — heuristic only,
  documented as such (§6).
- No change to `TrackerEngine`'s public API or to `priority`'s existing
  meaning (OQ-4), unless this review is revised to approve otherwise.
- No `main.py` integration — same reasoning as Milestone 5's "deliberately
  not integrated" decision (`docs/milestone_5_tracking.md`); revisit at
  the Dashboard milestone.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Constant-velocity predicted horizons are inaccurate for maneuvering aircraft, so onset/dissipation estimates inherit that error | Already a documented, accepted limitation (Milestones 2–4); §6's `decay` term explicitly reduces confidence for longer-horizon estimates rather than hiding the uncertainty |
| Reusing `astra.tracking.association` from `astra.forecast` creates a new inter-package dependency | One-directional (`forecast → tracking`), consistent with the existing layered graph; no cycle risk |
| Schema addition (`predicted_peak_time_s`, OQ-2) changes the `FourDArhac` dataclass after Milestone 5 shipped it | Purely additive (new optional field, defaults `None`); does not change any existing field's type or meaning; `tests/test_tracking.py` unaffected |
| Confidence heuristic looks more authoritative than it is | Documented explicitly as a simplification in the as-built doc and Known Limitations, matching precedent from Milestones 4 and 5 |

## 11. Approval

This review requires explicit approval before `astra/forecast/` is
created or `ASTRAConfig` gains any `forecast_*` fields. Approving this
review means signing off on OQ-1 through OQ-5 specifically (or
requesting changes to any of them) — everything else in this document
follows directly from those five decisions.
