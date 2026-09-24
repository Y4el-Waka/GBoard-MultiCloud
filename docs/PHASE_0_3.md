# Phase 0.3: Metrics Engine

## Global objective

Phase 0.3 will introduce a provider-neutral Metrics Engine capable of
representing multiple numeric channels per sensor with stable identity,
canonical units, explicit semantics, and timezone-aware samples.

The engine must eventually support at least:

- Ping latency, packet loss, and availability;
- CPU utilization;
- memory utilization and used bytes;
- inbound/outbound traffic and link utilization;
- SFP RX/TX power, temperature, voltage, and bias current;
- future disk, jitter, errors/discards, HTTP, temperature, voltage, current, and
  custom SNMP channels.

The migration is incremental. Existing snapshot, API, historical, MF, control,
and frontend contracts remain compatibility surfaces until explicit parity and
migration work is complete.

## Proposed architecture

```text
PRTG-compatible API
        |
   PRTGClient                       legacy path remains active
        |                                      |
PRTG channel adapter                    current collector/history
        |
canonical SensorIdentity + MetricChannelDefinition + MetricSample
        |
Metrics Engine processing/storage boundary
        |
compatibility adapters + future metrics API
        |
current UI contract / future multichannel UI
```

Responsibility boundaries:

- `PRTGClient` transports source responses and remains unaware of channels.
- Provider adapters map source names, units, values, and quality to canonical
  definitions.
- The `metrics` domain validates provider-neutral identity and samples.
- Processing layers will later own counter deltas, rates, aggregation,
  resampling, and retention.
- Compatibility adapters preserve current snapshot and Ping history responses.
- Flask and frontend remain presentation layers rather than metric parsers.

## Phase 0.3A: Domain model and synthetic fixtures

Implemented scope:

- stable `SensorIdentity(server_id, sensor_id)` with optional device context;
- canonical `MetricChannelDefinition`;
- numeric, finite, timezone-aware `MetricSample`, normalized to UTC;
- small gauge/counter/rate/state semantic vocabulary;
- channel-set definitions for Ping, CPU, memory, traffic, and SFP;
- canonical base units without display formatting;
- deterministic synthetic multichannel time-series fixtures;
- tests for validation, extensibility, channel subsets, units, and framework
  independence;
- no integration with production application modules.

Explicitly unchanged in 0.3A:

- collector snapshot and `lastvalue`;
- historic CSV parsing and datasets;
- Flask APIs and response payloads;
- SQLite schema and persisted state;
- `datos_simulados.py`;
- templates, static assets, and charts.

## Phase 0.3B: PRTG metrics adapter

Implemented scope:

- additive `PRTGMetricAdapter` above the Phase 0.3A domain;
- centralized PRTG source-channel aliases and source-to-canonical mappings;
- explicit normalization for latency, percentages, byte volumes, traffic
  rates, optical power, temperature, voltage, and bias current;
- independent inputs for PRTG-shaped sensor records and current historic CSV;
- explicit source timezone requirement for naive timestamps and UTC output;
- stable identity from configured `server_id` plus PRTG `objid`;
- tolerant per-channel results with normalized samples, unknown channels, and
  typed issues;
- partial-channel support and selected source traceability metadata;
- deterministic synthetic PRTG fixtures for Ping, CPU, memory, traffic, and
  SFP;
- no production consumer integration.

The resulting boundary is:

```text
PRTG table record / channel observations / historic CSV
                         |
                 PRTGMetricAdapter
                         |
       AdapterResult(samples, unknown_channels, issues)
                         |
                  MetricSample[]
```

`PRTGClient`, the current simulator, collector, `HistoryService`, snapshot,
SQLite, APIs, and frontend remain on the legacy path. `Coverage` is not inferred
as availability, and accumulated traffic counters are reported but not converted
to rates.

## Phase 0.3C: Synthetic multichannel PRTG simulator

Implemented scope:

- two additive opt-in source routes, `multichannel.json` and
  `multichannel_history.json`;
- deterministic family assignment for Ping, CPU, memory, traffic, and SFP while
  retaining the existing 1,000 legacy sensors;
- per-sensor profiles derived from `SIMULATOR_SEED` without consuming the legacy
  random sequence;
- aware UTC logical clock with explicit, sleep-free ticks;
- continuous family-specific value evolution and ordered historical series;
- PRTG-like channel labels and formatted source units rather than canonical
  metric keys;
- deterministic full and partial SFP channel sets;
- explicit normal, trend, partial, unknown-channel, and invalid-source
  scenarios;
- simulator-to-`PRTGMetricAdapter` integration tests without production
  consumer integration.

The legacy devices/sensors tables, Ping CSV, healthcheck, pause/resume,
collector snapshot, SQLite, APIs, and frontend remain unchanged. Logical ticks
only affect the new opt-in surfaces.

```text
legacy simulator routes ----------------> current collector/history/UI

synthetic profiles + logical tick
               |
     multichannel JSON/history
               |
     PRTGMetricAdapter (tests only)
               |
          MetricSample[]
```

## Phase 0.3D: Normalized history integration

