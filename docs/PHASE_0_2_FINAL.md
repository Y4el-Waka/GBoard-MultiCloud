# Phase 0.2 Final Architecture and Closure Review

- Status: Closed after validation
- Date: 2026-09-23
- Scope: Backend responsibility boundaries without observable behavior changes

## Final architecture

CoreView Cloud Lab remains a local-first Flask application and collector with a
synthetic PRTG-compatible environment. Phase 0.2 separated configuration,
transport, history, and sensor-control responsibilities while preserving the
existing dashboard, APIs, snapshot, SQLite data, MF behavior, and simulator.

```text
                         config.py
                settings, paths, server sources
                              |
             +----------------+----------------+
             |                                 |
        monitor.py                           app.py
     collection/processing          Flask/auth/audit/HTTP/UI
             |                       |                 |
             |                HistoryService   SensorControlService
             |                       |                 |
             +-----------------------+-----------------+
                                     |
                               PRTGClient
                         generic PRTG HTTP transport
                                     |
                       real PRTG or datos_simulados.py

        app.py -------- database.py -------- monitor.py
                 shared monitoring SQLite DDL
```

## Components and ownership

### `config.py`

Owns environment loading, typed settings, common defaults, runtime path
resolution, PRTG server-source precedence, server normalization, and the
documented app/monitor compatibility profiles. Importing `config.py` itself
does not load `.env` or write runtime files.

### `prtg_client.py`

Is the only PRTG HTTP transport layer. It owns sessions, authentication,
timeouts, retries, backoff, pools, TLS verification, endpoint construction,
minimal response validation, sanitized errors, and deterministic session
cleanup. It does not interpret metric semantics.

### `history_service.py`

Owns historic server lookup, PRTG historic requests, the current legacy CSV
parser, the existing Ping-oriented response shape, and the bounded thread-safe
in-process cache. Its current payload is a compatibility adapter, not the future
canonical metric model.

### `sensor_control_service.py`

Owns server lookup, pause-duration validation, temporary pause, indefinite
pause, resume, PRTG error translation, and public HistoryService cache
invalidation. It has no Flask, user, role, or audit dependency.

### `database.py`

Owns only the shared, unchanged SQLite DDL for `sensor_events`, `sensor_state`,
and `mf_alert_state`, including their indexes. It performs no connection setup,
retention, migration versioning, or import-time writes. Flask and the collector
retain their existing connection PRAGMAs and retention behavior.

### `monitor.py`

Owns collection scheduling, worker concurrency, status normalization, device
cache, sensor snapshot construction, event detection, MF state, persistence,
aggregate summary series, and atomic snapshot publication. Each worker uses an
isolated `PRTGClient`. The device-cache TTL now uses timezone-aware UTC
internally; serialized timestamps remain unchanged.

### `app.py`

Owns Flask routes, authentication, authorization, users, pins, audit records,
server administration, request/response translation, UI delivery, application
composition, and the WSGI `app` object. It creates and wires the history and
sensor-control services but does not perform direct PRTG HTTP operations.

### `datos_simulados.py`

Provides the synthetic PRTG-compatible API used by local development and
regression tests. It remains independent of special client-side simulator
logic.

## Data flows

### Current sensor snapshot

1. `monitor.py` loads the monitor server profile from `config.py`.
2. A worker-specific `PRTGClient` requests devices and sensors from each PRTG
   endpoint or the simulator.
3. The collector normalizes status, correlates devices, evaluates MF state,
   detects changes, and persists current state/events using the shared schema.
4. The collector atomically publishes `estado_sensores_prtg.json` with the
   existing top-level contract.
5. `app.py` serves that snapshot through `/api/sensors`.
6. The unchanged frontend applies its existing filters and rendering.

### Historic query

1. Flask authenticates the request and validates HTTP parameters.
2. `HistoryService` resolves the configured server and checks its cache.
3. `PRTGClient` returns source historic CSV.
4. `HistoryService` applies the legacy parser and returns the current datasets
   payload.
5. Flask preserves the existing JSON response and status-code mapping.

### Sensor control

1. Flask authenticates the user, reads request parameters, and builds the
   existing user-facing message.
2. `SensorControlService` resolves the server and calls `PRTGClient` for timed
   pause, indefinite pause, or resume.
3. On success, the service requests global invalidation through the public
   `HistoryService.clear()` API.
4. Flask writes the existing audit record and returns the unchanged JSON.

## Principal decisions

- Application and monitor settings remain separate typed profiles so different
  timeout and compatibility defaults are explicit.
- Relative runtime paths resolve against the project/runtime `BASE_DIR`, not the
  process working directory.
- One `PRTGClient` instance owns one HTTP session; sessions are not shared among
  collector threads.
- Transport remains metric-agnostic. It returns generic PRTG tables or source
  CSV without Ping-specific interpretation.
- History parsing/cache and sensor control are application services without
  Flask dependencies.
- Audit ownership remains in Flask because it depends on the authenticated user
  and request context.
- Shared monitoring tables have one DDL definition, while retention and
  connection policy remain with their current processes.
