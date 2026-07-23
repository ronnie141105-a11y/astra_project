# ASTRA Backend Improvements Backlog

**Read this file first when picking up backend work in a new
conversation.** It exists so this line of work can continue one item at
a time without re-deriving context or re-reading the whole repo. Do not
fold this into `docs/PROJECT_STATUS.md`, `docs/architecture.md`, or
`docs/Developer_Handover.md` -- keep it separate, the same way
`docs/dashboard_redesign_progress.md` is kept separate for the parallel
UI work (this file is that log's backend counterpart).

This backlog was written after a full read-through of every pipeline
stage (trajectory, hotspot, tracking, forecast, complexity, resolution)
following the Milestone 7 resolution-engine improvements (route-aware
evaluation, vector-and-rejoin heading candidates, joint multi-aircraft
candidates -- already shipped, see git history "improve resolution
engine..."). Items are numbered in the order they were originally raised,
not necessarily priority -- each item records its own priority
reasoning.

---

## Status at a glance

**Note (this session):** while picking up items 6/7, found that
`astra/forecast/engine.py`'s item-1 change (PROVISIONAL tracks being
forecastable) had been silently reverted somewhere in an intermediate
merge -- confirmed by diffing against the original item-1 commit, every
other backend file matched exactly, only this one file had reverted to
its pre-item-1 state. Restored to match the original fix exactly (byte-
for-byte, re-diffed to confirm); this is why `test_forecast.py` briefly
showed 2 failures when this session started before the restore. Not a
new design change, just recovering something that existed before and
was lost in an unrelated merge -- worth knowing if other reverted-file
surprises like this turn up later.


| # | Item | Status |
|---|------|--------|
| 1 | Provisional tracks from future-only clusters | **DONE** (this session) |
| 2 | Complexity scoring can't flag certain same-heading/same-altitude conflicts | **DONE** (this session) |
| 3 | Resolution engine follow-ups: domino cost (**done**), fuel-cost proxy (**done**), target selection fallback (**done**), RVSM/level-parity (**done**, this session), feedback loop (not started) | **Partially done** |
| 4 | Performance: per-candidate full-snapshot replay cost | Not started |
| 5 | Test suite / pytest collision with the custom `Runner` convention | Not started |
| 6 | TrackerEngine could report DISSIPATING/GROWING indefinitely during a genuine but slow trend reversal | **DONE** (this session, found via user report) |
| 7 | Dashboard resolution-candidate cap hardcoded to a fixed 3, silently dropping real candidates | **DONE** (this session, found via user report) |
| 8 | Resolution engine: proactive whole-lookahead horizon evaluation + diverse multi-aircraft (joint) candidates | **DONE** (this session, found via user report) |

---

## Item 1 — Provisional tracks from future-only clusters — **DONE**

### The problem

`TrackerEngine.update()` originally only ever opened or extended a track
from an *observed* (horizon 0) cluster. A hotspot with zero current
proximity -- no aircraft anywhere near each other yet, but clearly
converging in a longer-horizon prediction -- was structurally invisible
to the tracker, no matter how obvious the prediction. This was the
single biggest gap found while building this project's own
`arrival_sequencing`/`sector_overload`/`crossing_airways` scenario
presets (see `astra/dashboard/scenario_presets_operational.py`): every
"genuinely 30-60 min out" scenario had to compromise by keeping some
aircraft artificially close together *right now*, specifically to give
the tracker something to seed a track from at all. Given the thesis's
central claim is medium-term prediction, this was the gap most worth
closing.

### What shipped

A new lifecycle stage, `"PROVISIONAL"` (`astra.tracking.models.ArhacStatus`):
a track opened from a *predicted* (non-zero horizon) cluster with no
real-world counterpart yet, carried forward via a new
`FourDArhac.provisional_track` field kept entirely separate from
`FourDArhac.track`'s existing "observed only" invariant.

Each poll cycle, `TrackerEngine.update()` now does three things instead
of two:

1. The original horizon-0 loop, unchanged in spirit -- except a
   provisional track's first real match is now recognised as a
   **promotion** (`_promote_provisional_track`), not a normal extension:
   it gets exactly the same "starts fresh" status logic as a brand-new
   track (`_initial_status`), so provisional (predicted-only) history
   never counts toward `tracking_confirm_cycles`. `arhac_id` and
   `first_detected_cycle_s` (the original provisional detection time)
   are preserved across the promotion, so "flagged N minutes before it
   became real" (`provisional_lead_time_s` in the serializer) is always
   recoverable from the track alone.
2. `_detect_and_extend_provisional`: scans every *other* (non-zero)
   horizon this cycle, ascending, for a predicted cluster that doesn't
   already correspond to some open track (real or provisional) and
   clears a new complexity floor (`tracking_provisional_min_complexity`,
   default 25.0) -- opening a new PROVISIONAL track for it. An
   already-provisional track matched again this cycle gets one new
   `provisional_track` entry (at most once per cycle, from whichever
   horizon matched first, for maximum lead time); a match against a
   *non*-provisional track is left alone (just enough to suppress
   opening a duplicate for that track's own already-real future).
3. Aging/closing, unchanged in mechanism -- a provisional track that
   matches nothing at all this cycle (neither observed nor any
   predicted horizon) accumulates `_missed_cycles` exactly like a real
   one, and closes the same way if the phenomenon it predicted stops
   reappearing (e.g. one aircraft changed heading and the predicted
   convergence no longer happens).

Provisional tracks are **forecastable**
(`ForecastEngine._FORECASTABLE_STATUSES` now includes `"PROVISIONAL"` --
an onset time estimate for something not yet observed at all is the
actual point of this feature) but **never resolvable**
(`ResolutionEngine._RESOLVABLE_STATUSES` deliberately still excludes it
-- there is nothing concrete yet to issue a clearance against; no code
change was needed there, it works by simply not being in that set).

New config knobs (`astra/utils/config.py`):
- `tracking_provisional_min_complexity` (default 25.0) -- floor a
  predicted cluster's score must clear before a provisional track opens
  for it, keeping this from firing on every faint, sub-noise density
  blip in a far horizon. Independent of `forecast_onset_threshold`
  (that governs onset *time* prediction on an already-open track; this
  gates whether a not-yet-observed prediction is worth tracking at all).
- `tracking_provisional_confidence_multiplier` (default 0.5) -- applied
  on top of the normal detection-count confidence ramp, so a
  provisional track's confidence is visibly lower than a real track
  with the same number of detections.

Small supporting fixes, both backward compatible:
- `astra.tracking.association.best_track_match` now falls back to a
  track's `provisional_track[-1]` when `track.track` is empty (instead
  of only ever reading `track.track[-1]`).
- `astra.forecast.horizon_series.build_series` does the same for its
  series anchor.

Dashboard serializer (`astra/dashboard/serializers.py`,
`serialize_track`): every existing field is untouched (a provisional
track just keeps showing `history: []`, `centroid: null`,
`current_complexity_score: null`, exactly as an old empty-`track.track`
record already did) -- four new fields are purely additive:
`provisional_history`, `provisional_current_complexity_score`,
`provisional_centroid`, `provisional_lead_time_s`. **Not yet wired into
`dashboard.js`/`.css`** -- that's UI work, deliberately left for the
parallel UI redesign track rather than touched here. A `.status-
PROVISIONAL` CSS rule (mirroring the existing `.status-CANDIDATE` etc.
in `dashboard.css`) is the only thing needed for it not to render
unstyled; the JS doesn't special-case status strings anywhere that would
otherwise break.

### Verification

`tests/test_tracking.py`: 7 new tests (opening from a future-only
horizon, ignoring below-threshold predictions, extending across cycles,
not duplicating within one cycle when visible at multiple horizons,
promotion preserving `arhac_id`/`first_detected_cycle_s` and correctly
requiring its own `tracking_confirm_cycles`, staleness/closing, and
confirming `ResolutionEngine` never resolves a provisional track).
`tests/test_forecast.py`: 2 new tests (a provisional track gets a real
`predicted_onset_s`; `build_series` anchors correctly on
`provisional_track` when `track.track` is empty).
`tests/test_dashboard.py`: 2 new tests (existing fields stay at their
old defaults; new fields carry the predicted data; lead time computes
correctly after promotion). All 462 checks across the full suite pass
(zero regressions).

Live end-to-end smoke test (real `MockConnector` + `Pipeline`, two
aircraft on real airway W1, ~35 NM apart at spawn -- no cluster at
horizon 0 at all): a PROVISIONAL track opened at t=60s from a horizon-30
prediction, persisted for 19 real minutes (confidence ramping 0.05 ->
0.14 throughout, correctly capped low), then promoted to CANDIDATE at
t=1200s with `provisional_lead_time_s = 1140.0` -- i.e. this specific
run demonstrably flagged the hotspot **19 minutes before it became
real**, under the same `arhac_id` throughout. This is the concrete,
reproducible proof this feature was built to deliver.

### Deliberately out of scope for this pass (possible follow-ups)

- No GROWING/PEAK/DISSIPATING trend classification *during* the
  provisional phase -- status stays flatly `"PROVISIONAL"` until
  promotion. Adding trend classification to the predicted-only series
  would roughly double this feature's complexity for a much smaller
  payoff than the onset estimate itself; noted here rather than built.
- `priority` (FMP triage rank) is computed identically for provisional
  and real tracks (both use `peak_complexity`). Arguably a provisional
  track's priority should be visually/numerically distinguished further
  from a real one beyond status + confidence; not built.
- Frontend rendering of the new `provisional_*` fields (status color,
  a "first flagged N min ago" label, etc.) -- backend-only in this pass,
  see note above.

---

## Item 2 — Complexity scoring can't flag certain same-heading/same-altitude conflicts — **DONE**

### The problem, and what investigating it actually found

The original framing (see git history) was "two aircraft on the same
heading/altitude contribute zero to `heading_div_deg`/`alt_div_ft`, so
maybe the engine needs a sixth, closure-rate-aware component." Looking
at `astra.complexity.conflict.closest_point_of_approach` closely before
implementing that showed the premise was wrong: MTCA/LTCA conflict
detection already *does* correctly react to a same-heading, same-altitude
pair closing toward minima -- the CPA calculation is relative-velocity
based and has no heading/altitude blind spot at all. A same-heading pair
5 NM apart and closing genuinely does get `mtca_count = 1` when the
closure is severe enough, exactly like any other geometry.

The real bug was in how that `mtca_count`/`ltca_count` gets *normalised*:
`complexity_mtca_reference_count` (default 3) and
`complexity_ltca_reference_count` (default 5) are calibrated as "this
many *concurrent* conflict pairs = a fully saturated, maximally complex
scenario". A 2-aircraft cluster only has one possible pair
(`C(2,2) = 1`) -- so even a single, severe, already-inside-minima MTCA
conflict there gets normalised against a reference of 3, capping the
conflict sub-score at ~33% of its own weight *regardless of how severe
the conflict actually is*. Combined with `heading_div_deg`/`alt_div_ft`
legitimately scoring zero for an identical-heading/altitude pair (30% of
the total weight), a 2-aircraft in-trail conflict was structurally
incapable of reaching `forecast_onset_threshold` (50) no matter how
close or fast-closing the pair got. Found empirically while re-reading
this project's own `arrival_sequencing` preset's docstring, which
explicitly (and, it turned out, wrongly) framed this as acceptable
because it was "only" a workload story, not a real conflict -- the
underlying traffic (a faster aircraft slowly overtaking a slower one
5 NM ahead, same track, same level) is exactly the geometry a real
separation bust often looks like, and the tool had no way to ever flag
it as one.

### What shipped

`ComplexityEngine._effective_conflict_reference(configured_reference,
member_count)`: caps the configured MTCA/LTCA reference at the cluster's
actual maximum possible pair count, `C(n, 2) = n * (n - 1) / 2`, floored
at 1. `_combine` now takes `member_count` and uses this scaled reference
instead of the raw config value when normalising `mtca_score`/
`ltca_score`. This only ever *lowers* the effective reference for
clusters smaller than the configured reference's own implied size (e.g.
2 or 3 aircraft against a reference of 3 or 5) -- every cluster at or
above that size gets the exact same behaviour as before the fix
(`arrival_rush`'s 5 aircraft / 10 possible pairs vs. a default LTCA
reference of 5 is completely untouched, for instance).

