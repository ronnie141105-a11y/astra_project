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

---

## Update 2026-07-23: `waypoints.json` expanded from ENR 4.4, `sectors.json` Sector 5a fixed

**Note:** `manifest.json` already registers a `navaids` entry pointing at
`geo/navaids.json` as of this session -- the gap flagged immediately above
(2026-07-06) has since been closed, presumably in a later session not
captured in this log. Left as a correction here rather than editing the
entry above, per this log's own convention of appending rather than
rewriting history.

**Source:** `VV-ENR-4_4-en.pdf` (AIP Viet Nam, ENR 4.4 "Name-code
designators for significant points"), all 4 pages, amendment dates 15 JUL
2023 (page 1) / 30 JUN 2026 (pages 2-4) as printed in the source. This is
the first session to use this specific AIP section -- previous sessions'
`significant_point` entries in `waypoints.json` came from ENR 2.1/3.1
(sector/route tables), not from ENR 4.4's dedicated point-designator table.

**What changed:**

1. **121 new `significant_point` features added to `waypoints.json`**
   (68 -> 188 total features; 31 -> 152 `significant_point` entries). ENR
   4.4 lists 152 name-code designators total; 31 were already present
   (added in earlier sessions from ENR 2.1/3.1) and matched exactly on
   coordinates (**zero discrepancies** found between the two AIP sections
   for any of the 31 overlapping points, cross-checked to the nearest
   arc-second). The remaining 121 were missing and are now added, each
   with `category: "significant_point"`, `note: "Route <ATS routes
   column, verbatim>"`, and `source: "VV-ENR-4_4-en.pdf (AIP Viet Nam)"`.
   - One further ENR 4.4 name, **OSIXA**, was already present under a
     *different* category (`sector_boundary_point`, added in the
     2026-07-06 drop as a Sector 5 vertex) -- confirmed same coordinates
     (109.843889E, 9.522222N, exact match), so it was **not** duplicated;
     instead its existing `note` was extended to record that it's also a
     named ENR 4.4 significant point on route N892, so both roles are
     visible on the one entry.
   - Coordinate conversion: DMS (`DDMMSSN` / `DDDMMSSE`) -> decimal
     degrees -> GeoJSON `[lon, lat]`, matching this file's existing
     convention exactly.
   - New features were appended to the end of the array (not interleaved
     alphabetically with the existing 68) -- consistent with this file's
     existing order, which is grouped by when each batch was added, not
     globally alphabetical (see e.g. the `D01..D28` sector-boundary block
     staying contiguous despite later significant points spanning the
     whole alphabet).

2. **`DONDA` and `EXOTO` fix `sectors.json`'s Sector 5a, the single
   lowest-confidence polygon flagged in this log's very first entry
   (2026-07-06: "the single geometrically weakest polygon in the
   dataset").** That entry's boundary-closure text names these two points,
   but their coordinates weren't in any AIP extract supplied at the time,
   so the D08->D05 closing edge was approximated as two blind straight
   segments through an unnamed crossing point. ENR 4.4 gives both real
   coordinates: **DONDA 14°42'12"N 112°01'18"E (route M771), EXOTO
   15°21'30"N 111°03'00"E (route L642)**. Notably, DONDA's latitude
   (14.703333°) is *exactly* the crossing-point latitude (14°42'12"N)
   quoted in the original ENR 2.1 boundary text -- strong independent
   confirmation these are the right points for this edge, not a
   coincidental name match. The ring was updated from
   `...D08 -> (114E, crossing lat) -> D05` to
   `...D08 -> (114E, crossing lat) -> DONDA -> EXOTO -> D05`; confidence
   raised `low` -> `medium` (the crossing-point-to-D05 portion is now
   exact; only the D08-to-crossing-point FIR-boundary jog itself remains
   a straight-line stand-in, since no extract has that boundary's vertex
   list). `vertex_source` and `note` updated accordingly; `source` now
   cites both ENR 2.1 (boundary text) and ENR 4.4 (point coordinates).

3. **`manifest.json`, `firs.json`, `airways.json`, `navaids.json`
   unchanged** -- no new layer files were added (waypoints.json already
   has a manifest entry), and no route/navaid data came from this PDF.

**Side effect worth flagging:** cross-checked every waypoint name
referenced by `airways.json`'s route point sequences against the now-188
entries in `waypoints.json`. This addition resolves **33 previously
dangling references** (route point names with no matching entry in
`waypoints.json` -- e.g. `ANHOA`, `HATIN`, `KAMSU`, `VIDAD`, `XONUS`, and
28 others) that existed since the 2026-07-06 `airways.json` rebuild, whose
own validation only checked names against *its own* build-time point
table, not against the final `waypoints.json` file. **11 references still
don't resolve** in `waypoints.json`: `CQ`/`RG`/`TSH`/`TRN`/`PTH`/`VTV` are
navaid short-idents already covered by `navaids.json` under those same
keys (a renderer that falls back to `navaids.json` for unresolved
`waypoints.json` names would find them; one that doesn't, won't -- worth
checking `geo_layers.js`'s lookup logic if this matters), and
`NOB`/`NAH`/`DAN`/`HUE`/`VIN` are city/place-name route endpoints (Noi
Bai, Ha Noi, Da Nang, Hue, Vinh) genuinely outside ENR 4.4's
significant-point-only scope -- not fixable from this source.

**Validation performed on this update:**
- All 6 `geo/*.json` files (including the two touched) still parse as
  valid JSON.
- No duplicate waypoint names across all 188 features (188 unique names).
- All 188 coordinates within valid lon/lat ranges, and within a loose
  Vietnam-FIR sanity box (lon 95-125, lat -2-26).
- Zero coordinate discrepancies between the 31 already-present
  significant points and their ENR 4.4 values.
- `sector_5a`'s ring still closes (first vertex == last vertex) and now
  has 11 vertices (was 9).
- Every `vertex_source` name referenced by any `sectors.json` polygon
  still resolves in `waypoints.json` (no dangling sector references
  introduced or pre-existing).
- Full Python test suite (`tests/*.py`) re-run after these changes:
  unaffected, as expected -- none of these files are read by the
  `astra/` engine code, only by the dashboard's static asset pipeline.

**Not done / out of scope of this update:** the 11 remaining dangling
`airways.json` references noted above; the D08-to-crossing-point
FIR-boundary jog in Sector 5a (still a straight-line stand-in, now just a
shorter one); re-verifying Sectors 3/4 (still chart-traced, per every
prior entry in this log) -- this update touched only what ENR 4.4's
content could directly fix.

---

## Update 2026-07-23 (later same day): `airways.json` expanded from 9 to 70 routes, `navaids.json` expanded from 10 to 28

**Source:** `VV-ENR-3_1-en.pdf` (ENR 3.1, "Lower ATS Routes") and
`VV-ENR-3_3-en.pdf` (ENR 3.3, "Area Navigation (RNAV) Routes"), both
supplied as pre-converted HTML tables inside markdown. These are the same
two AIP sections `airways.json`'s original 9 routes came from
(2026-07-06) -- this update is a full pass over the *rest* of both
tables, which had not been transcribed yet.

**Parsing approach:** these tables use HTML `rowspan`/`colspan` and bundle
several waypoints' name+coordinate text into a single header cell per
route (one cell can contain e.g. 8 concatenated `▲ NAME DDMMSSN
DDDMMSSE` fragments). A `BeautifulSoup`-based parser was written rather
than transcribing by hand, given the volume (70 routes across ~230 table
rows): it walks each table row, tracks which column-0 cell is a genuine
new cell versus a rowspan continuation (to avoid re-extracting a bundled
cell's content once per row it visually spans -- an early version of
this parser double-counted every 2-point route's waypoints for exactly
this reason, caught by a diff against the 9 already-trusted routes,
see Validation below), and extracts every `(name, lat, lon)` triple via
regex, in document order. Navaid-type points (`NAME NDB/DVOR/DME/VOR/DME
(IDENT)`) are distinguished from plain significant points at the same
step.

**Two source artifacts required manual correction, not general-purpose
regex handling:**
1. Two routes (**L644**, **W4**) have their designator glued directly
   onto the *previous* route's last coordinate with no separating
   whitespace in the source text (e.g. `...1063902EL644△ AN LOC...` --
   `L644`'s designator sits immediately after L637's closing `TSH`
   coordinate). A general word-boundary regex cannot detect a designator
   immediately following a digit/letter with no boundary, so these two
   were split by hand once identified (both share their starting point
   with the preceding route -- L644 starts at TSH, the same point L637
   ends at; W4 starts at CBI, the same point W3 ends at -- consistent
   with these being genuinely chained, connected routes, not a parsing
   artifact). This also matches waypoints.json's own pre-existing notes:
   `LEDUP`, `LOSON`, `BODOD`, `DUDIS` were already annotated "Route L644"
   from an earlier session, even though `L644` itself had never been
   added to `airways.json` until now.
2. Flight-level values are concatenated directly against the following
   minimum-altitude-in-meters value with no separator (e.g. `FL
   1252700 M` = FL125 + minimum altitude 2700m). A non-greedy regex
   requiring the FL figure to be 2-3 digits, followed by either a
   3-4 digit meters value + "M" or directly "Class:", resolves this
   correctly in every case checked -- validated by spot-checking several
   dozen output values against the source text by eye (e.g. confirming
   `FL 80900 M` -> FL80, not FL8).

**What changed:**

1. **61 new routes added to `airways.json`** (9 -> 70 total): every
   ENR 3.1 and ENR 3.3 route not already present. Each feature has
   `designator`, `waypoints` (ordered point-name list), `levels`
   (derived as `FL<upper>/FL<lower>` or `FL<upper>/FL<lowmin>-FL<lowmax>
   (varies by segment)` when a route's lower limit changes across its
   legs -- most do), `note`, and `source`. **`note` is deliberately
   thin for this batch** -- "Sector/ACC/COP/frequency detail not
   captured in this automated pass; see source for full context" (or
   the RNAV-spec-qualified equivalent for ENR 3.3 routes) -- because
   that detail lives in the *remarks* column, which this parser does not
   attempt to extract (unlike the original 9 routes' notes, which were
   transcribed by hand with real sector/ACC summaries). Getting the
   waypoint sequence and levels right, at this volume, was already a
   meaningful undertaking; fabricating or guessing sector/remarks
   content to make the note field look complete would be worse than
   leaving it honestly thin.
2. **Zero new points added to `waypoints.json`.** Every non-navaid
   point name referenced across both route tables (151 unique) was
   already present -- expected, since the 2026-07-23 (earlier) ENR 4.4
   update had already added the full significant-point table these
   routes draw from. Cross-checked coordinates for all 151: zero
   discrepancies.
3. **18 new navaids added to `navaids.json`** (10 -> 28 total): every
   navaid ident referenced in either route table that wasn't already
   there -- `AM, BQ, CB, CBI, CQ, CRA, DAN, DBN, HUE, MC, NAH, NOB, PCA,
   PQU, RG, VDO, VIN, VPH`. As with the 2026-07-06 navaids.json entries,
   `frequency`/`channel`/`hours_of_operation`/`elevation_m` are not
   available from ENR 3.1/3.3 (those come from ENR 4.1) -- set to
   `null` rather than guessed, with `source` noting the gap explicitly.
   **Four of these 18 (`CQ`, `RG`, `PCA`, `CRA`) were already present in
   `waypoints.json`** under the older `navaid_ndb`/`navaid_vordme`
   categories from a prior session (as `NDB_CQ`, `NDB_RG`, `PCA`, `CRA`)
   -- confirmed identical coordinates, left as-is (not removed), matching
   this project's established precedent of allowing the same physical
   point to appear in both the legacy `waypoints.json` categorization and
   the newer dedicated `navaids.json` layer (see the 2026-07-23 ENR 4.4
   entry's treatment of `OSIXA` for the same pattern) -- rather than
   picking one file to be authoritative and migrating the other. The
   pre-existing **BMT coordinate discrepancy** between ENR 4.1
   (`navaids.json`'s value) and ENR 3.1/3.3 (one arc-second east) noted
   in the 2026-07-06 entry recurred here as expected and was left
   exactly as previously decided: `navaids.json` keeps its ENR 4.1
   value; new route geometry built from this update resolves `BMT`
   against `navaids.json` (not the raw ENR 3.1/3.3 value it was parsed
   with), for internal consistency with the dedicated navaid layer.

**Side effect worth flagging:** this closes 6 of the 11 remaining
dangling `airways.json` references noted in the earlier 2026-07-23 (ENR
4.4) entry -- `CQ`, `RG`, `TSH` (already resolved), `TRN` (already
resolved), `PTH` (already resolved), `VTV` (already resolved) are now
genuinely covered by `navaids.json` rather than merely "coverable via a
renderer fallback." The other 5 (`NOB`, `NAH`, `DAN`, `HUE`, `VIN`) are
now **also** resolved -- they were flagged as "city/place-name route
endpoints genuinely outside ENR 4.4's scope," and this update's source
(ENR 3.1/3.3, not ENR 4.4) is exactly where they do belong, as full
navaids (Noi Bai DVOR/DME, Nam Ha DVOR/DME, Da Nang DVOR/DME, Phu Bai
DVOR/DME, Vinh DVOR/DME respectively). **Zero dangling `airways.json`
references remain.**

**Validation performed:**
- All 6 `geo/*.json` files parse as valid JSON.
- Cross-checked all 9 pre-existing routes' parsed output against their
  already-shipped `airways.json` entries: **exact match**, all 9 --
  this is what caught and confirmed the fix for the rowspan
  double-counting bug described above (an earlier parser version
  produced `['PLK','BOMPA','PLK','BOMPA']` for B202 instead of
  `['PLK','BOMPA']`; W7 came out as 9 points instead of 3).
- 70 unique route designators, no duplicates.
- 28 unique navaid idents, no duplicates.
- Zero dangling `airways.json` waypoint references (down from 11).
- Every new route's `LineString` coordinate count matches its
  `waypoints` list length (no missing/extra points from the resolver).
- No degenerate routes (no consecutive duplicate coordinates).
- All 28 navaid coordinates within the Vietnam-FIR sanity box.
- Full Python test suite re-run: unaffected, as expected (static
  dashboard assets only).

**Not done / out of scope of this update:** per-route sector/ACC/COP/
frequency detail in `note` (see above -- deliberately left thin rather
than guessed); `W18`'s `levels` (source has only one `FL` figure for
this route -- `FL 460900 MClass: A`, missing the expected second `FL`
entirely -- rendered as "not captured in source extract" rather than
guessing which number is upper vs lower); frequency/channel/hours/
elevation for the 18 new navaids (needs ENR 4.1, not supplied this
session).


