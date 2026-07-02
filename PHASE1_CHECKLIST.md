# Phase 1 Completion Checklist

**Status: COMPLETE — 130/130 verification checks pass.**

---

## Brief requirements → implementation

Phase 1 brief: _"Receive from BlueSky every simulation step: Aircraft ID,
Latitude, Longitude, Altitude, Ground speed, Heading, Vertical speed,
Aircraft type, Timestamp. Store the traffic state."_

### R1 — Aircraft ID
| Check | File | Detail |
|---|---|---|
| ✅ Stored as `callsign: str` on `AircraftState` | `interface/traffic_state.py` | Frozen field |
| ✅ Always upper-cased on ingestion | `interface/bluesky_connector.py` | `str.upper()` in `_on_acdata` |
| ✅ Case-insensitive lookup via `TrafficSnapshot.get()` | `interface/traffic_state.py` | `.upper()` before dict lookup |

### R2 — Latitude
| Check | File | Detail |
|---|---|---|
| ✅ `lat: float` decimal degrees WGS-84 | `interface/traffic_state.py` | |
| ✅ Passed through unchanged from ACDATA | `interface/bluesky_connector.py` | Already in degrees |

### R3 — Longitude
| Check | File | Detail |
|---|---|---|
| ✅ `lon: float` decimal degrees WGS-84 | `interface/traffic_state.py` | |
| ✅ Passed through unchanged from ACDATA | `interface/bluesky_connector.py` | Already in degrees |

### R4 — Altitude
| Check | File | Detail |
|---|---|---|
| ✅ `altitude_ft: float` feet AMSL | `interface/traffic_state.py` | |
| ✅ Converted from BlueSky metres via `meters_to_feet()` | `interface/bluesky_connector.py` | `alt` field in ACDATA |
| ✅ Conversion function unit-tested | `astra/utils/units.py` | V6 test suite |

### R5 — Ground speed
| Check | File | Detail |
|---|---|---|
| ✅ `ground_speed_kt: float` knots | `interface/traffic_state.py` | |
| ✅ Converted from BlueSky `gs` (m/s) via `mps_to_knots()` | `interface/bluesky_connector.py` | |

### R6 — Heading
| Check | File | Detail |
|---|---|---|
| ✅ `heading_deg: float` true degrees 0–360 | `interface/traffic_state.py` | |
| ✅ Sourced from BlueSky `trk` (track angle, already degrees) | `interface/bluesky_connector.py` | `trk ≈ hdg` at zero wind — documented simplification |

### R7 — Vertical speed
| Check | File | Detail |
|---|---|---|
| ✅ `vertical_speed_fpm: float` feet per minute (+ve = climb) | `interface/traffic_state.py` | |
| ✅ Converted from BlueSky `vs` (m/s) via `mps_to_fpm()` | `interface/bluesky_connector.py` | |

### R8 — Aircraft type
| Check | File | Detail |
|---|---|---|
| ✅ `aircraft_type: str` ICAO designator (e.g. `"A320"`) | `interface/traffic_state.py` | |
| ✅ Known gap: ACDATA does not carry type — documented | `interface/type_registry.py` | Module docstring |
| ✅ Workaround: `TypeRegistry` caches type at `create_aircraft()` time | `interface/type_registry.py` | |
| ✅ Falls back to `"UNKNOWN"` for aircraft not created via ASTRA | `interface/type_registry.py` | `UNKNOWN_TYPE` sentinel |
| ✅ `TypeRegistry` is thread-safe (Lock) | `interface/type_registry.py` | |

### R9 — Timestamp
| Check | File | Detail |
|---|---|---|
| ✅ `timestamp_s: float` simulation seconds (`simt`) | `interface/traffic_state.py` | |
| ✅ Sourced from BlueSky `simt` ACDATA field | `interface/bluesky_connector.py` | |
| ✅ `TrafficSnapshot.timestamp_s` mirrors the same value | `interface/traffic_state.py` | |

### R10 — Store the traffic state
| Check | File | Detail |
|---|---|---|
| ✅ Bounded deque `maxlen = ASTRAConfig.history_length` (default 3600) | `interface/state_reader.py` | |
| ✅ Duplicate-timestamp guard (no double entries per simulation tick) | `interface/state_reader.py` | `poll()` compares `timestamp_s` |
| ✅ `current()` → latest snapshot | `interface/state_reader.py` | |
| ✅ `history(last_n)` → ordered slice | `interface/state_reader.py` | |
| ✅ 1-hour rolling history at 1 Hz (3600 entries × 1 s) | `utils/config.py` | `ASTRAConfig.history_length=3600` |

---