Implemented scope:

- additive HistoryService.get_normalized_history() API;
- minimal injected HistoricalMetricsSource provider boundary;
- real use of PRTGMetricAdapter across multiple source timestamps;
- immutable NormalizedHistoryResult with stable identity, samples, available
  channel definitions, unknown channels, issues, and observed bounds;
- on-demand grouping by canonical channel_key;
- chronological ordering and deterministic last-source-wins deduplication for
  equal timestamp/channel pairs;
- aware UTC query boundaries and samples;
- semantic Ping parity tests against the unchanged legacy parser;
- normalized CPU, memory, traffic, and SFP integration using the synthetic
  multichannel source;
- no normalized cache in this increment.

The dual path is now:

~~~text
PRTG CSV -> legacy parser -> legacy cache/datasets -> /api/history -> current UI

HistoricalMetricsSource
          |
PRTGMetricAdapter
          |
NormalizedHistoryResult
  samples / channels / issues / unknowns
          |
 direct backend use and tests only
~~~

The legacy parser, cache, public route, frontend, collector, snapshot, and
SQLite remain unchanged. Availability is retained by the normalized path but
is not inferred from legacy Coverage.

## Phase 0.3E: Versioned normalized metrics API

Implemented scope:

- additive authenticated `GET /api/v2/metrics/history` endpoint;
- explicit points mode (1-1000) and aware ISO-8601 range mode, without implicit
  query defaults;
- stable status policy for validation, missing resources, upstream failures,
  and unexpected failures;
- standalone `metrics.serialization` boundary from normalized domain results
  to versioned JSON-compatible primitives;
- canonical channel grouping, numeric values, canonical units, and UTC `Z`
  timestamps;
- allowlisted serialization for adapter issues and unknown channels;
- injected `HistoricalMetricsSource` composition seam, with no simulator,
  adapter, or HTTP transport calls in the route;
- API contracts for Ping, CPU, memory, bidirectional traffic, full/partial SFP,
  partial failures, and source failures;
- no normalized cache and no frontend integration.

The public paths coexist:

~~~text
PRTG historic CSV -> legacy parser/cache -> /api/history -> current UI

HistoricalMetricsSource -> PRTGMetricAdapter -> NormalizedHistoryResult
       -> metrics.serialization -> /api/v2/metrics/history
~~~

The v2 API remains experimental during Phase 0.3. A composition root must
inject a normalized provider; without one, v2 reports an upstream-unavailable
response while every legacy path remains functional.

## Phase 0.3F.1: Laboratory metrics provider

Implemented scope:

- concrete `SyntheticHistoricalMetricsSource` using the simulator's real
  localhost HTTP multichannel history endpoint;
- explicit `COREVIEW_METRICS_PROVIDER=synthetic` activation, disabled by
  default and validated by centralized configuration;
- composition in `create_app()`, while preserving direct provider injection;
- loopback-only destinations, rejected redirects/userinfo/query strings, and
  no synthetic transmission of configured PRTG credentials;
- points mode mapped directly to the simulator's 1-1000 point contract;
- range mode resolved from the actual logical tick and tick interval, followed
  by inclusive source-timestamp filtering;
- sanitized provider errors using the existing HistoryService exception model;
- real ephemeral HTTP integration tests across simulator, provider,
  HistoryService, Flask API, serializer, and all five sensor families;
- reproducible localhost smoke-test documentation.

The provider transports PRTG-shaped source observations only.
`PRTGMetricAdapter` remains the sole channel/unit normalizer, so partial
channels, issues, and unknown channels retain their existing contracts.

Range mode never invents timestamps or returns out-of-range observations.
Intervals needing more than 1000 simulator ticks, containing no logical tick,
or beginning after current logical time are reported as upstream failures.

## Remaining planned incremental phases

### 0.3F.2 - Frontend and chart integration

Add multichannel visualization only after backend contracts are stable. Select
channels and axes by metadata rather than sensor-name conditions, and retain the
current dashboard until equivalent behavior is verified.

## Entry criteria for Phase 0.3F.2

- Phase 0.3A through 0.3F.1 remain green with the complete regression suite.
- `/api/history` and the current dashboard remain available as a rollback
  path.
- The frontend consumes only the versioned serializer contract, not PRTG
  source labels or adapter internals.
- Channel selection and axes are driven by canonical definitions and units.
- Loading, empty, partial-channel, unknown-channel, issue, and upstream-error
  states have explicit UI behavior.
- Traffic IN/OUT and partial SFP layouts are designed before replacing any
  current chart.
- Any provider used for manual UI validation is configured at the composition
  root and does not create a simulator dependency in Flask.

## Migration principles

- Add before replacing.
- Preserve legacy contracts until parity is explicit.
- Keep stable IDs separate from mutable display metadata.
- Keep numeric storage separate from localized formatting.
- Normalize new timestamps to UTC without rewriting legacy timestamps in place.
- Keep provider-specific source names in adapters and source metadata.
- Avoid sensor-name conditionals in shared domain and presentation code.
- Require tests for every supported channel, unit conversion, and compatibility
  boundary.
