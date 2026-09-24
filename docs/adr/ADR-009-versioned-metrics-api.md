# ADR-009: Versioned normalized metrics API

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3E

## Context

Phase 0.3D established a provider-neutral normalized history path through
`HistoryService`, `PRTGMetricAdapter`, and the metrics domain. The existing
`/api/history` response is Ping- and Chart.js-oriented, uses legacy timestamp
and dataset conventions, and is consumed by the current frontend. Replacing it
would create a broad compatibility break before the multichannel UI exists.

The normalized result also cannot be returned directly: it contains Python
domain objects and aware datetimes, while HTTP needs stable validation, status
codes, safe issue serialization, and canonical timestamp formatting.

## Decision

Add the authenticated, opt-in endpoint:

```text
GET /api/v2/metrics/history
```

`/api/history` and its parser, cache, JSON, and frontend consumers remain
unchanged. Version 2 is experimental during Phase 0.3.

### Query semantics

Every request identifies a configured server, source sensor, and canonical
sensor family with `server`, `sensor_id`, and `sensor_type`. Exactly one
mode is required:

- points mode uses a positive `points` integer capped at 1000;
- range mode uses both `start` and `end` as timezone-aware ISO-8601
  timestamps, with `start < end`.

Modes cannot be mixed, one range boundary is invalid, and naive timestamps are
rejected. Boundaries become aware UTC datetimes before reaching the provider.
No HTTP query defaults are implicit.

### Status codes

- `200`: success, including partial channels, adapter issues, and unknowns;
- `400`: invalid parameters, incompatible modes, or unsupported family;
- `401`: missing authenticated CoreView session;
- `404`: configured server or source sensor not found;
- `502`: normalized provider/source failure or unavailable provider;
- `500`: unexpected application error only.

Expected errors use stable codes. Upstream messages, payloads, credentials,
URLs, and stack traces are not returned.

### Serializer boundary

`metrics.serialization` transforms `NormalizedHistoryResult` to
JSON-compatible primitives. The domain and HistoryService remain unaware of
Flask and JSON. The serializer:

- exposes schema version `2.0`;
- preserves `server_id + sensor_id` and canonical sensor type;
- groups samples by canonical channel key;
- emits numeric values separately from canonical units;
- serializes aware timestamps to ISO-8601 UTC with `Z`;
- sources label, semantic, value type, and unit from domain definitions;
- emits only allowlisted issue and unknown-channel fields;
- omits raw source metadata and presentation configuration.

The route calls only `HistoryService.get_normalized_history()` and the
serializer. It does not call the simulator, adapter, or PRTGClient directly.

### Provider wiring and cache

`create_app()` accepts an optional `HistoricalMetricsSource`. This
composition-root seam lets tests or the lab inject the synthetic provider and
later permits a real PRTG provider without endpoint branches.

No provider is hardcoded into Flask. If none is configured, v2 returns `502`
while all legacy behavior continues. Phase 0.3E adds no normalized cache; the
legacy cache remains separate and unchanged.

## Alternatives considered

### Extend `/api/history` with multichannel fields

Rejected because canonical channels, aware timestamps, and new grouping would
make the existing payload ambiguous and risk current charts.

### Serialize domain objects inside HistoryService

Rejected because the service would acquire JSON and presentation concerns.

### Call the simulator or adapter directly from the route

Rejected because acquisition and normalization already belong behind
HistoryService. Direct calls would hardcode the lab source and duplicate error
policy.

### Add an implicit default range

Rejected because invisible time windows produce surprising payloads and
unstable cache semantics. Clients must choose points or an explicit range.

### Treat any bad channel as an HTTP error

Rejected because valid siblings remain useful and issues must stay observable.

### Add a normalized cache now

Rejected until the experimental contract, provider profiles, and aggregation
semantics are stable.

## Consequences

Positive consequences:

- CoreView gains an additive versioned API without changing UI or legacy
  history.
- Ping, CPU, memory, traffic, and partial SFP share one canonical JSON model.
- Values, units, timestamps, identity, issues, and unknowns are explicit.
- Flask owns auth, HTTP validation, and error translation while the service and
  domain stay framework-independent.
- Provider selection stays at the composition root.

Tradeoffs and remaining work:

- The endpoint is unavailable unless a normalized provider is injected.
- Only the synthetic provider proves the full contract today; real PRTG
  provider wiring remains pending.
- Range fidelity depends on the provider honoring supplied boundaries.
- Requests are uncached and may repeat source work.
- Request-side `server` lookup still uses a mutable configured display
  identifier although response identity exposes stable `server_id`.
- Counter-to-rate, resampling, aggregation, storage, and frontend consumption
  remain out of scope.