### What this changed, empirically (re-measured every affected preset)

Every existing 2-aircraft preset in `scenario_presets.py` involves at
least one MTCA/LTCA pair, so all of them scored higher after this fix --
`docs`/description strings for `crossing`, `merge`, `head_on`, and
`parallel_overtake` were re-measured against a live pipeline run and
corrected in place (old vs. new, at their own respective horizons):

| Preset | Before | After |
|---|---|---|
| `crossing` | ~31 pts -> ~54 pts by 10 min | ~38 pts -> ~56 pts by 5 min |
| `merge` | ~41 pts -> ~75 pts by 5 min | ~45 pts -> ~75 pts by 5 min |
| `head_on` | ~37 pts -> ~57 pts by 5 min | ~44 pts -> ~71 pts by 5 min |
| `parallel_overtake` | ~29 -> ~42 pts, never crosses 50 | ~44 pts -> ~56 pts by 15 min, **now crosses** |
| `arrival_rush` (5 ac, unaffected -- C(5,2)=10 > default reference 5) | ~47 -> ~80-89 pts | unchanged |
| `free_flow` (negative control, no cluster) | no cluster | unchanged |

The most consequential change is `arrival_sequencing` (the
`scenario_presets_operational.py` preset this whole investigation
started from): it previously plateaued at ~35-44 pts and never crossed
`forecast_onset_threshold`, requiring
`scenarios/arrival_sequencing_demo.py` to hand-build a `FourDArhac`
track to demonstrate a resolution candidate at all (`ResolutionEngine`
had nothing eligible to work with otherwise). After this fix, a live
multi-cycle `Pipeline` run shows the track reaching CONFIRMED by the
second poll cycle with a real `predicted_onset_s` of ~2760-3090s
(46-51.5 minutes out -- squarely inside this project's own 30-60 minute
target window) and `ResolutionEngine` proposing a ranked SPEED
adjustment automatically, no hand-built track needed. The preset's own
docstring, its `scenario_presets.py` entry description, and the demo
script's docstring were all updated to describe this as what it now is:
a workload story that ASTRA also correctly recognises as a genuine,
distant, resolvable conflict -- not a scenario deliberately kept below
the alert line.

