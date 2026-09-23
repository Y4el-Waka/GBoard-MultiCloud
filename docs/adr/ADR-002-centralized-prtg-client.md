# ADR-002: Centralized PRTG client

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.2C

## Context

Before Phase 0.2C, `app.py` and `monitor.py` each constructed their own
`requests.Session`, retry adapter, authentication query parameters, TLS options,
timeouts, and PRTG endpoint URLs. The implementations duplicated transport
behavior and shared a monitor session across `ThreadPoolExecutor` workers even
though `requests.Session` is not designed as a shared concurrent mutable object.

The Flask application directly called PRTG for historic CSV and pause/resume
operations. The collector directly called the table endpoint for devices and
sensors. Both consumers must continue to work with real PRTG and the local
synthetic API without changing public responses, snapshot structure, history
parsing, MF behavior, persistence, or UI behavior.

## Decision

Create `prtg_client.py` as the only PRTG HTTP transport layer. It exposes the
small `PRTGClient` API:

- `get_devices(server)`
- `get_sensors(server)`
- `get_historic_csv(server, sensor_id, start, end, average_seconds=...)`
- `pause_for(server, sensor_id, minutes, message)`
- `pause_indefinitely(server, sensor_id, message)`
- `resume(server, sensor_id)`

The client builds endpoint URLs, adds legacy password or passhash authentication,
applies the configured timeout and TLS verification flag, configures retries,
backoff, and connection pools, executes GET requests, checks HTTP status, and
performs only minimal transport-level response validation.

JSON table responses must be JSON objects. Historic CSV remains source text and
is returned unchanged so the existing parser stays in `app.py`. The client does
not interpret sensor names, channels, values, units, Ping semantics, MF state, or
frontend datasets.

### Session lifecycle and thread safety

Each `PRTGClient` instance owns one `requests.Session`. A client instance is
created inside one Flask operation or one monitor worker and is used as a context
manager. The context manager closes its owned session deterministically.

No client or session is shared between worker threads. Within a monitor worker,
the same instance can reuse its connection pool for the devices and sensors
requests. Flask operations likewise receive an isolated instance. This lifecycle
is intentionally simpler than a global or thread-local session registry and does
not change the collector's worker count or scheduling.

### Authentication

PRTG credentials remain query parameters for compatibility with legacy PRTG:

- `username` plus `passhash` when passhash is configured.
- `username` plus `password` otherwise.

Passhash retains precedence when both values exist. Missing credentials raise a
typed authentication error.

### Error handling

The client defines four small exception types:

- `PRTGClientError`
- `PRTGConnectionError`
- `PRTGAuthenticationError`
- `PRTGResponseError`

Timeouts and connection failures become `PRTGConnectionError`. HTTP 401/403
become `PRTGAuthenticationError`. Other HTTP failures and invalid responses
become `PRTGResponseError`. Other request failures use the base client error.

The application maps these exceptions back to its existing HTTP 502 responses.
The collector maps them back to its existing per-server error record, so failures
remain isolated to the affected PRTG group.

### Secret protection

The client never logs request parameters, credential-bearing query strings, raw
request exceptions, or complete URLs. Its own messages contain only a sanitized
server name and operation label. Raised exceptions are newly constructed and do
not retain potentially credential-bearing `requests` exception text.

TLS warnings are suppressed only around an individual request when verification
is disabled. The client does not globally disable warnings for the process.

## Alternatives considered

### Keep separate transports in app and monitor

Rejected because auth, retries, TLS, errors, and endpoints would continue to
drift and would remain difficult to test consistently.

### Keep one global shared Session

Rejected because Flask requests and monitor workers may execute concurrently.
Sharing that mutable Session would retain the thread-safety risk identified in
the audit.

### Use a thread-local Session registry

Rejected for this phase because the monitor creates new executor threads on each
collection cycle. A registry would complicate deterministic cleanup and could
retain obsolete sessions.

### Create a new Session for every individual HTTP request

Rejected because a monitor worker commonly requests devices and sensors from the
same server and should be able to reuse one instance-local connection pool.

### Move history parsing and metric normalization into the client

Rejected because those are application/domain responsibilities, not transport,
and would couple the client to the current Ping-biased model.

## Consequences

Positive consequences:

- PRTG HTTP sessions, auth, retries, backoff, pools, TLS, timeouts, endpoints, and
  transport errors now have one tested implementation.
- Monitor workers and Flask operations no longer share sessions across threads.
- Real PRTG and the simulator use the same protocol-neutral client.
- Client errors and logs do not expose password/passhash values or credential
  query strings.
- The future Metrics Engine can consume transported data without making this
  client aware of metric channels or units.

Tradeoffs and remaining debt:

- Each worker or Flask operation creates and closes a Session, adding a small
  setup cost in exchange for explicit ownership and thread safety.
- Legacy PRTG authentication still places credentials in the query string on the
  wire; HTTPS and sanitized logging remain essential.
- The existing historic parser is still Ping-oriented and remains in `app.py`.
- Server filtering differences between the application and monitor remain the
  compatibility debt documented in ADR-001.
- Provider abstraction and multi-channel metric normalization are deferred to a
  later phase and must stay above this PRTG-specific transport layer.
