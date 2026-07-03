# Milestone 6 — 4DARHAC Forecast (`astra.forecast`)

## Scope

Closes the gap Milestone 5 deliberately left open: for each open,
confirmed `FourDArhac`, estimate `predicted_onset_s`, refine
`predicted_dissipation_s`, and produce a `confidence` reflecting how much
to trust that estimate — using the current poll cycle's predicted-horizon
`ComplexityRegion`s that `TrajectoryEngine` → `ClusterEngine` →
`ComplexityEngine` already compute every cycle, but that nothing beyond
`demo_hotspot.py`/`demo_complexity.py` had consumed before this milestone.
`ForecastEngine` is stateless — unlike `TrackerEngine`, it does not own
tracks; it is called once per track, per poll cycle, after
`TrackerEngine.update()` has already run in the same cycle.

This milestone implements `docs/milestone_6_forecast_design_review.md`
essentially as approved. The five open design questions (OQ-1 through
OQ-5) in that review were all resolved as recommended; this document
records the as-built result and the one real defect found while wiring
the demo together.

## Design decision — forecast fields populated in place (OQ-1)

`ForecastEngine` mutates the same `FourDArhac` objects `TrackerEngine`
owns, filling in the fields the Milestone 5 schema had already reserved
(`predicted_onset_s`, `predicted_dissipation_s`, `confidence`) plus two
new ones added by this milestone (see OQ-2 and OQ-4 below). No separate
`FourDArhacForecast` composition type was introduced — `FourDArhac`
remains the single mutable domain object for "the current state of one
ARHAC", consistent with why `Cluster`/`ComplexityRegion` stay frozen and
`FourDArhac` does not (`docs/architecture.md` §6.2).

## Design decision — `predicted_peak_time_s` added to the schema (OQ-2)

`peak_complexity`/`peak_time_s` (Milestone 5) mean "the highest score
**observed** so far". A forecast asks a different question — whether a
*higher* score is expected at some future predicted horizon — so a new
field, `predicted_peak_time_s: Optional[float]`, was added to
`FourDArhac` (`astra/tracking/models.py`) as recommended. `ForecastEngine`
may *raise* `peak_complexity` (and move `peak_time_s` alongside it) when
a matched predicted horizon exceeds the previously-known peak, keeping
"when was the highest score seen or expected" coherent as one pair of
fields, with `predicted_peak_time_s` recording that the peak is a
forecast rather than an observation. Purely additive: existing field
types/meanings are unchanged, and `tests/test_tracking.py` (44/44) is
unaffected.

## Design decision — predicted-horizon interpolation, not trend extrapolation (OQ-3)

