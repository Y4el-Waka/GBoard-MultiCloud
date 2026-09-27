# Phase 0.4: Real PRTG metrics acquisition

- Status: In progress
- Current increment: 0.4C validation procedure prepared; real run pending
- Real PRTG validation: Pending; no authorized instance was contacted
- `implementation_complete_with_fixtures: true`
- `validated_against_real_prtg: false`

## Objective

Phase 0.4 prepares a production-capable normalized metrics source without
changing the stable legacy application or the Phase 0.3 laboratory flow.
Phase 0.4A characterizes the original PRTG HTTP API and adds a safe read-only
discovery boundary. Phase 0.4B connects that boundary to the existing
normalized service/API path through an explicit real-source provider. This is
an implementation result against fixtures, not a production compatibility
claim.

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

## Phase 0.4B: real-source provider

```text
configured PRTG server + selected sensor
                 |
             PRTGClient
                 |
       PRTGSensorDiscovery
                 |
    RealHistoricalMetricsSource
      | metadata       | source CSV + timezone/aggregation
      +----------------+
                 |
           HistoryService
                 |
         PRTGMetricAdapter
                 |
 NormalizedHistoryResult / SensorMetricMetadata
                 |
       CoreView metrics API v2
```

`RealHistoricalMetricsSource` implements the same small provider protocol as
the synthetic source. It does not import the synthetic catalog and never
falls back to synthetic data. Discovery verifies the exact returned sensor ID
and fails closed as `unknown` or `unsupported` when evidence is insufficient.
History is requested only after a user selects one sensor.

The provider transports raw CSV and source metadata to `HistoryService`.
`PRTGMetricAdapter` remains the only component that maps aliases, parses
numeric values, converts units, normalizes UTC timestamps, and emits issues or
unknown channels. Accumulated traffic volume and counters are not converted to
bps.

### Activation

The default remains disabled. For an owned or explicitly authorized PRTG:

```dotenv
COREVIEW_METRICS_PROVIDER=prtg
COREVIEW_PRTG_SOURCE_TIMEZONE=America/Mexico_City
COREVIEW_PRTG_HISTORY_AVERAGE_SECONDS=300
COREVIEW_PRTG_HISTORY_MAX_REQUESTS_PER_MINUTE=5
COREVIEW_PRTG_DISCOVERY_CACHE_TTL_SECONDS=300
COREVIEW_PRTG_DISCOVERY_CACHE_MAX_ENTRIES=128
COREVIEW_PRTG_METRICS_EXPECTED_PROCESSES=1
PRTG_VERIFY_SSL=true
```

The selected server must be present in the normal ignored runtime
configuration with a stable `id`, HTTPS URL, username, and password or
passhash. Secrets stay outside version control. Application startup constructs
the provider but performs no network request.

### Points and range semantics

- `points=N` requests the real source window from `now - N * avg` through
  `now`. PRTG may return fewer points; CoreView keeps at most the latest `N`
  distinct source timestamps and never invents timestamps.
- `start/end` forwards the exact timezone-aware range after conversion to the
  configured PRTG source timezone. Samples outside the requested range are not
  presented as valid results.
- `avg` is source aggregation metadata, not a promise that every interval has
  a sample.

### Load controls and cache

- Historic calls use a per-`server_id`, thread-safe, sliding one-minute budget
  capped at five. Budget exhaustion returns the existing v2 `502` boundary
  with code `upstream_rate_limited`.
- Successful discovery is held in a bounded TTL/LRU cache keyed by
  `(server_id, sensor_id)`. Authentication, transport, invalid-response, and
  not-found errors are not cached.
- Both mechanisms are process-local. `prtg` mode therefore validates
  `COREVIEW_PRTG_METRICS_EXPECTED_PROCESSES=1`. Actual WSGI process count
  cannot be discovered reliably; the operator must deploy one application
  process until a shared coordinator exists.
- No background history scan and no normalized history cache were added.

### Error policy

The service keeps distinct categories for not found, unknown/unsupported,
authentication, rate budget, invalid upstream response, and transport. Flask
returns only allowlisted messages/codes; it never serializes credentials, raw
responses, authenticated URLs, or stack traces. A bad channel remains an issue
and does not destroy valid sibling channels.

### Fixture integration evidence

The test suite starts an ephemeral loopback HTTP server and exercises:

```text
fixture HTTP -> PRTGClient -> discovery -> real provider -> HistoryService
             -> PRTGMetricAdapter -> authenticated Flask API v2 -> JSON
```

Ping, CPU, memory, Traffic IN/OUT, partial SFP, localized decimal/timezone
input, identity isolation, unknown/unsupported sensors, metadata TTL, request
budget, auth failure, and provider composition are covered. The server binds
only to `127.0.0.1`, uses synthetic credentials, and is stopped by the test.

### Authorized manual smoke test

Never run this against infrastructure you do not own or lack explicit
authorization to query. Use a least-privilege read-only account and one
selected sensor. After setting the runtime configuration above:

```bash
.venv/bin/python scripts/prtg_metrics_smoke.py \
  --server-id <configured-stable-id> \
  --sensor-id <selected-prtg-sensor-id> \
  --expected-family ping \
  --points 3 \
  --confirm-read-only-authorized
```

The command refuses to run without the acknowledgement, checks the expected
family before requesting history, and prints only allowlisted structural
evidence. It aliases source columns and does not print values, names, URLs,
credentials, or raw PRTG payloads. A successful command deliberately leaves
`validated_against_real_prtg: false` and `compatibility_status: not_tested`;
only a reviewed matrix update may promote the tested version/sensor type.

## Phase 0.4C: compatibility validation preparation

The reproducible runbook, fixture baseline, empty real-compatibility matrix,
localization checklist, security checklist, evidence sanitization procedure,
parity checks, and declaration gate are maintained in
`docs/REAL_PRTG_VALIDATION.md`.

No owned or explicitly authorized PRTG instance was supplied for this
increment. No external server was contacted, all real rows remain
`not_tested`, and `validated_against_real_prtg` remains `false`.

The preparation audit found one misleading behavior in the Phase 0.4B smoke:
one successful selected-sensor request emitted a global real-validation flag.
The smoke now distinguishes successful execution from reviewed compatibility,
requires the operator's expected family, captures only one historical request,
and produces sanitized structural evidence for manual review.

## Remaining Phase 0.4 work

1. Obtain explicit authorization and run the opt-in smoke sequentially against
   selected sensors, following `docs/REAL_PRTG_VALIDATION.md`.
2. Validate installed-version captions, sensor type tokens, locale, DST,
   aggregation, and empty/error rows before promoting additional aliases.
3. Decide whether production needs a shared multi-process request coordinator
   and cache; do not raise worker count in `prtg` mode before that decision.
4. Resolve the legacy server-name request lookup and app/monitor enabled/type
   discrepancy in a separately compatible migration.

Counter-to-rate, normalized persistence, Redis/distributed cache, dashboard
redesign, cloud infrastructure, and legacy identity migration remain out of
scope.