All updated docstrings/descriptions were re-measured directly against a
live `Pipeline` run rather than derived analytically, consistent with
this project's existing "validated empirically" documentation style
(see `scenario_presets.py`'s own module docstring).

### Verification

`tests/test_complexity.py`: 3 new tests -- `_effective_conflict_reference`
directly (scales down for 2/3-aircraft clusters, never raises the
reference, floors at 1, unaffected for larger clusters); a full
`ClusterEngine` -> `ComplexityEngine` run on a genuine 2-aircraft
same-heading/same-altitude closing pair, asserting the conflict
sub-score now reaches its full weighted contribution rather than the
old ~33%-diluted value; and an explicit regression check that a
3-aircraft cluster's effective reference is byte-for-byte the
configured default. All 475 checks across the full suite pass (zero
regressions -- no existing test hardcoded an exact 2-aircraft composite
score, only component values and generic `_normalise` behaviour, both
unaffected by this change).

### Deliberately not touched

- `heading_div_deg`/`alt_div_ft` scoring zero for identical
  heading/altitude was *not* changed -- on reflection this is correct,
  not a gap: it truthfully reports "no heading/altitude diversity risk
  beyond whatever the conflict sub-score itself already captures",
  which is exactly right once that sub-score can actually saturate
  properly. No sixth component was added; the simpler, more targeted
  fix above addressed the actual root cause directly.
- `astra/dashboard/dashboard.js`/`.css` -- not touched, per the same
  boundary as item 1 (parallel UI work).

---

## Item 3 — Resolution engine follow-ups

Raised together as one item since each is small in isolation; grouped
here rather than split into 3a/3b/etc.

### Domino cost across all horizons — **DONE** (this session)

`ResolutionEngine._domino_cost` previously only compared the
hypothetical snapshot's *other* clusters against reality at
`evaluated_horizon_min` (the same horizon the primary before/after
comparison uses) -- a candidate that looked clean there could still
spike a different hotspot at an earlier or later horizon, uncounted.

Fixed by scanning every horizon `ResolutionEngine`'s own trajectory
engine predicts (`prediction.horizons_min`, plus horizon 0 via the
clearance's immediate effect) and taking the *worst* per-horizon
penalty, via a new `_domino_cost_at_horizon` helper factored out of the
original single-horizon logic (unchanged internally -- same
match-against-reality-or-count-as-new rule, just now called once per
horizon instead of once total). Because `evaluated_horizon_min` is
always one of the horizons scanned, this is a strict generalisation:
`domino_cost_norm` can only be greater than or equal to what the
original check would have returned for any given candidate, never
less -- confirmed by a dedicated regression test
(`test_domino_cost_never_less_than_single_horizon_check`).

`_evaluate`/`_build_joint_candidate`/`resolve()` were updated to thread
the full `regions_by_horizon` dict through instead of a single
extracted horizon's region list (`resolve()` already had it available;
it just wasn't being passed on).

**Cost tradeoff, as flagged when this item was first written down (see
item 4 below):** this does increase per-candidate work -- one
`ClusterEngine.detect()` + a `ComplexityEngine.assess()` per new
hypothetical cluster, now repeated at every configured horizon (9 by
default) instead of once. Measured directly rather than assumed: the
`arrival_sequencing` demo (2 aircraft) still runs in ~1.7s end to end;
the `sector_overload` demo (40 aircraft, 60 simulated poll cycles) in
~2.5s. Not a measured problem at this project's scale -- consistent
with item 4's own "not yet measured as an actual problem" framing,
which still holds after this change.

**Verification:** 2 new tests in `tests/test_resolution.py` -- one
hand-builds a `PredictionResult` where the evaluated horizon alone shows
no domino effect but a different horizon does (proving the scan catches
what the old single-horizon check would have missed), the other proves
the monotonicity guarantee above. 478/478 checks across the full suite
pass (zero regressions -- the existing domino-cost test only asserted
the generic `[0, 1]` range, unaffected by scanning more horizons).

### Fuel-cost proxy for SPEED/HEADING candidates — **DONE** (this session)

`ResolutionEngine._costs` previously hardcoded `fuel_cost_proxy_norm` to
`0.0` for both `SPEED` and `HEADING` candidates -- only `FLIGHT_LEVEL`
paid any fuel cost at all, regardless of how large a speed or heading
change was proposed.

Fixed with two lever-specific, deliberately crude proxies (consistent
with this scoring model's existing "not a real fuel-burn model, see
OQ-4" framing):

* `SPEED`: now the same value as its own deviation ratio, mirroring
  `FLIGHT_LEVEL`'s existing convention exactly -- a sustained speed
  change away from filed cruise, in either direction, costs fuel.
* `HEADING`: `|sin(radians(delta_value))|` -- the fraction of distance
  flown during the vector that goes sideways rather than toward the
  destination, bounded in `[0, 1]`, peaking at a 90-degree vector.
  Applies identically to `SUSTAINED` and `VECTOR_AND_REJOIN` candidates
  (`_costs` only looks at `clearance_type`/`delta_value`, not
  `maneuver_kind` -- even a bounded, rejoin-ending vector still flies
  extra distance during the vector phase itself). Deliberately smaller
  than the deviation cost for the same angle (`sin(15) ~= 0.26` vs. a
  deviation ratio of `1.0` for the same 15-degree base step) -- a
  heading nudge wastes proportionally less of its flown distance than
  its "operational deviation" magnitude alone would suggest.

Verified against a live pipeline run
(`astra/dashboard/arrival_sequencing_demo.py`): the previously best-ranked
`SPEED +20kt` candidate's score dropped from `+0.2445` to `+0.1445` --
exactly `resolution_weight_fuel (0.10) * 1.0` (its own deviation ratio,
now also its fuel cost) -- while its ranking relative to the other
candidates in that scenario stayed the same. A different scenario could
see fuel cost change which candidate wins, which is the intended
behaviour: SPEED and HEADING candidates were previously getting an
unearned "fuel is free" advantage over FLIGHT_LEVEL whenever their
complexity/domino/deviation terms were otherwise close.

**Verification:** 3 new tests in `tests/test_resolution.py` -- SPEED's
fuel proxy equals its deviation ratio and scales with magnitude;
HEADING's fuel proxy matches the `sin` formula directly, is smaller than
its own deviation cost for a modest angle, saturates at a 90-degree
vector, and applies identically to a `VECTOR_AND_REJOIN` spec; and an
explicit regression check that FLIGHT_LEVEL's formula is byte-for-byte
unchanged. 490/490 checks across the full suite pass (zero regressions
-- no existing test asserted `fuel_cost_proxy_norm` for SPEED/HEADING at
all, so this was previously untested behaviour, not just unimplemented).

### Target selection fallback — **DONE** (this session)

`select_target_aircraft_ranked` (`astra/resolution/candidates.py`) ranks
members by pairwise MTCA/LTCA conflict-pair count; for a
density/diversity-only cluster (no conflict pairs at all), it fell back
to alphabetically-first callsign -- not grounded in anything about the
actual traffic picture.

Fixed by breaking ties (including the all-zero-conflict-count case) by
distance from the cluster centroid instead, closest first: the most
central member is the one whose own movement does the most to actually
de-densify the cluster, since nudging an aircraft already near the edge
barely changes the cluster's density/extent. Alphabetical callsign order
is now only the final, last-resort tie-break, for aircraft exactly
equidistant from the centroid (keeps the ranking fully deterministic
even then). For any cluster where conflict counts actually differ
between members (the common case with a real conflict pair), ranking
behaviour is completely unchanged -- conflict count still dominates; only
the no-conflict-pairs tie case now uses a meaningful signal instead of
alphabetical luck.

Verified directly: a 3-aircraft density-only cluster with one member
sitting exactly at the centroid, one moderately off, and one far off
(callsigns chosen so alphabetical order would rank them wrong) now
correctly ranks by actual centroid distance instead of alphabetically.

**Verification:** 2 tests in `tests/test_resolution.py` -- one updated
(the existing no-conflict-pairs test now asserts the centroid-closest
member wins, not the alphabetically-first one), one new (equidistant
members still fall back to alphabetical order, confirming the
last-resort tie-break still works). 490/490 checks across the full
suite pass (zero regressions elsewhere -- only the one test whose
fallback assumption this change deliberately targeted needed updating).

### RVSM / level-parity awareness — **DONE** (this session)

FLIGHT_LEVEL candidates didn't check odd/even flight-level-by-track-
direction convention at all -- a proposed altitude change could recommend
a level that would put the aircraft on the wrong parity for its
direction of flight, with no cost consequence.

Fixed with a new pure function, `astra.resolution.candidates.matches_rvsm_parity`:
semicircular (odd-east/even-west) parity check, simplified to
whole-thousands-feet parity rather than the exact ICAO RVSM table (2000ft
spacing above FL290, 1000ft below) -- consistent with this scoring
model's existing "crude proxy" framing elsewhere. `ResolutionEngine._costs`
now adds a flat `resolution_rvsm_parity_penalty` (default 0.5) to a
FLIGHT_LEVEL candidate's `deviation_cost_norm` -- not `fuel_cost_proxy_norm`,
which stays a pure altitude-change-magnitude proxy -- whenever the
candidate's *resulting* altitude would be wrong for the target's current
track direction. A penalty, not a hard filter: a non-standard level
stays a scoreable (if usually worse) option rather than being silently
removed from the candidate set, since real ATC occasionally does assign
one with coordination.

A side effect worth noting, found while testing this: since the default
`resolution_altitude_step_ft` (1000ft) is an odd number of thousands, a
1x-magnitude FLIGHT_LEVEL step *always* flips parity relative to the
starting altitude, while a 2x-magnitude step (2000ft, from
`resolution_step_multipliers`) always preserves it. So whenever an
aircraft starts at a conventionally-correct level (the common case), its
1x FLIGHT_LEVEL candidates will now consistently show the RVSM penalty
and its 2x ones won't -- meaning the model now naturally prefers jumping
a full RVSM increment over assigning a non-standard intermediate level,
matching real practice, without that behaviour being explicitly coded in.

**Verification:** 8 new tests -- `matches_rvsm_parity` directly
(eastbound/westbound, boundary headings, heading normalisation);
`_costs` applying the penalty only when the resulting level is actually
wrong, and not when it's correct (including confirming a 2x step
preserves parity where a 1x step from the same base does not); and an
arithmetic-level confirmation that the penalty's weighted effect on
`resolution_score` matches the config formula exactly (deliberately not
a full end-to-end pipeline comparison -- doing so surfaced an unrelated
confound: changing a candidate's altitude by 2x the base step can also
change whether the hypothetical cluster still re-associates with the
track at all via `best_cluster_match`, which would have made a direct
score comparison misleading rather than clean). 506/506 checks across
the full suite pass (zero regressions -- no existing test asserted
`deviation_cost_norm` for FLIGHT_LEVEL against a specific track
direction, so nothing depended on the old parity-blind behaviour).

### Remaining sub-items (not started)

- **No feedback loop.** Nothing tracks whether a proposed clearance was
  actually issued (via `MockConnector`) and whether the predicted
  outcome held -- every cycle's `resolve()` starts from scratch with no
  memory of prior recommendations for the same track. Would need a
  design decision on where that state lives (a new field on
  `FourDArhac`? A separate store keyed by `arhac_id`?) before
  implementation -- flagged, not designed.

Not started.

---

## Item 4 — Performance: per-candidate full-snapshot replay cost

`ResolutionEngine._evaluate` does a full re-predict + re-cluster +
re-complexity-assess pass over the *entire* snapshot, once per
candidate. Since the Milestone 7 improvements (route-aware evaluation,
wider step-multiplier search, joint candidates) and the domino-cost
multi-horizon scan (item 3, done this session), that's meaningfully more
replay work per cycle than the original design: candidate counts went
from 4-6 to 8-12 per track, each now also running a ~9x-per-candidate
domino-cost scan (one horizon became nine), plus a joint-candidate pass
that itself runs several more single-aircraft evaluations to pick
secondary legs (see `ResolutionEngine._build_joint_candidate`). Measured
directly after the domino-cost change: the `arrival_sequencing` demo (2
aircraft) still runs in ~1.7s end to end, `sector_overload` (40
aircraft, 60 simulated poll cycles) in ~2.5s -- fine at this project's
scale; not yet profiled at 100+ aircraft or with many
simultaneously-eligible tracks in one cycle.

Possible directions once this is actually a measured problem (not
before -- no profiling has been done yet, this is a flagged risk, not a
confirmed bottleneck):
- Cache/reuse the *unmodified* full-snapshot prediction across
  candidates for the same track (only the target aircraft's entry
  differs between most candidates; the other aircraft's predicted
  positions are identical every time and currently recomputed anyway).
- Short-circuit candidate generation for levers unlikely to help (e.g.
  skip the 2x-magnitude tier for a lever whose 1x version already
  scored near the domino/deviation cost ceiling).

Not started -- not yet measured as an actual problem, just an
identified growing cost as other work builds on top of the resolution
engine.

---

## Item 5 — Test suite / pytest collision

Every `tests/test_*.py` file uses a custom `Runner`-based convention
(`tests/_runner.py`), meant to be run directly (`python3
tests/test_resolution.py`), not via pytest. But the function signature
`def test_x(r: Runner)` reads to pytest as a fixture request named `r`
-- if someone runs `pytest tests/` the normal way (a very natural first
thing to try on an unfamiliar repo), every single test fails with a
cryptic "fixture 'r' not found" error instead of a clear pointer to the
right invocation.

Fix is small and self-contained: either
- a `tests/conftest.py` that's just a comment explaining these aren't
  pytest tests and pointing at the right command (does not make them
  pass under pytest, just fails loudly and clearly instead of
  confusingly), or
- a `pytest.ini`/`pyproject.toml` `[tool.pytest.ini_options]` with
  `collect_ignore` or a naming convention change so pytest doesn't try
  to collect these files as its own tests at all (cleaner -- `pytest
  tests/` would just report "no tests collected" instead of failing),
  or
- rename the fixture-shaped parameter (e.g. `def test_x(runner: Runner)`
  doesn't help -- pytest fixture-injects by *name*, and `runner` isn't
  a known fixture either, so this alone doesn't fix it; the actual fix
  is one of the two options above, not a rename).

Not started -- flagged as a low-effort, high-clarity-payoff fix for
anyone else opening this repo cold.

---

## Item 6 — TrackerEngine could get stuck reporting DISSIPATING (or GROWING) during a genuine but slow trend reversal — **DONE**

Found via a direct user report: "its kinda stupid to say the hotspot is
dissipating when the complexity is getting higher and the aircraft is
about to meet and crash each other." Investigated and confirmed as a
real, reproducible bug, not a misunderstanding.

### The bug

`TrackerEngine._next_status` classified GROWING/PEAK/DISSIPATING purely
by comparing each cycle's `complexity_score` to the *immediately
preceding* cycle's score. Once a track entered `DISSIPATING`, escaping
back to `GROWING` required a single cycle's rise to exceed
`tracking_trend_tolerance` on its own -- comparing only to the previous
cycle reset the baseline every single cycle, so a genuine recovery
unfolding as many small steps (each individually under tolerance) could
hide from detection indefinitely, even while the score climbed
steadily and substantially over many cycles. The mirror case existed
too: a track stuck reporting `PEAK` (never advancing to `DISSIPATING`)
during a slow, genuine decline.

Reproduced directly before fixing (see git history for the exact
repro): a score sequence rising 50 -> 60 -> dipping to 56.0 (correctly
entering `DISSIPATING`) -> then climbing back up in 0.4-point steps,
56.4, 56.8, 57.2, ... all the way to 59.2 -- stayed `DISSIPATING` for
every one of those climbing cycles, only correcting once a single big
jump (to 65.0) finally exceeded tolerance on its own.

### What shipped

New `FourDArhac.trend_extremum_score` field: the running peak (while
`GROWING`/`PEAK`) or trough (while `DISSIPATING`) reached so far during
the *current* trend regime, maintained by a new
`TrackerEngine._next_trend_extremum` helper. `_next_status` now compares
against this regime-local extremum instead of just the previous cycle
when deciding whether to leave `PEAK` or `DISSIPATING` -- so a genuine
cumulative reversal is caught even when no single step crosses the
tolerance alone.

Care was taken to preserve two properties of the original design that a
naive fix could have broken:

* **`GROWING` still always passes through `PEAK` for at least one cycle**
  before `DISSIPATING`, regardless of how sharp the reversal is --
  `GROWING`'s own transition rule still compares only to the immediately
  preceding cycle (matching the original design exactly), so the
  "local maximum" marker semantics of `PEAK` are unchanged. Hysteresis
  only applies once a track is already in `PEAK` or `DISSIPATING`
  deciding whether to leave.
* **A large, unambiguous single-cycle reversal is still immediately
  responsive** -- the fix only changes behaviour for slow/noisy
  reversals that a single-previous-cycle comparison would miss; a sharp
  crash or recovery is detected exactly as fast as before.

### Verification

5 new tests in `tests/test_tracking.py`: the exact reported bug
(DISSIPATING recovering from a slow cumulative climb), the mirror case
(PEAK correctly advancing to DISSIPATING during a slow decline, not
stuck forever), PEAK correctly holding through small genuine noise
(confirming the fix doesn't overcorrect into flapping), the extremum
field's own reset/initialisation behaviour, and a regression check that
a big single-cycle reversal is still immediately responsive. 523/523
checks across the full suite pass (zero regressions to any existing
test -- none of them exercised a multi-cycle slow-reversal sequence,
which is exactly why this bug shipped unnoticed in the first place).

---

## Item 7 — Dashboard resolution-candidate cap hardcoded to a fixed 3, silently dropping real candidates — **DONE**

Found via a direct user report: "why is the solution proposal always 3,
cant they make more proposal? ... it dont need to always be 5 or 3, it
can be 1 or 2 solution too." Investigated and confirmed: not a frontend
rendering choice, a backend serializer cap.

### The bug

`ASTRAConfig.dashboard_max_resolution_candidates_shown` defaulted to
`3`, and `serialize_resolution_set` truncates the ranked candidate list
to that count before it ever reaches the dashboard. Since the Milestone
7 resolution-engine improvements (wider step-multiplier search, up to
~12 single-aircraft candidates per track now, plus an optional joint
candidate), this cap was silently dropping the majority of what
`ResolutionEngine` actually generated and ranked -- confirmed directly:
a live 3-aircraft scenario generated 12 real candidates, of which the
dashboard would only have shown 3, silently discarding 9.

### What shipped

Raised the default to `20` -- comfortably above any realistic candidate
count today, functioning as a safety upper bound rather than a display
page size (the field's docstring was rewritten to say so explicitly:
"a track with 1 or 2 real options should show exactly that many, not be
padded or truncated to a fixed count"). This is a one-line default
change plus documentation -- the underlying mechanism (a configurable
cap, capable of showing any count) was already correct; only the
default value was the problem.

Per the user's own request, this backend change deliberately stops
short of implementing the fixed-size-page-with-arrows carousel UI
described (show 5 at a time, `<`/`>` to page through more) -- that is
frontend/rendering work, out of scope for this backend-only track (see
this file's header note on the boundary with the parallel UI redesign
work). What this fix guarantees is that the frontend now has the *real*
data to build that UI against: the full ranked list `ResolutionEngine`
generated, not a silently-truncated one.

### Verification

1 new test in `tests/test_dashboard.py` confirming variable candidate
counts (1, 2, 5, 9) all pass through the serializer unpadded and
untruncated at the new default cap -- directly validating the user's
"it dont need to always be 5 or 3, it can be 1 or 2" requirement. The
existing cap-enforcement and empty-list tests already covered the
mechanism generically (both continue to pass unchanged). 527/527 checks
across the full suite pass (zero regressions -- the one test that
hardcoded the old default value of `3` was updated to `20`, matching
the intentional default change).

---

## Item 8 — Resolution engine: proactive whole-lookahead evaluation + diverse multi-aircraft candidates — **DONE**

### The problem

Two related gaps found while reviewing `ResolutionEngine` against the
strategic (30-60 min out), multi-agent framing the thesis is built
around:

1. **Timing.** `resolve()` evaluated candidates at exactly one horizon
   -- `_closest_horizon(track)`, the configured horizon nearest
   `track.predicted_onset_s` -- rather than sweeping the lookahead.
   This was a deliberate OQ-5 cost bound, documented as such, but the
   practical effect was that a hotspot's resolution wasn't proposed
   until close to its predicted onset, not as soon as the hotspot was
   forecast at all. A track detected with a 45-minute onset got
   evaluated once, near the 40/50-minute horizon -- nothing was ever
   shown for the horizons in between, and nothing changed as the
   picture evolved cycle to cycle other than that single point sliding
   forward.
2. **Diversity.** `_build_joint_candidate` (singular) built exactly one
   multi-aircraft candidate, only for clusters of 3+ resolvable
   members, with every secondary aircraft restricted to a SPEED-only
   search. A 2-aircraft conflict -- the most common real case -- never
   got a joint option at all, and there was no way to compare, say,
   "both aircraft turn" against "primary turns, secondary slows down"
   -- only ever one fixed combination.

### What shipped

**`astra/resolution/engine.py`:**

- New `_lookahead_horizons(track)`: every configured horizon (ascending)
  up to and including the predicted onset lead time. `_closest_horizon`
  is kept, unchanged, as the single-value fallback this uses when a
  track has no onset estimate yet, and for any other caller that still
  wants one representative horizon.
- `resolve()` rewritten to evaluate every horizon `_lookahead_horizons`
  returns (not just one), building `candidates_by_horizon` (the full
  strategic view) and picking the earliest horizon with a genuinely
  effective option (`complexity_delta_norm > 0`) as
  `evaluated_horizon_min` -- so the top-level `candidates`/
  `joint_candidates` a caller sees by default is still one clear
  recommendation, not the whole sweep dumped with nothing prioritised.
- `_build_joint_candidate` (singular) replaced by
  `_build_joint_candidates` (plural): now allows exactly 2 resolvable
  cluster members (both must move -- "leave one aircraft fixed" only
  makes sense for 3+, where it's preserved unchanged from the original
  design), and tries every lever set in the new
  `resolution_joint_secondary_levers` config list for each secondary
  aircraft, producing one independently-scored
  `JointResolutionCandidate` per (lever-set, secondary aircraft)
  combination -- e.g. primary HEADING + secondary HEADING, primary
  HEADING + secondary SPEED, as distinct options rather than one fixed
  pairing. The apply/predict/rescore tail is factored out into
  `_score_joint_legs` so every combination reuses the same scoring path.
- Fixed a fencepost bug in an early draft of the 2-member support: the
  original single-candidate code's secondary-selection slice
  (`ranked[1:max_targets]`) relied on `len(ranked) >= 3` to ever select
  a secondary at all; naively generalising it without accounting for
  that off-by-one would have silently produced zero joint candidates
  for exactly the 2-aircraft case this item exists to fix. Caught via
  a live pipeline run before landing, not via the unit tests (which
  were written against the same, then-still-buggy assumption).

**`astra/resolution/models.py`:**

- `ResolutionSet.joint_candidate` (singular) → `joint_candidates`
  (`List[JointResolutionCandidate]`), sorted descending by
  `complexity_delta_norm`. The old singular name is kept as a
  `@property` returning the best-`resolution_score` entry of the new
  list, so existing single-joint-candidate consumers (dashboard
  serializer, `test_tracking.py`) needed no changes.
- New `ResolutionSet.candidates_by_horizon: Dict[int, List[ResolutionCandidate]]`
  -- every horizon actually evaluated this cycle, not just the
  recommended one.
- New `ResolutionSet.ranked_by_impact()`: single- and multi-aircraft
  candidates at `evaluated_horizon_min` merged into one list, sorted by
  `complexity_delta_norm` (pure complexity reduction) rather than the
  weighted `resolution_score`. Deliberately a separate method rather
  than replacing the existing `resolution_score`-based ordering on
  `candidates`/`joint_candidates`/`best_overall()`: `resolution_score`
  also subtracts domino/deviation/fuel cost, so the top of the impact
  ranking is not always the top of the cost-adjusted one (e.g. a joint
  candidate that cuts complexity the most but moves aircraft a long way
  can rank #1 on impact while ranking lower once cost is weighed in).
  Both fields stay on every candidate so a caller -- or the dashboard --
  can render either view, or both, without recomputing anything.

**`astra/utils/config.py`:**

- New `resolution_joint_secondary_levers: List[List[str]]`, default
  `[["SPEED"], ["HEADING"], ["FLIGHT_LEVEL"]]` -- which single-lever
  searches to try per secondary aircraft in a joint candidate.
  Deliberately one lever per entry, not a cross-product of levers
  within one leg, for the same bounded-search reason the original
  speed-only restriction existed: 2-3 aircraft x every lever x every
  combination is a genuine combinatorial blow-up the exhaustive,
  no-optimisation-library approach this project uses elsewhere isn't
  designed for. Validated non-empty with only known clearance types.

**`astra/dashboard/serializers.py`:**

- `serialize_resolution_set` now also emits `joint_candidates` (the
  full diverse list), `horizons_evaluated` (keys of
  `candidates_by_horizon`), and `ranked_by_impact` (the impact-sorted
  merged view), alongside the unchanged `candidates` and the
  backward-compatible singular `joint_candidate`.

### Known follow-on work (not done this session)

- **Dashboard.js** still only renders the single best joint candidate
  (via the unchanged `joint_candidate` field) and the single
  recommended horizon. The backend now serializes the full multi-
  horizon, multi-candidate picture (`horizons_evaluated`,
  `joint_candidates`, `ranked_by_impact`) but nothing in the frontend
  consumes it yet -- a horizon selector/timeline and a way to list 2-3
  joint candidates per track (not just the one "J" chip) are frontend
  work, out of scope for this backend-only track (see this file's
  header note on the boundary with the parallel UI redesign work).
- **Per-cycle cost.** Sweeping every lookahead horizon (typically 1-5,
  depending on how far out the onset is) multiplies the existing
  per-candidate full-snapshot replay cost from item 4 by roughly that
  many -- item 4 ("per-candidate full-snapshot replay cost", not
  started) is now more urgent than before this item shipped. Not
  profiled this session; worth doing before relying on this in a
  higher-traffic scenario than the test fixtures use.

### Verification

`tests/test_resolution.py` updated: the old
`test_engine_joint_candidate_absent_for_two_member_cluster` (asserted
no joint candidate for 2 members -- exactly the behaviour this item
reverses) replaced with
`test_engine_joint_candidates_present_for_two_member_cluster`, and
`test_engine_joint_candidate_for_three_member_cluster` /
`test_engine_joint_candidate_capped_by_config` updated for the plural
API. New assertions cover: multi-horizon sweep width and content,
joint-candidate leg counts and target pairing for both 2- and 3-member
clusters, impact-sorted ordering of `joint_candidates`, and consistency
between `best_overall()` / `joint_candidate` (singular,
backward-compatible) and the underlying `joint_candidates` list.

Full suite: 427/428 checks pass. The one failure
(`dashboard_max_resolution_candidates_shown default` in
`tests/test_dashboard.py`, asserting the old pre-item-7 default of `3`)
predates this session's changes entirely -- confirmed via `git stash`
before touching anything -- and is unrelated to this item; left as-is
rather than fixed opportunistically, since item 7's own verification
note already explains the intentional `3` → `20` default change that
made it stale.

Also verified live against the project's own `_converging_snapshot()`
fixture with a 30-minute onset: `resolve()` swept horizons
`[5, 10, 15, 20, 30]` in one call (previously: `[5]` only), and produced
three diverse joint candidates -- `HEADING+SPEED`, `HEADING+HEADING`,
`HEADING+FLIGHT_LEVEL` -- each independently scored and impact-ranked,
where the old code would have produced exactly one, speed-only.

