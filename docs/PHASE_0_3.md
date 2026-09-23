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

## Remaining planned incremental phases

### 0.3D - HistoryService integration

Introduce normalized historic samples behind `HistoryService` while preserving
the current Ping response through a compatibility adapter. Do not remove the
legacy parser until parity is measured and regression-tested.

### 0.3E - Metrics API compatibility

Design an additive metrics API or versioned response contract. Preserve current
public routes and payloads. Resolve stable server-ID transition and serialization
rules before exposing canonical identity externally.

### 0.3F - Frontend and chart integration

Add multichannel visualization only after backend contracts are stable. Select
channels and axes by metadata rather than sensor-name conditions, and retain the
current dashboard until equivalent behavior is verified.

## Entry criteria for Phase 0.3D

- Phase 0.3A through 0.3C remain green with the complete regression suite.
- Normalized history is introduced behind `HistoryService`, not inside
  `PRTGClient` or Flask routes.
- The existing Ping datasets remain available through an explicit compatibility
  path until parity is proven.
- New history tests use the multichannel simulator and adapter without changing
  the legacy Ping CSV contract.
- Stable sensor identity and timezone behavior are preserved across source,
  normalization, caching, and compatibility output.

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