## Design decisions (beyond the brief)

| Decision | Rationale | Location |
|---|---|---|
| `AircraftState` frozen dataclass | Historical fact — must not be mutated after observation | `traffic_state.py` |
| `ConnectorProtocol` via `typing.Protocol` | Avoids MRO collision with BlueSky's `Client` inheritance | `connector_base.py` |
| `StateReader` uses dependency injection | Enables offline development and reproducible unit tests | `state_reader.py` |
| `MockConnector` in Phase 1 | Phases 2–7 need a traffic source without BlueSky | `mock_connector.py` |
| `geodesy.py` placed in Phase 1 (`utils/`) | `MockConnector` needs `move_position()`; placing in `utils` prevents circular deps | `utils/geodesy.py` |
| Units converted at adapter boundary | Every downstream module works in ATM units; BlueSky SI never escapes `bluesky_connector.py` | `units.py`, `bluesky_connector.py` |
| All config in one frozen dataclass | Single source of truth; `__post_init__` validates cross-field constraints at startup | `utils/config.py` |
| `bluesky` import confined to `bluesky_connector.py` | Enforced by V3 dependency check; swapping simulators = one file change | Architecture rule |

---

## File inventory

```
Phase 1 source files
──────────────────────────────────────────────────────────────
astra/utils/config.py             ASTRAConfig frozen dataclass + DEFAULT_CONFIG
astra/utils/units.py              meters_to_feet, mps_to_knots, mps_to_fpm, nm_to_meters, …
astra/utils/geodesy.py            haversine_distance_nm, bearing_deg, move_position
astra/utils/logger.py             get_logger() — consistent format across all modules

astra/interface/traffic_state.py   AircraftState (frozen), TrafficSnapshot
astra/interface/type_registry.py   TypeRegistry — thread-safe callsign→type cache
astra/interface/connector_base.py  ConnectorProtocol (typing.Protocol, runtime_checkable)
astra/interface/bluesky_connector.py  BlueSkyConnector — live BlueSky ZMQ adapter
astra/interface/mock_connector.py    MockConnector — offline dead-reckoning simulator
astra/interface/state_reader.py      StateReader — history buffer + for_bluesky()/for_mock()

main.py                           Entry point (python main.py [--mock])
demo_phase1.py                    Offline demonstration — 5 aircraft, full snapshot print
requirements.txt                  bluesky-simulator (pip install -r requirements.txt)
scenarios/phase1_demo.scn         BlueSky scenario for live manual testing
docs/architecture.md              Mermaid system + dep + sequence diagrams
Developer_Handover.md             Full developer guide
PHASE1_CHECKLIST.md               This file
──────────────────────────────────────────────────────────────
Later-phase placeholder packages (docstring only, no logic)
astra/trajectory/__init__.py      Phase 2
astra/hotspot/__init__.py         Phase 3
astra/complexity/__init__.py      Phase 4
astra/prediction/__init__.py      Phase 5
astra/resolution/__init__.py      Phase 6
astra/dashboard/__init__.py       Phase 7
```

---

## Verification results

All checks run against the installed `bluesky-simulator` package and the
current codebase. Run in four focused scripts to avoid shell timeouts.

### V1 — Syntax / compile  ✅  20/20
All `.py` files parse without `SyntaxError` (verified with `ast.parse`).

### V2 — Imports  ✅  11/11
Every module imports cleanly in isolation:
`astra.utils.{config,units,geodesy,logger}`,
`astra.interface.{traffic_state,type_registry,connector_base,mock_connector,state_reader,bluesky_connector}`,
`main`.

### V3 — Dependency graph  ✅  7/7
```
Expected import edges (no others):
  bluesky_connector  →  traffic_state, type_registry, utils.{config,logger,units}
  connector_base     →  traffic_state
  mock_connector     →  traffic_state, utils.{geodesy,logger}
  state_reader       →  bluesky_connector, connector_base, mock_connector,
                         traffic_state, type_registry, utils.{config,logger}

Rules verified:
  ✅ No circular imports (DFS traversal)
  ✅ utils.* imports nothing from astra.interface
  ✅ bluesky.* imported only in bluesky_connector.py
```

### V4 — BlueSky compatibility  ✅  10/10
Verified against installed `bluesky-simulator` source (no server required):
- `Client.connect(hostname, recv_port, send_port)` — all three keyword args confirmed
- `Node.act_id` initialised to `None` — `has_active_node()` logic correct
- `Store.update()` uses `setattr(self, key, item)` — `getattr(data, "lat")` pattern correct
- ACDATA fields `simt, id, lat, lon, alt, trk, gs, vs` all confirmed present in `send_aircraft_data()`

