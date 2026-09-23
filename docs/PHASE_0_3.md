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

## Planned incremental phases

### 0.3B - PRTG channel adapter

Build a provider adapter above `PRTGClient` that maps representative PRTG
channel payloads to canonical definitions and samples. Establish explicit unit
conversion, source traceability, missing-channel, invalid-value, and unknown-
channel behavior. Keep the current collector output unchanged.

### 0.3C - Synthetic simulator multichannel

Extend the PRTG-compatible simulator only after the adapter contract is stable.
Expose deterministic synthetic source channels for Ping, CPU, memory, traffic,
and SFP while retaining all legacy simulator endpoints and responses.

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

## Entry criteria for Phase 0.3B

- Phase 0.3A model and fixtures remain green with the complete regression suite.
- No production module imports `metrics` yet.
- Canonical key and unit mappings are reviewed before provider code is written.
- Representative source payload fixtures are synthetic and contain no company
  or production infrastructure data.
- Unknown and partially available channels are treated as normal adapter cases,
  not model errors.

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
