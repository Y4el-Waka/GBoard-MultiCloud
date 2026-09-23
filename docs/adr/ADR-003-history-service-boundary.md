# ADR-003: History service boundary

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.2D

## Context

After Phase 0.2C, PRTG HTTP transport was centralized in `prtg_client.py`, but
`app.py` still owned the complete historic-data workflow. The Flask route looked
up and reloaded servers, calculated date ranges, called `PRTGClient`, parsed CSV,
built the frontend payload, managed a process-local TTL/LRU cache, logged empty
results, and translated transport failures into HTTP responses.

This mixture made the history behavior difficult to test without Flask and kept
domain details in the presentation layer. It also increased the risk of changing
the current API payload while preparing a future multi-channel Metrics Engine.

The existing parser is intentionally Ping-oriented and its output is consumed by
the current frontend. Phase 0.2D must preserve that behavior rather than redesign
metrics, channels, units, JSON responses, or charts.

## Decision

Create `history_service.py` as the application-service boundary for historic
queries. `HistoryService` owns:

- requested-server lookup and the existing reload-on-miss behavior;
- validation of supported historic ranges;
- historic requests through a supplied `PRTGClient` factory;
- the existing CSV parsing and transformation into the current payload;
- a bounded, process-local TTL/LRU cache;
- full and per-sensor cache invalidation;
- history-specific errors and sanitized operational logging.

The public service API is deliberately small:

- `get_history(server_identifier, sensor_id, minutes=..., average_seconds=...)`
- `clear()`
- `invalidate_sensor(server_identifier, sensor_id)`
- `cleanup()`
- `cache_size()` and the read-only `cache_max_entries` property

`app.py` creates one service instance per Flask application. The `/api/history`
route only reads request parameters, invokes the service, serializes the returned
payload, and translates typed service errors to the existing HTTP responses.
Pause/resume and server administration continue to invalidate the entire history
cache, preserving the previous behavior, but now do so through `clear()`.

### Cache lifecycle

The cache remains in process memory and preserves the configured TTL and maximum
entry count. Entries are keyed by server identifier, sensor ID, requested window,
and averaging interval. Cache hits update LRU order; expired entries are removed
on access or explicit cleanup; insertion evicts the least recently used entry
when the configured bound is exceeded.

An `RLock` protects all cache reads, writes, cleanup, size checks, and
invalidations. This preserves safe access when the Flask server handles concurrent
requests without introducing a distributed cache or changing deployment
semantics.

### Error mapping

The service defines a small domain error model:

- `HistoryServiceError`
- `HistoryServerNotFoundError`
- `HistoryValidationError`
- `HistoryUpstreamError`

`PRTGClientError` is converted to `HistoryUpstreamError`. Flask maps these errors
to the current 404, 400, and 502 JSON responses. Transport details remain below
the service boundary and are not exposed in public responses.

### Legacy parser boundary

The parser moved intact from `app.py` to `history_service.py`. Its fixed
classification of Ping, minimum, maximum, packet loss, downtime, and coverage is
legacy compatibility behavior, not the future metric model.

Phase 0.3 must replace fixed channel classification with provider/channel
metadata above `PRTGClient`. New CPU, memory, traffic, or SFP name-based branches
must not be added to this parser. `PRTGClient` remains transport-only and is not
coupled to Ping or frontend datasets.

## Alternatives considered

### Keep the workflow in the Flask route

Rejected because Flask would continue to own domain orchestration, parsing, and
cache state, leaving history difficult to reuse and test independently.

### Move historic parsing into `PRTGClient`

Rejected because transport should return source data and must remain unaware of
metric semantics, units, channels, and frontend payloads.

### Introduce a repository, provider hierarchy, and distributed cache now

Rejected as unnecessary architecture for Phase 0.2D. The current process-local
cache and PRTG-only source are sufficient to preserve behavior while establishing
one clear service boundary.

### Redesign the parser for multiple sensor types now

Rejected because it would change semantics beyond this phase and risk the frozen
Ping and frontend contracts. Multi-channel normalization belongs to Phase 0.3.

## Consequences

Positive consequences:

- Historic orchestration can be tested without Flask, Internet access, or real
  runtime files.
- Flask no longer knows about CSV columns, Ping datasets, cache keys, TTL/LRU
  mechanics, or direct historic PRTG calls.
- Cache behavior and invalidation now have one thread-safe implementation.
- Typed history errors make the service boundary explicit while public Flask
  status codes and JSON remain unchanged.
- The future Metrics Engine has a clear integration point above generic PRTG
  transport and below HTTP presentation.

Tradeoffs and remaining debt:

- Cache state is still local to one process and is not shared across multiple
  application instances.
- The parser and returned payload remain coupled to the current Ping-oriented
  frontend contract.
- The application still owns pause/resume orchestration and server-management
  operations; they only call the service for cache invalidation.
- App/monitor server-loading compatibility differences remain as documented in
  ADR-001.
- Provider abstraction, canonical channel identities, unit normalization, and
  multi-channel storage are deferred to Phase 0.3.