### V5a — MockConnector functional  ✅  39/39
Protocol compliance · lifecycle (connect/poll/has_active_node) ·
`create_aircraft()` · AircraftState frozen · case-insensitive get() ·
1-tick position propagation (distance within 0.5 NM precision) ·
VS/SPD/HDG/ALT commands · CRE/DEL via send_command() · HOLD/OP/PAUSE ·
unknown command silently ignored · empty command silently ignored ·
`sim_step_s` validation · helper methods (`aircraft_callsigns`,
`set_running`, `remove_aircraft`)

### V5b — StateReader functional  ✅  17/17
`for_mock()` factory · pre-connect state · connect() · first poll() ·
aircraft in snapshot · de-duplication (HOLD → same timestamp → None) ·
history cap · oldest→newest ordering · `history(last_n)` · `current()` ·
`send_command()` pass-through

### V5c — BlueSkyConnector  ✅  16/16
Protocol compliance · pre-connect state · `connect_to_simulator()` alias ·
`_on_acdata()` decoding: altitude m→ft, gs m/s→kt, trk→heading, vs m/s→fpm ·
TypeRegistry lookup (registered = type; unregistered = UNKNOWN) ·
`TrafficSnapshot` methods (callsigns, as_list, len, iter) ·
`ASTRAConfig` validation (bad horizon, negative poll_interval_s)

### V6 — Geodesy unit tests  ✅  10/10
Haversine: equator 1° ≈ 60 NM · self-distance = 0 · symmetry ·
Bearing: east=90°, north=0°, south=180° ·
move_position: 60 NM east, 60 NM north · round-trip error < 0.001 NM ·
heading 360° ≡ 0°

---

## Manual verification steps (requires BlueSky)

These are run by the developer when BlueSky is available. They verify the
live ZMQ path that cannot be tested without a running server.

**MV-1 BlueSky starts headless**
```bash
python -m bluesky --headless
```
Expected: prints `BlueSky Open ATM simulator` and `Starting server`.

**MV-2 ASTRA connects**
```bash
python main.py
```
Expected: `Waiting for a BlueSky simulation node…`, then within ~3 s
`BlueSky node active. Polling every 1.0s.`

**MV-3 Traffic appears (scenario file)**
Load `IC scenarios/phase1_demo.scn` in BlueSky.
Expected: 4 aircraft (KL204, BAW123, DLH456, EZY789) appear in ASTRA
output each poll. Types show as `UNKNOWN` (created by .scn, not ASTRA).

**MV-4 ASTRA-created aircraft carry correct type**
Instead of the scenario file, from ASTRA code or console:
```python
reader.create_aircraft("KL204","A320",52.30,4.80,90,30000,250)
reader.send_command("OP")
```
Expected: KL204 appears with `type=A320` (TypeRegistry populated).

**MV-5 Clean shutdown**
Press `Ctrl+C`. Expected: `Stopped by user.` log line, clean exit.

---

## Demo output reference

Running `python demo_phase1.py` produces (timestamps may vary):

```
Polling 5 times (each tick = 60 simulated seconds) …

  Tick 1: simt=60s, 5 aircraft
  Tick 2: simt=120s, 5 aircraft
  Tick 3: simt=180s, 5 aircraft
  Tick 4: simt=240s, 5 aircraft
  Tick 5: simt=300s, 5 aircraft

======================================================================
  ASTRA Phase 1 Demo — TrafficSnapshot at simt=300s
======================================================================
  Callsign   Type        Lat       Lon   Alt (ft)  GS (kt)    Hdg  VS (fpm)
  ------------------------------------------------------------------------
  BAW436     B738    47.7992    7.8702      33000    450.0  270.0       0.0
  DLH721     A321    47.5477    8.3000      35000    470.0  180.0       0.0
  KL204      A320    47.4992    8.5553      33000    465.0   90.0       0.0
  SWR101     A319    47.8107    8.0000      31000    440.0    0.0       0.0
  UAE512     B77W    48.0805    8.6172      37000    490.0   45.0       0.0
  ------------------------------------------------------------------------
  Total: 5 aircraft   History depth: 5 snapshot(s)
======================================================================

  Inter-aircraft horizontal separations (NM):
  BAW436-SWR101:    5.28 NM  (< 15 NM — potential hotspot candidate)
  DLH721-KL204:    10.75 NM  (< 15 NM — potential hotspot candidate)
```

These two pairs (BAW436-SWR101 at 5.28 NM and DLH721-KL204 at 10.75 NM)
are below the 15 NM DBSCAN threshold and will form hotspot clusters when
Phase 3 is implemented.
