# Phase 0.4: Real PRTG metrics acquisition

- Status: In progress
- Current increment: 0.4A complete against sanitized fixtures
- Real PRTG validation: Pending; no authorized instance was contacted

## Objective

Phase 0.4 prepares a production-capable normalized metrics source without
changing the stable legacy application or the Phase 0.3 laboratory flow.
Phase 0.4A characterizes the original PRTG HTTP API and adds a safe read-only
discovery boundary. It does not implement `RealMetricsProvider`.

## Phase 0.4A architecture

```text
configured server (stable id) + selected sensor id
                         |
                    PRTGClient
          +--------------+----------------+
          |                               |
 table.json / sensors             table.csv / channels
          |                               |
          +-------- PRTGSensorDiscovery --+
                             |
        metadata + source channels + classification
                + historic request metadata

explicit selected-sensor request only
                         |
              historicdata.csv (source)
                         |
         structural characterization now
         PRTGMetricAdapter in Phase 0.4B
```

`prtg_discovery.py` has no Flask, requests, SQLite, frontend, or synthetic
catalog dependency. HTTP remains exclusively owned by `PRTGClient`.

## PRTG operations characterized

### Sensor metadata

`GET /api/table.json` with:

- `content=sensors`;
- `filter_objid=<sensor_id>`;
- `count=1`;
- columns for object/parent IDs, probe, group, device, sensor, source type,
  tags, interval, status, last value, last check, and declared channel count.

The configured CoreView server `id` is combined with the returned PRTG sensor
`objid`. `group` and other names remain mutable source metadata.

### Channel inventory

`GET /api/table.csv` with:

- `content=channels`;
- `id=<sensor_id>`;
- `count=500`;
- object/name/last-value columns.

CSV is intentional because distinct PRTG captions can retain volume and speed
representations that are ambiguous as repeated JSON property names. Channel
IDs are retained when a response exposes them; they are optional because their
availability is not yet validated across PRTG versions and sensor types.

### Historical source data

`GET /api/historicdata.csv` with:

- `id=<sensor_id>`;
- explicit `sdate` and `edate`;
- explicit `avg`;
- `usecaption=1`.

This call is never part of inventory discovery. A caller must explicitly
select one sensor and range. Paessler documents a limit of five historic-data
requests per minute, so future production wiring needs request budgeting and a
small metadata/history cache before bulk usage. Phase 0.4A adds neither cache
nor background scan.

Paessler's official API v2 is a separate product API under `/api/v2`; it is not
CoreView's experimental `/api/v2/metrics/history`. It is not used in this
increment.

## Discovery metadata contract

`PRTGSensorDiscoveryResult` contains:

- `sensor`: compound identity, PRTG name/type, device ID/name, probe/group
  labels, tags, interval, status, last source value/check, channel count;
- `channels`: source channel ID when present, source name, possible canonical
  key, applicable sensor family, all observed value forms, source/canonical
  units, semantic kind, compatibility, and issues;
- `classification`: `ping`, `cpu`, `memory`, `traffic`, `sfp`, `unsupported`,
  or `unknown`, with evidence and reason;
- `historic`: selected sensor ID plus explicit range/timezone requirements.

Discovery values remain source strings. Numeric parsing and conversion belong
to `PRTGMetricAdapter` when the production provider is implemented.

## Classification policy

Classification is fail-closed and never uses the visible sensor name.

1. Normalize the explicit PRTG source-type token.
2. Resolve source channel aliases through the existing adapter mapping.
3. Require at least one unit-compatible channel signature for the expected
   family (RX plus TX for generic SFP evidence).
4. Accept a known type only when its signature agrees.
5. Accept a generic type only when the compatible channel evidence identifies
   exactly one family.
6. Return `unknown` for missing, conflicting, or insufficient evidence.
7. Return `unsupported` only for an explicit known out-of-scope source type.

The current type registry is fixture-backed and deliberately small. It is not
a claim that every PRTG Ping/CPU/memory/traffic/optical sensor type is covered.
Only `ping`, `snmpcpu`, `snmpmemory`, and `snmptraffic` are currently promoted
as explicit type mappings. WMI/Windows CPU and memory, NetFlow, packet sniffer,
and vendor-specific optical type aliases remain candidates pending sanitized
evidence; compatible unique channel signatures may still classify a generic
type without promoting its token globally.

## Channel semantics and units

The discovery layer records rather than converts observations:

| Source observation | Discovery semantic | Canonical compatibility |
|---|---|---|
| milliseconds | gauge | `latency_ms` when alias agrees |
| percent | gauge/calculated | percentage channel when alias agrees |
| bytes/KByte/MByte/GByte | gauge for memory; volume for traffic | memory bytes compatible; traffic volume not bps |
| bit/s, Kbit/s, Mbit/s, Gbit/s | rate | traffic bps compatible |
| dBm | gauge | optical power compatible |
| °C | gauge | temperature compatible |
| V | gauge | voltage compatible |
| mA | gauge | bias current compatible |

Headers labelled speed are rates; headers labelled volume are accumulated
volume; counter-labelled values remain counters. Unknown units and incompatible
family/unit combinations remain explicit issues. No value is changed to zero,
and no counter/volume is converted into a rate.

## Historic CSV characterization

Sanitized fixtures cover Ping, CPU, memory, traffic, and SFP histories with:

- semicolon separators;
- `Date Time` timestamps;
- naive source timestamps and explicit UTC `Z` timestamps;
- channel captions and a Coverage column;
- multiple rows, formatted units, and an empty value.

`characterize_historic_csv()` reports delimiter, headers, timestamp header,
channel/raw headers, observed source units, row count, empty-value count,
invalid timestamps, timestamp mode, and requested aggregation. It does not
normalize samples. Naive timestamps require an explicit source timezone before
adapter integration.

Pending real validation includes delimiter/decimal locale interactions,
localized headers, daylight-saving transitions, aggregation behavior, raw
columns, error rows, empty intervals, channel order, and maximum supported
range.

## Evidence matrix

| Behavior | Official documentation | Sanitized fixtures | Real PRTG |
|---|---:|---:|---:|
| Original API table endpoint and `content`/`columns` queries | Confirmed | Exercised | Pending |
| Object filtering and pagination/count controls | Confirmed | Exercised | Pending |
| `content=sensors` metadata shape used by CoreView | Partially confirmed | Exercised | Pending |
| `content=channels` inventory and last values | Confirmed conceptually | Exercised | Pending |
| Optional channel IDs and exact CSV captions | Not universal | Exercised | Pending |
| Distinct traffic volume and speed observations | Documented/source-compatible | Exercised | Pending |
| `historicdata.csv` range/average/caption parameters | Confirmed | Exercised | Pending |
| Historic request limit of five per minute | Confirmed | Not applicable | Pending |
| Type-to-family registry | Not complete | Exercised | Pending |
| Locale/timezone behavior | Installation-dependent | Mixed naive/UTC fixtures | Pending |
| Paessler API v2 separate from CoreView v2 | Confirmed | Not used | Pending |

Official references used for characterization:

- Paessler manual: `https://www.paessler.com/manuals/prtg/http_api`;
- Paessler manual: `https://www.paessler.com/manuals/prtg/multiple_object_property_or_status`;
- Paessler manual: `https://www.paessler.com/manuals/prtg/historic_data`;
- Paessler API v2 overview:
  `https://www.paessler.com/support/prtg/api/v2/overview/index.html`.

No capture in this repository is represented as output from a real PRTG
instance. `tests/fixtures/prtg_discovery/manifest.json` records that provenance
explicitly.

## Security and load controls

- The discovery API exposes only read-only table and history operations.
- Pause/resume and every state-changing endpoint are outside this boundary.
- TLS verification remains whatever the configured server requires; discovery
  never disables it.
- Redirects are rejected for sensor, channel, and on-demand history requests.
- Only configured server mappings reach `PRTGClient`; discovery does not accept
  an arbitrary destination URL.
- Logs contain stable server/sensor IDs and exception types, never credentials,
  authenticated query strings, or upstream payloads.
- Inventory is one selected sensor (two requests), not all sensors.
- History is one explicit selected sensor/range and is never requested during
  discovery.

## Compatibility boundary

Phase 0.4A is additive. It does not wire discovery into `app.py`,
`history_service.py`, `monitor.py`, the public APIs, dashboard, SQLite,
synthetic simulators, MF, or sensor controls. Existing PRTG client calls retain
their redirect behavior and public signatures; only the new discovery methods
force redirect rejection.

## Planned next increment: Phase 0.4B

Build an opt-in `RealHistoricalMetricsSource` only after an authorized capture
matrix validates the exact installed PRTG versions and source types. Phase 0.4B
should:

1. capture and sanitize one selected sensor per supported family;
2. promote only confirmed source type/channel aliases into mappings;
3. provide explicit source timezone and locale policy;
4. feed source histories into `PRTGMetricAdapter` without duplicating parsing;
5. enforce per-server history request budgets and bounded metadata caching;
6. keep unknown sensors/channels observable and unsupported by default;
7. remain opt-in beside the synthetic provider until parity and security review
   are complete.

Counter-to-rate, normalized persistence, distributed cache, dashboard redesign,
and cloud deployment remain out of scope.
