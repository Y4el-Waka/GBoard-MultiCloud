# ADR-004: Sensor control service

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.2E

## Context

After Phase 0.2D, PRTG transport and historic queries had explicit boundaries,
but `app.py` still orchestrated sensor control. The Flask module looked up and
reloaded servers, selected the PRTG pause/resume operation, clamped pause
duration, invoked `PRTGClient`, and invalidated historic cache state.

That mixture coupled HTTP presentation to application behavior and duplicated
the server lookup pattern already used by `HistoryService`. Phase 0.2E must
separate this orchestration without changing routes, authorization, audit
records, public JSON, status codes, cache invalidation semantics, or PRTG
transport.

## Decision

Create `sensor_control_service.py` as the application-service boundary for
sensor control. `SensorControlService` owns:

- requested-server lookup and the existing reload-on-miss behavior;
- validation and clamping of timed-pause durations;
- temporary pause, indefinite pause, and resume calls through a supplied
  `PRTGClient` factory;
- conversion of PRTG transport failures into sensor-control domain errors;
- full historic-cache invalidation through the public `HistoryService.clear()`
  method after a successful control operation.

The public API is deliberately small:

- `pause_for(server_identifier, sensor_id, minutes, message)`
- `pause_indefinitely(server_identifier, sensor_id, message)`
- `resume(server_identifier, sensor_id)`

`pause_for` returns the effective, clamped number of minutes so Flask can retain
its exact response and audit detail. The service defines four errors:
`SensorControlError`, `SensorServerNotFoundError`,
`SensorControlValidationError`, and `SensorControlUpstreamError`.

`app.py` creates one service instance per Flask application and supplies the
same server providers, `PRTGClient` factory, and `HistoryService` instance used
by the existing history boundary.

### Flask and audit ownership

Flask continues to own authentication, authorization, request parsing,
browser-local date/time interpretation, current-user message defaults, audit
records, HTTP status codes, and JSON responses. Audit is written only after the
service reports success, preserving the existing ordering.

The service has no Flask dependency and knows nothing about sessions, roles,
users, client IP addresses, templates, or response construction.

### PRTG transport and error handling

All control requests continue to go through `PRTGClient`. The service does not
construct URLs, authentication parameters, sessions, retry policy, TLS options,
or timeouts. It maps `PRTGClientError` to a sanitized domain error and logs only
the operation, server identifier, and sensor ID; upstream exception text and
credentials are not logged or propagated.

### History invalidation

The service invalidates history only after PRTG confirms a successful control
operation. Failures do not invalidate the cache. Invalidation remains global by
calling `HistoryService.clear()`, because changing to per-sensor invalidation
would alter the behavior frozen for this phase. The service never accesses cache
internals.

### Relationship to the Metrics Engine

Pause and resume are control-plane commands. They do not interpret samples,
channels, units, or historic datasets, so they do not belong to the future
Metrics Engine. That engine may consume sensor observations, while this service
continues to coordinate generic sensor control above PRTG transport.

## Alternatives considered

### Keep control orchestration in Flask

Rejected because server lookup, duration rules, transport invocation, and cache
invalidation are application responsibilities that can be tested independently
from HTTP.

### Add pause/resume to `HistoryService`

Rejected because history queries/cache ownership and sensor control are distinct
responsibilities. Their only relationship is the explicit invalidation request
after a successful state change.

### Add orchestration to `PRTGClient`

Rejected because `PRTGClient` is transport-only. Server discovery, domain
validation, cache invalidation, and public application errors do not belong in
the HTTP client.

### Introduce a generic command bus or provider hierarchy

Rejected as unnecessary complexity for three existing commands and contrary to
the incremental scope of Phase 0.2E.

### Change to per-sensor cache invalidation

Deferred because the current application globally clears history after control
operations. This phase preserves that observable behavior.

## Consequences

Positive consequences:

- Pause/resume orchestration is testable without Flask, Internet access, or real
  runtime files.
- `app.py` retains presentation, authorization, and auditing while no longer
  performing server lookup, PRTG control calls, or cache invalidation.
- `PRTGClient` remains the only PRTG HTTP transport layer.
- `HistoryService` remains the sole owner of historic cache state.
- Upstream failures have a small domain model and sanitized logging.

Tradeoffs and remaining debt:

- Historic invalidation remains global and process-local for compatibility.
- Browser-local `pause_until` parsing remains in Flask because it is part of the
  existing HTTP/UI workflow; timezone semantics remain legacy behavior.
- Server-management CRUD remains in `app.py` and still triggers public history
  cache invalidation directly.
- `app.py` still creates PRTG clients as composition-root wiring for
  `HistoryService` and `SensorControlService`; it does not perform direct PRTG
  requests.
- App/monitor server-loading differences remain as documented in ADR-001.
- Metric channels, units, providers, and multi-channel persistence remain
  deferred to the future Metrics Engine work.