- The development secret fallback remains compatible, but application startup
  emits a warning whenever it is active. Production-like operation must set
  `APP_SECRET_KEY` explicitly.

## Closure findings

| Finding | Classification | Impact, risk, and relation to Phase 0.3 |
|---|---|---|
| Import-time side effects | Defer to later Phase | Importing `app.py` loads environment/settings and creates the global app, which initializes SQLite, seeds configured users, and loads servers. Removing this requires a parametrized bootstrap and deployment review. New Metrics Engine modules must remain import-safe and must not import `app.py` or `monitor.py`. |
| Global `app = create_app()` | Document only | Required by the current WSGI contract. It is acceptable while the factory remains callable for tests. Changing it has deployment regression risk and does not directly block metric normalization. |
| SQLite initialization and migrations | Defer to later Phase | Current DDL and additive `ALTER TABLE` operations are idempotent but unversioned. A migration framework/version table is appropriate before a production persistence change, not during compatibility closure. |
| Duplicated monitoring schema | Fix now | Literal DDL duplication could drift during Phase 0.3. The unchanged shared tables and indexes now live in `database.py`; PRAGMAs, retention, and data behavior were not moved. |
| Mutable server-name identity | Defer to later Phase | `group_name`/`grupo` participates in snapshot, pins, events, MF state, history cache, and public requests. A safe migration needs dual identity and backfill, so changing it now would have high regression risk. |
| Deprecated `datetime.utcnow()` | Fix now | It was limited to an internal device-cache TTL. It now uses aware UTC and does not affect serialized timestamps or public payloads. |
| Insecure application-secret fallback | Fix now | Startup now warns when the compatibility fallback is active. Fail-fast is deferred until an explicit development/production runtime mode exists, avoiding disruption to local development. |
| App/monitor `enabled` and `type` discrepancy | Defer to later Phase | The difference is characterized and intentional for compatibility. Correcting it changes visible server availability and must be coordinated with provider and identity work. |
| Runtime path resolution | Document only | Paths are centralized and deterministic relative to `BASE_DIR`; absolute overrides remain supported. Deployments that relied on CWD-relative paths must use absolute paths. |
| Ping-biased metric dependencies | Defer to later Phase | `lastvalue`, legacy history datasets, aggregate collector series, and frontend chart expectations are not a canonical multi-channel metric model. They must remain compatibility adapters while the new model is introduced. |

## Stable server identity migration strategy

Server names must remain display labels, not long-term primary identity. A
future migration should be explicit and backward-compatible:

1. Make the existing synthetic/configured server `id` mandatory and stable.
2. Add `server_id` alongside `grupo`/`group_name` in internal records and the
   snapshot while retaining the current fields during a compatibility window.
3. Add nullable `server_id` columns to pins, events, current state, and MF state;
   backfill only when the name-to-ID mapping is unambiguous.
4. Move cache and persistence keys to `(server_id, sensor_id)` and keep server
   name as mutable presentation metadata.
5. Accept current name-based API inputs during transition, resolve them once at
   the boundary, and introduce ID-based inputs only through an explicitly
   versioned contract change.

This migration should precede durable multi-provider metric storage, but it is
not required to prototype the in-memory/domain shape of the Metrics Engine.

## Accepted risks and remaining debt

- Importing `app.py` still initializes runtime state; importing `monitor.py`
  still loads environment-derived settings.
- SQLite migrations remain unversioned, and app/collector processes coordinate
  through SQLite locking rather than an application-level migration lock.
- Most serialized timestamps remain naive local wall-clock strings because
  changing them would alter established contracts.
- The default secret still permits local startup; the warning is not a
  production enforcement mechanism.
- Application and collector intentionally disagree about disabled/non-PRTG
  server records.
- Server names remain persistence and cache identity.
- History cache remains process-local and control operations invalidate it
  globally.
- Broad `except Exception` blocks remain around selected collector resilience
  boundaries; they preserve per-server isolation but should gain structured
  error categories later.
- The historical parser and current chart datasets remain Ping-oriented.

## Explicitly out of scope

- Metrics Engine implementation or frontend metric redesign.
- New sensor/channel fields or snapshot/API response changes.
- Stable-ID database migration.
- SQLite redesign, PostgreSQL, Redis, or distributed cache.
- Docker, cloud-provider resources, or deployment architecture.
- MF behavior changes, authentication redesign, roles, or audit redesign.
- Changes to templates, static assets, filters, charts, or login behavior.

## Preparation for Phase 0.3 Metrics Engine

Phase 0.3 should begin with a framework-independent domain contract above
`PRTGClient` and below snapshot/history presentation. It should define:

- stable sensor identity independent from display names;
- metric/channel identity, label, unit, value type, and source metadata;
- timestamp and timezone rules for new observations;
- normalized samples capable of carrying multiple channels per sensor;
- provider adapters that translate PRTG data without putting sensor-name
  conditions into transport or Flask;
- compatibility adapters that continue producing the existing snapshot and
  Ping history payload while migration proceeds.

The first Phase 0.3 increment should be additive and exercised with synthetic
Ping, CPU, memory, traffic, and SFP fixtures. It must not replace the current
collector output or frontend until parity is demonstrated.
