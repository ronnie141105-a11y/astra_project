# ASTRA thesis test scenarios & results

This package gives you a BlueSky `.scn` scenario that exercises the
full ASTRA pipeline (Trajectory → Cluster → Complexity → Tracking →
Forecast → Resolution, including this session's domino-effect scoring
and expanded candidate search), a script to run any `.scn` file offline
without a live BlueSky install, a standalone deterministic demo that
isolates the new domino-effect penalty, and the **real data** these
already produced when run in this environment — ready to drop into
your results chapter.

## Files

**Scenarios** (`.scn`, BlueSky stack-command format — also load in a
real BlueSky instance unchanged):

| File | What it tests |
|---|---|
| `thesis_multi_hotspot.scn` | Two independent 4-aircraft crosses ~55 NM apart, simultaneously active. **Multi-track stress test**: concurrent tracking/forecasting/ranking, `resolution_max_tracks_per_cycle`. |

`thesis_baseline.scn` (control condition, 6 well-separated aircraft)
and `thesis_converging_hotspot.scn` (4-aircraft symmetric converging
cross, the primary single-hotspot demo) have since been retired — they
predated this project's route-aware `flight_type` ("LANDING"/
"OVERFLIGHT") spawn profiles and just flew a fixed heading forever with
no destination-aware behaviour. Their results from this run are still
kept below and in `Data/` for reference (`baseline_cycles.csv` /
`baseline_detail.json`, `converging_cycles.csv` / `converging_detail.json`).

**Scripts:**

- `run_scn_offline.py` — loads any `.scn` file into the offline
  `MockConnector` (no BlueSky process needed) and runs it through
  `astra.pipeline.Pipeline` cycle by cycle, logging a CSV summary +
  full JSON detail per cycle. This is how the `*_cycles.csv` /
  `*_detail.json` files below were produced. Usage:
  ```
  python3 run_scn_offline.py thesis_multi_hotspot.scn \
      --duration-min 20 --sim-step-s 15 --out-prefix multi_hotspot
  ```
- `domino_effect_demo.py` — standalone, no BlueSky/MockConnector at
  all; calls `ResolutionEngine.resolve()` directly against a
  hand-built scenario engineered so two otherwise-similar HEADING
  candidates get identical deviation/fuel cost but only one of them
  flies into a second, independent, already-real hotspot. Isolates
  exactly what `domino_cost_norm` (this session's main addition)
  contributes on top of the existing complexity-delta scoring. See the
  big docstring at the top of the file for the full geometry rationale
  and why the heading step is deliberately amplified (90° vs
  production's 15° default) purely to make the two hotspots separable
  within one 5-minute demo horizon — documented in detail there so you
  can cite the reasoning directly.

**Generated data** (already run for you in this environment):

- `baseline_cycles.csv` / `baseline_detail.json`
- `converging_cycles.csv` / `converging_detail.json`
- `multi_hotspot_cycles.csv` / `multi_hotspot_detail.json`
- `domino_effect_demo_results.json`

## Headline results from this run

**Baseline (control):** 60 cycles, 15 sim-minutes. `max_complexity_observed`
stayed exactly `0.0` and `n_open_tracks` stayed `0` for every cycle —
zero false-positive hotspots on well-separated traffic.

**Converging hotspot:** peak observed complexity **83.0**. Track
lifecycle: `CANDIDATE`(t=15s, score 44.1) → `CONFIRMED`(t=30s, score
45.0, **forecast sets `predicted_onset_s`**) → `GROWING` → `PEAK`(score
83.0) → `DISSIPATING`. Resolution triggered at the one cycle where the
track was `CONFIRMED`+forecasted-but-not-yet-critical (t=30s), producing
6 ranked candidates (both directions × SPEED/FLIGHT_LEVEL/HEADING) for
the busiest aircraft. This is the intended, designed-for use case:
`ResolutionEngine` only engages once `ForecastEngine` has a real
predicted onset — see `docs/PROJECT_STATUS.md`'s note on why an
*already*-critical hotspot (`predicted_onset_s is None` — "onset
already happened") is a different code path.

**Multi-hotspot:** both hotspots detected and tracked concurrently
(peak complexity 77.6 and 45–56 respectively at the point resolution
triggered), confirming the tracker/forecaster/resolver all handle
simultaneous, independent tracks correctly and rank by urgency.

**Domino-effect demo:** the clean, isolated result —

| Candidate | domino_cost_norm | complexity_delta_norm | resolution_score |
|---|---|---|---|
| HEADING −90° | **0.000** | 0.775 | **+0.2761** (best) |
| HEADING +90° | **0.315** | 0.457 | +0.0384 |
| SPEED ±20 kt | 0.000 | 0.000 | −0.1500 |
| FLIGHT_LEVEL ±1000 ft | 0.000 | 0.000 | −0.2500 |

Both HEADING candidates have identical deviation/fuel cost and
similar complexity-reduction on the *primary* conflict — only
`domino_cost_norm` tells them apart, correctly ranking the clean turn
above the one that flies into another aircraft's flight path.

## Reproducing live in BlueSky

`thesis_multi_hotspot.scn` loads unchanged in a real BlueSky instance
(`python -m bluesky`, then load the scenario, or place it in BlueSky's
`scenario/` folder and `IC` it) — it deliberately avoids route/waypoint
commands `MockConnector` doesn't implement, so the offline run above
and a live run see identical initial traffic. Point `main.py` (live
mode) at the running BlueSky node as usual; the dashboard will show
the same clustering/tracking/forecast/resolution behaviour reflected
in the CSV/JSON here.

## Notes for your thesis

- `thesis_multi_hotspot.scn` is anchored on the HCM FIR (10.80N,
  106.70E), matching the rest of the project's demo data; the two
  retired scenarios' archived results below used the same anchor.
- Aircraft speeds in the hotspot scenarios (110–130 kt) are
  terminal-area/early-approach speeds, not cruise — deliberately
  chosen so each scenario starts *below* `forecast_onset_threshold`
  (50) and is forecast to cross it, rather than starting already
  critical (which bypasses `ForecastEngine.predicted_onset_s`
  entirely — see the converging-hotspot result above).
- The known limitation that `TrackerEngine` only reads
  observed (horizon-0) regions — so a cluster must already exist in
  the *current* snapshot to ever become a track, even if a future
  horizon predicts one — applies to all of these scenarios; it's why
  every scenario's aircraft start within the 15 NM/1000 ft DBSCAN
  neighbourhood of each other rather than converging from further out.
  This is documented as open/deferred work in `docs/PROJECT_STATUS.md`.