`astra/forecast/horizon_series.py` builds a discrete `(time_s,
complexity_score)` series per track per cycle: the track's most recent
observed entry, plus every predicted horizon (5/10/15/30/60 min) whose
cluster matches that track this cycle, via
`astra.tracking.association.best_cluster_match` — reused, not
reimplemented, mirroring how Milestone 5 reused
`astra.hotspot.distance`'s small-pure-module pattern. `astra/forecast/
projection.py` then does the actual math over that series:
`linear_crossing_time()` linearly interpolates the first time the series
crosses a threshold (rising for onset, falling for dissipation), and
`predicted_peak()` finds the highest-scoring future point if it exceeds
the current peak. This reuses the kinematic model the rest of the
pipeline (Milestones 2–4) already trusts, rather than fitting a second,
statistical trend model over observed history alone — the same
constant-velocity-accuracy caveat already documented for Milestones 2–4
applies unchanged here (see Known Limitations, Developer_Handover.md).

## Design decision — `forecast_urgency_rank` kept separate from `priority` (OQ-4)

`priority` (Milestone 5's severity-only FMP triage rank by
`peak_complexity`) is left exactly as `TrackerEngine` built it —
`ForecastEngine` does not touch it. A new, separately-named
`forecast_urgency_rank: Optional[int]` field was added instead,
computed by `ForecastEngine._assign_urgency_rank()` in
`forecast_many()`: tracks are ranked by soonest `predicted_onset_s` (1 =
soonest), and any track with no predicted onset this cycle (already
active, no crossing found, insufficient matched horizons, or not
forecastable at all) keeps `forecast_urgency_rank = None`. Resolution
(Milestone 7) and Dashboard (Milestone 8) can choose which ranking — or
a combination — to surface later; this avoids redefining an
already-shipped, tested field's meaning out from under Milestone 5's 44
regression checks.

## Design decision — insufficient matched horizons leave fields `None` (OQ-5)

If fewer than `forecast_min_matched_horizons` (default `2`) predicted
horizons matched a track this cycle, `predicted_onset_s`,
`predicted_dissipation_s`, and `predicted_peak_time_s` are all left
`None` rather than interpolating over a single, unreliable point, and
`confidence` is capped to `detection_ramp * horizon_coverage` (with
`horizon_coverage` at or near zero). An explicit "insufficient data"
state was preferred over a low-quality forecast presented with false
precision — the same philosophy Milestone 4 already applied to its own
documented simplifications.

## `_FORECASTABLE_STATUSES`

Only tracks with `status in {CONFIRMED, GROWING, PEAK, DISSIPATING}` are
forecast at all; `CANDIDATE` tracks are skipped (forecasting a
possibly-noise, unconfirmed track risks amplifying single-cycle DBSCAN
artifacts — the same reasoning `tracking_confirm_cycles` already applies
to promotion) and `CLOSED` tracks are skipped (nothing left to associate
predicted horizons against). Skipped tracks pass through
`ForecastEngine.forecast()` unchanged.

## Confidence formula (documented heuristic, not calibrated)

```
confidence = detection_ramp * horizon_coverage * (1 - decay)
```

- `detection_ramp` — Milestone 5's existing placeholder
  (`min(1.0, detections / tracking_confirm_cycles)`), reused as-is.
- `horizon_coverage` — fraction of this cycle's non-zero predicted
  horizons that matched the track (`matched_count / total_horizons`).
- `decay` — `1 - exp(-Δt / forecast_confidence_decay_s)`, an exponential
  penalty for how far out the estimated time is, reflecting the
  constant-velocity model's known accuracy degradation over longer
  horizons.

As with `ComplexityEngine`'s weighted combination
(`docs/milestone_4_complexity.md` "Score combination"), the project has
no historical reference dataset to calibrate a genuine statistical
confidence model — this is a documented simplification, not a claim of
calibration, carried into Developer_Handover.md's "Known limitations".

## Config additions (`ASTRAConfig`, Phase 6 section)

| Field | Default | Meaning |
|---|---|---|
| `forecast_onset_threshold` | `50.0` | `complexity_score` above which an ARHAC counts as "active" for onset purposes. Validated to `[0, 100]`. |
| `forecast_dissipation_threshold` | `30.0` | `complexity_score` below which an ARHAC counts as dissipated. Deliberately lower than the onset threshold (hysteresis, avoiding flapping right at one boundary value). Validated to `[0, 100]` and strictly `< forecast_onset_threshold`. |
| `forecast_min_matched_horizons` | `2` | Minimum matched predicted horizons before attempting interpolation (OQ-5). Validated `>= 1`. |
| `forecast_confidence_decay_s` | `1800.0` (30 min) | Time-constant for the confidence decay term above. Validated `> 0`. |

All four are validated in `ASTRAConfig.__post_init__`, following the
same fail-fast pattern as the Phase 4/5 checks.

## Real bug found while finishing `demo_forecast.py`

While wiring `tests/demo_forecast.py` end to end, both the demo and
`tests/test_forecast.py` raised `AttributeError: 'FourDArhac' object has
no attribute 'forecast_urgency_rank'`. Root cause: an earlier pass at
implementing OQ-1/OQ-2/OQ-4 had added the two new fields
(`predicted_peak_time_s`, `forecast_urgency_rank`) to a second,
orphaned copy of the `FourDArhac` dataclass in `astra/forecast/models.py`
that nothing ever imported — `astra/forecast/engine.py`,
`astra/forecast/horizon_series.py`, `astra/tracking/engine.py`, and both
test files all import `FourDArhac` from the canonical
`astra/tracking/models.py`, which still had only the Milestone 5 field
set. Fix: added the two missing fields (with matching docstrings) to the
canonical `astra/tracking/models.py`, and deleted the unused duplicate
`astra/forecast/models.py` — nothing referenced it, so removing it is
behaviour-neutral and prevents the two copies from silently diverging
again. No other code changed; `ForecastEngine`'s logic was already
correct once it had a schema to write to.

## Verification

`tests/test_forecast.py` (47 checks): `linear_crossing_time` rising/
falling/no-crossing cases; `predicted_peak` exceeds/does-not-exceed/
empty-series cases; `build_series` matched-count and total-horizons
bookkeeping, including horizon-0 exclusion; `CANDIDATE`/`CLOSED` tracks
skipped untouched; insufficient-matched-horizons → all three
`predicted_*` fields `None` and confidence capped; onset crossing
detected and already-active (no onset to predict) cases; dissipation
crossing detected and already-dissipated cases; peak raised by a higher
matched future horizon vs. not raised; `forecast_many()` urgency-rank
assignment (soonest onset = 1, no-onset tracks = `None`) end to end
across a small multi-track scenario; and `ASTRAConfig` validation for
all four new fields. Combined with Milestones 3–5 (24/24, 42/42, 44/44),
the full suite is 157/157.

`demo_forecast.py` extends `demo_tracking.py`'s scripted scenario: three
near-stationary aircraft (`GS=2kt`) form a stable track through
`CANDIDATE → CONFIRMED`, then diverge headings/altitude and pick up
speed (`GROWING → PEAK`) while the higher scripted speed makes the
*predicted* horizons diverge further from the observed cluster than in
the Milestone 5 demo, giving `ForecastEngine` something to interpolate
over; headings/speed are then re-aligned (`DISSIPATING`). Each cycle
prints `ForecastEngine`'s onset/dissipation/peak-time estimates,
confidence, and urgency rank alongside the observed cluster and trend
status already shown by `demo_tracking.py`.

## Explicit non-goals (carried over from the design review)

No resolution suggestions (Milestone 7), no dashboard/HMI changes
(Milestone 8), no genuine statistical/ML calibration of confidence
(heuristic only, documented above), no change to `TrackerEngine`'s
public API or to `priority`'s existing meaning (OQ-4).

## `main.py` — deliberately not integrated

Same reasoning as Milestone 5's "`main.py` — deliberately not
integrated" (`docs/milestone_5_tracking.md`): each milestone's
functionality is demonstrated through its own `demo_*.py` script, and
`main.py` stays a stable Phase 1 reference point until the Dashboard
milestone (8) gives the full pipeline an actual live-loop consumer.
