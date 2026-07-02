# ASTRA Prototype — System Architecture

## 1. High-Level Data Flow

```mermaid
flowchart TD
    BS["🛩  BlueSky Simulator\n(External process — python -m bluesky --headless)\nPublishes ACDATA at 5 Hz over ZeroMQ"]

    subgraph ASTRA["ASTRA Python Application  (this repository)"]
        direction TB

        subgraph IF["astra/interface  ── Phase 1 COMPLETE"]
            CB["ConnectorProtocol\n(typing.Protocol)"]
            BSC["BlueSkyConnector\n· subclasses bluesky.network.client.Client\n· subscribes to ACDATA topic\n· converts SI units → ATM units\n· maintains TypeRegistry for aircraft type"]
            MC["MockConnector\n· pure-Python, no BlueSky needed\n· dead-reckoning propagation\n· parses CRE/DEL/SPD/ALT/HDG/VS/OP/HOLD"]
            TR["TypeRegistry\n· thread-safe callsign→type cache\n· workaround for ACDATA gap"]
            SR["StateReader\n· accepts any ConnectorProtocol\n· bounded deque history (configurable)\n· duplicate-timestamp dedup\n· factory methods: for_bluesky() / for_mock()"]
            TS["TrafficSnapshot\n· timestamp_s (simt)\n· Dict[callsign → AircraftState]\n· as_list() / get() / callsigns()"]
            AS["AircraftState  (frozen dataclass)\n· callsign, lat, lon\n· altitude_ft, ground_speed_kt\n· heading_deg, vertical_speed_fpm\n· aircraft_type, timestamp_s"]
        end

        subgraph TP["astra/trajectory  ── Phase 2 TODO"]
            PRED["TrajectoryPredictor\n· kinematic dead-reckoning\n· horizons: 5/10/20/30/60 min\n· consumes TrafficSnapshot\n· produces PredictedSnapshot list"]
        end

        subgraph HS["astra/hotspot  ── Phase 3 TODO"]
            DBSCAN["HotspotDetector\n· DBSCAN (haversine metric)\n· ε = 15 NM horizontal\n· vertical gate = 1 000 ft\n· cluster tracking across time steps"]
        end

        subgraph CM["astra/complexity  ── Phase 4 TODO"]
            CX["ComplexityAssessor\n· density · MTCA conflicts\n· heading diversity\n· altitude diversity\n· type mixture\n· → score 0–100 (modular, extensible)"]
        end

        subgraph PD["astra/prediction  ── Phase 5 TODO"]
            HP["HotspotPredictor\n· start/end time estimation\n· confidence scoring\n· priority ranking\n· 60-minute lookahead"]
        end

        subgraph RS["astra/resolution  ── Phase 6 TODO"]
            RES["ResolutionEngine\n· candidate generation\n  (speed / FL / direct-to / heading)\n· multi-objective scoring\n  (complexity Δ · conflict Δ · fuel · deviation)\n· ranked solution list"]
        end

        subgraph DB["astra/dashboard  ── Phase 7 TODO"]
            DASH["Dashboard\n· live traffic map\n· predicted trajectories\n· hotspot heatmap\n· hotspot table + timeline\n· AI resolution suggestions\n· ghost trajectories"]
        end
    end

    BS -- "ZeroMQ ACDATA (Store obj)\nsimt·id·lat·lon·alt·gs·trk·vs" --> BSC
    MC -. "offline / testing" .-> SR
    BSC --> SR
    CB -. "Protocol satisfied by" .-> BSC
    CB -. "Protocol satisfied by" .-> MC
    TR --> BSC
    SR --> TS
    TS --> AS
    SR --> TP
    PRED --> HS
    DBSCAN --> CM
    CX --> PD
    HP --> RS
    RES --> DB
    DB -- "Clearances\n(SPD / ALT / DCT)" --> SR
```

---

## 2. Package Dependency Graph

```mermaid
graph LR
    subgraph utils["astra/utils  (zero internal deps)"]
        C[config.py]
        U[units.py]
        G[geodesy.py]
        L[logger.py]
    end

    subgraph interface["astra/interface"]
        TS2[traffic_state.py]
        TR2[type_registry.py]
        CB2[connector_base.py]
        BSC2[bluesky_connector.py]
        MC2[mock_connector.py]
        SR2[state_reader.py]
    end

    C --> SR2
    U --> BSC2
    G --> MC2
    L --> BSC2
    L --> MC2
    L --> SR2

    TS2 --> CB2
    TS2 --> BSC2
    TS2 --> MC2
    TS2 --> SR2

    TR2 --> BSC2
    TR2 --> SR2

    CB2 --> SR2
    BSC2 --> SR2
    MC2 --> SR2
```

Rules enforced in CI (V3):
- `utils` never imports from `interface` or any later phase.
- `bluesky` is imported **only** in `bluesky_connector.py`.
- No circular imports (verified by DFS).

---

## 3. Poll-Cycle Sequence

```mermaid
sequenceDiagram
    participant ML as main.py loop
    participant SR as StateReader
    participant CN as Connector<br/>(BlueSky or Mock)
    participant BS as BlueSky process<br/>(live only)

    ML->>SR: poll()
    SR->>CN: poll()
    CN-->>BS: update() [live] / propagate positions [mock]
    BS-->>CN: ACDATA Store (if new tick)
    CN-->>SR: (internal snapshot updated)
    SR->>CN: latest_snapshot()
    CN-->>SR: TrafficSnapshot | None
    SR->>SR: dedup check (timestamp_s)
    SR-->>ML: TrafficSnapshot (new) | None (no change)
    ML->>ML: downstream pipeline (Phase 2–7)
```

---

## 4. ConnectorProtocol

Both concrete connectors satisfy this Protocol via **structural subtyping**
(no explicit inheritance — avoids MRO collision with BlueSky's `Client`):

```
ConnectorProtocol
├── connect()                     → None
├── poll()                        → None
├── latest_snapshot()             → Optional[TrafficSnapshot]
├── has_active_node()             → bool
├── send_command(text: str)       → None
└── create_aircraft(cs,type,lat,lon,hdg,alt,spd) → None
```

---

## 5. Unit Conventions

| Domain       | Unit used throughout ASTRA | BlueSky internal | Conversion |
|---|---|---|---|
| Altitude     | feet (ft)                  | metres (m)       | `meters_to_feet()` |
| Ground speed | knots (kt)                 | m/s              | `mps_to_knots()` |
| Vertical speed | feet/minute (fpm)        | m/s              | `mps_to_fpm()` |
| Distance     | nautical miles (NM)        | metres (m)       | `nm_to_meters()` |
| Heading      | degrees true               | degrees true     | (unchanged) |
| Position     | decimal degrees WGS-84     | decimal degrees  | (unchanged) |
| Time         | simulation seconds (simt)  | simulation seconds | (unchanged) |

All conversions happen **once**, at the `_on_acdata()` boundary in
`bluesky_connector.py`. Every module above that layer works exclusively in
ATM units.
