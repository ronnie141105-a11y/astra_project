# Ho Chi Minh FIR / ACC — GIS Extraction Log

## Status: AIP-sourced rebuild (supersedes the earlier chart-only drop)

The first drop of these files was traced from `HOCHIMINH_6SECTORS_CHART.pdf`
(a simplified overview chart) and carried several `confidence: "low"` sectors
because the chart alone didn't give exact vertex lists for sectors 1, 2, 5, 6.

This drop replaces that geometry with vertex lists read **directly from AIP
Vietnam ENR 2.1** (`VV-ENR-2_1-en.pdf`), which gives an exact closed-polygon
definition per sector and altitude layer. Where the AIP disagreed with the
old chart (e.g. Sector 6's true shape, the existence of Sector 7), the AIP
wins per instruction. Routes are new in this drop, sourced from **ENR 3.1**
(`VV-ENR-3_1-en.pdf`).

- **Extraction date:** 2026-07-06
- **Coordinate format:** source is DMS, WGS-84 (`DD°MM'SS"N/E`); converted to
  decimal degrees, GeoJSON `[lon, lat]` order.
- **Scope, per instruction:** FIR VVHM + Sectors 1, 2, 5, 6, 7 only (Sectors 3
  and 4 from the first drop are NOT refreshed against the AIP and should be
  treated as chart-derived / carried-over until revisited).

## Files
| File | Contents |
|---|---|
| `geo/waypoints.json` | 68 named points (30 chart-legend points + 38 new AIP/route points), deduplicated |
| `geo/firs.json` | Ho Chi Minh FIR (VVHM), 1 polygon |
| `geo/sectors.json` | 10 polygons: Sectors 1a/1b, 2a/2b, 5a/5b/5c, 6a/6b, 7 |
| `geo/airways.json` | 9 ATS route segments (3 each for Sectors 1, 2, 5) |

## What came directly from the AIP (high confidence, exact vertex lists)
- **FIR VVHM** — 12 boundary vertices, verbatim from ENR 2.1 lateral limits text.
- **Sector 1a** (GND–FL265): DN4–SOSPA–D20–D21–VIMUT–D05–D04–NDB_CQ–D02–D01–D23–D24
- **Sector 1b** (FL265–FL460): D02–NDB_CQ–D04–D05–VIMUT–DN4–D24–D23–D01
- **Sector 2a** (GND–FL460): D01–E01–D23–D24–DN4–E04–E02–E03–RUNOP–D15
- **Sector 2b** (GND–FL305, additional area): DN4–SOSPA–E04
- **Sector 5b** (FL305–FL460): DN4–SOSPA–D20–D19–D18–DN7–DN6–E04
- **Sector 5c** (FL265–FL460): VIMUT–D21–D20–SOSPA–DN4
- **Sector 6a** (GND–FL460): E02–E04–DN6–DN7–D18–E03
- **Sector 6b** (GND–FL305): E04–SOSPA–D20–D19–D18–DN7–DN6
- **Sector 7** (GND–FL255): D15–RUNOP–D18–D19–D25–D26–D27–NDB_RG–D28
- All 9 route (`airways.json`) point sequences and their designators, COP
  remarks, and sector/altitude ownership notes — transcribed from ENR 3.1.

## What was approximated (and why)
1. **FIR closing segment** (last vertex `106°24'00"E` back to first `103°44'00"E`):
   the AIP describes this only as "along the Vietnamese-Laotian and
   Vietnamese-Cambodian border," with no coordinate list in the excerpt
   provided. Approximated as a straight line. `confidence: "medium"`.
2. **Sector 1a/1b closing edge** (D02↔D01) and **Sector 2a closing edge**
   (D15↔D01): AIP says "along the Vietnamese and Cambodian border." Same
   treatment — straight-line approximation. Sectors otherwise `"high"`.
3. **Sector 7 closing edge** (D28↔D15): AIP says "along the boundary of Ho
   Chi Minh and Phnom Penh FIR." Straight-line approximation.
4. **Sector 5a (GND–FL460, the main/lower Sector 5 footprint) — `confidence:
   "low"`.** The AIP closes this boundary via: `...D08 → along HCM/Singapore
   FIR boundary → along HCM/Manila FIR boundary (114°E) → crossing point
   with latitude 14°42'12"N → along that latitude → DONDA → EXOTO → D05`.
   **DONDA and EXOTO coordinates were not present in the supplied AIP
   extracts** (ENR 2.1's points table doesn't list them, and the ENR 3.1
   excerpt didn't include the routes — M771/L642/N892 — that would have
   given them). Per explicit instruction, this edge is approximated as two
   straight segments: `D08 → (114°E, 14°42'12"N) → D05`, skipping the real
   FIR-boundary jogs and the DONDA/EXOTO vertices entirely. **This is the
   single geometrically weakest polygon in the dataset — revisit if exact
   fidelity is ever needed.**

## Explicitly skipped (documented gap, not guessed)
- **Sector 6 and Sector 7 routes.** No route in the supplied ENR 3.1 excerpt
  has "Sector 6" or "Sector 7" in its Controlling-unit column. Sector 7 is a
  2025 sectorization addition (per the ENR 2.1 diagram note: "ADDITION OF
  SECTOR 7, ADJUSTMENT OF SECTOR 3 AND 4," AIRAC AMDT 02/25), and the
  ENR 3.1 excerpt's route entries still carry pre-2025 sector labels for
  that geographic area. Rather than infer which routes now belong to
  Sectors 6/7 from geometry alone, no routes were generated for them.
  `airways.json`'s schema is unchanged (same `designator` / `sectors` /
  `waypoints` / `note` / `source` properties), so routes for these two
  sectors can be appended later without a schema migration.
- **Sectors 3 and 4** were not re-verified against this AIP pass; they still
  carry the geometry (and confidence flags) from the first, chart-only drop.

## Validation performed on this drop
- All polygon rings closed (first vertex == last vertex).
- All polygon rings re-wound to consistent CCW (RFC 7946) orientation via a
  signed-area check (`ensure_ccw()`), applied programmatically to every ring.
- No self-intersecting polygon edges (checked pairwise, all 10 sector
  polygons + FIR).
- No duplicate waypoint keys in `waypoints.json` (68 unique names).
- Every vertex name referenced by a sector or route resolves to an entry in
  `waypoints.json` — no dangling references (`CQ`/Chu Lai NDB unified under
  the single key `NDB_CQ`; `RG`/Rach Gia NDB unified under `NDB_RG`).

## Confidence summary
| Layer | Confidence | Basis |
|---|---|---|
| FIR VVHM | medium | exact vertices, approximated land-border closure |
| Sector 1a / 1b | high | exact AIP vertices, minor border approximation |
| Sector 2a / 2b | high | exact AIP vertices, minor border approximation |
| Sector 5a | **low** | DONDA/EXOTO missing; closing edge approximated |
| Sector 5b / 5c | high | exact AIP vertices, no approximation needed |
| Sector 6a / 6b | high | exact AIP vertices, no approximation needed |
| Sector 7 | high | exact AIP vertices, minor FIR-boundary approximation |
| Sectors 3 / 4 (carried over) | unchanged from first drop | chart-traced, not AIP-verified |
| Routes (9, Sectors 1/2/5 only) | high | exact ENR 3.1 point sequences |
| Waypoints (68) | high | all coordinates transcribed directly from source tables |

## Remaining known limitations
- Sector 5a eastern edge is a rough approximation (see above) pending
  DONDA/EXOTO coordinates.
- No routes for Sectors 6/7 pending clarification of post-2025 route
  ownership.
- Land-border and FIR-boundary closing segments (FIR VVHM, Sectors 1/2/7)
  are straight-line stand-ins for the real (curved, multi-vertex) national
  borders described only qualitatively in the AIP text.
- This is a bachelor's-thesis prototype dataset — good enough for
  visualization and demo logic in ASTRA's dashboard, **not** survey-grade
  and not suitable for operational use.

---

## Update 2026-07-06 (later same day): `airways.json` replaced, `navaids.json` added

**`airways.json` was fully discarded and rebuilt from scratch** per explicit
request ("the old ones are all over the place"). The previous 9 routes
(B202, G474, R588, M505, L644, W15, L628, Q15, M765 — picked to cover
Sectors 1/2/5) are gone. The file now contains exactly the 9 routes
requested, transcribed point-by-point from `VV-ENR-3_1-en.pdf` (ENR 3.1):

| Designator | Points | Spans |
|---|---|---|
| W1 | NOB→LOVBI→NAH→MAREL→VIDAD→HATIN→XONUS→HAMIN→PHULU→CAHEO→DAN→VILOT→XAQUA→PLK→MEVON→BMT→ENRIN→AC→ESDOB→TSH | Ha Noi ACC Sectors 1-4 → Ho Chi Minh ACC Sector 1 |
| W2 | NAH→VIN→KAMSU→DONGI→KONCO→BIGBO→HUE→DAN→CQ→KUMUN→PCA→KAMGO→KARAN→NHATA→CRA→IBUNU→PTH→VEPMA→AC→TSH | Ha Noi ACC Sectors 2-4 → Ho Chi Minh ACC |
| W15 | AC→VETOM→LKH→SOSPA→CRA | HCM Sector 2 (≤FL265) / Sector 5 (>FL265) |
| W7 | LKH→ONEBI→BMT | HCM Sector 2 (≤FL265) / Sector 5 (>FL265) |
| W12 | PCA→NOBID→BMT→PATMA→DONXO→RUNOP→MOXEB→TRN | HCM Sector 1 |
| W9 | TSH→XOBAV→NIXIV→CN | HCM Sector 3 |
| W16 | TSH→ENPAS→TRN→RG | HCM Sector 3 |
| L637 | BITOD→BIBAN→ANHOA→BITIS→TSH | HCM Sector 3; continues to AIP Singapore |
| W19 | TSH→VTV→TULTU→LITAM→CN | HCM Sector 3 |

Each feature is a `LineString` with properties `designator`, `waypoints`
(ordered list of point names), `levels` (upper/lower limits as printed),
`note` (sector/COP context), `source`. Confidence: **high** — every
coordinate transcribed directly from the ENR 3.1 tables, no approximation.

**`navaids.json` is a new layer**, added because these are structurally
different from ordinary route waypoints (they're physical transmitting
stations with frequency/channel/hours/elevation, not just lat/lon fixes) and
the user asked for them to sit in a "distinguished section" rather than be
folded into `waypoints.json`. Contains exactly the 10 requested idents —
AC, BMT, VTV, TRN, CN, LKH, PTH, QL, PLK, TSH — transcribed from
`VV-ENR-4_1-en.pdf` (ENR 4.1). Each feature carries `ident`, `name`, `type`
(NDB / DVOR/DME / CVOR/DME), `frequency`, `channel`, `hours_of_operation`,
`elevation_m`, `layer_kind: "navaid"`. Confidence: **high**, direct
transcription.

**Note on a coordinate discrepancy:** ENR 4.1 lists BMT at `123959N
1080722E`, one arc-second west of the `1080723E` used for BMT elsewhere
(ENR 3.1 route tables, and thus in `waypoints.json`/old `airways.json`).
This is almost certainly a rounding artifact between AIP sections, not a
real navaid relocation. `navaids.json` uses the ENR 4.1 value (its own
authoritative source); `waypoints.json` still uses the ENR 3.1 value. Left
as-is rather than silently "fixed," since neither this session nor the
user has grounds to prefer one AIP table over another — flagging it here
for awareness.

**Validation performed:** all 9 routes have ≥2 points and unique
designators; all 10 navaids have unique idents; all coordinates within
valid lon/lat ranges; every route point name resolves against the local
point table used to build this file (no dangling references).

**Not done / out of scope of this update:** `waypoints.json`, `firs.json`,
and `sectors.json` are unchanged from the previous entry above. The
dashboard's `manifest.json` (which lists which `geo/*.json` files the
GeoLayerManager loads) has **not** been updated to register the new
`navaids.json` file — that's an application-config change outside this
session's remit; whoever owns the dashboard code will need to add a
`navaids` entry pointing at `geo/navaids.json` for it to actually render.
