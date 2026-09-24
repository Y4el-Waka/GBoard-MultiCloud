# ADR-010: Laboratory metrics provider

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3F.1

## Context

Phase 0.3E exposed normalized history through
`GET /api/v2/metrics/history`, but normal application startup did not compose
a `HistoricalMetricsSource`. Tests could inject an in-process source, while a
developer running the simulator, collector, and CoreView still received
`502 upstream_error` from v2.

The synthetic multichannel simulator already exposes PRTG-shaped history over
`/<server>/api/multichannel_history.json`. It supports point counts, not
explicit date ranges. The laboratory provider must make that source usable
without changing the endpoint, HistoryService, adapter, legacy simulator
routes, snapshot, collector, persistence, or frontend.

## Decision

Add `SyntheticHistoricalMetricsSource`, an HTTP source implementation with no
Flask, adapter, database, or frontend dependency. Its output remains raw
PRTG-shaped source data. HistoryService continues to invoke
`PRTGMetricAdapter`, so aliases, unit conversion, issue handling, partial
channels, and canonical samples are not duplicated.

The runtime flow is:

```text
Flask API v2
  -> HistoryService
  -> SyntheticHistoricalMetricsSource
  -> localhost multichannel simulator
  -> PRTGMetricAdapter
  -> NormalizedHistoryResult
  -> metrics serializer
```

### Explicit activation and composition

`COREVIEW_METRICS_PROVIDER` is typed application configuration with two
accepted values:

- `disabled` (default);
- `synthetic`.

Unknown values fail configuration loading. During `create_app()`, the
composition root creates the synthetic provider only when the configured value
is `synthetic`. Explicit provider injection remains available to tests and
future composition roots. The endpoint itself still knows only HistoryService.

When disabled, v2 preserves the Phase 0.3E behavior and returns `502` because
no normalized source is configured. Legacy features are unaffected.

### Localhost security boundary

The provider obtains its destination only from the selected configured server,
never from a request URL parameter. It accepts only `http` or `https` base
URLs whose hostname is `localhost` or a loopback IP address. Userinfo, query
strings, fragments, missing simulator paths, non-loopback hosts, and redirects
are rejected.

Provider requests send only sensor ID and point count. Configured PRTG
username, password, and passhash values are not sent to the synthetic
multichannel route. Each request creates a short-lived Session with
`trust_env=False`, so proxy environment variables cannot redirect loopback
traffic and no mutable Session is shared between Flask threads. Transport
exceptions are translated to sanitized
`HistoricalMetricsSourceError` values.

### Points contract

Points mode maps directly to the simulator endpoint. Values from 1 through
1000 are accepted, matching the public v2 limit and simulator contract.
Payloads must be marked synthetic, contain the requested sensor identity, and
contain a non-empty sample sequence.

### Range contract

The simulator's history ends at its current logical tick and has a known
`tick_seconds` resolution. For range mode the provider:

1. requests one sample to discover the real current tick and resolution;
2. calculates the number of existing ticks needed to reach `start`;
3. requests that exact point window, up to 1000 points;
4. parses the source timestamps only for temporal selection;
5. returns only observations satisfying `start <= timestamp <= end`.

It never alters or fabricates timestamps. If the range contains no simulator
tick, begins after the current logical time, has invalid timestamps, or needs
more than 1000 ticks, acquisition fails as a sanitized upstream error. The
provider does not silently truncate a requested range.

### Errors and partial data

Simulator HTTP 404 maps to
`HistoricalMetricsSensorNotFoundError`. Connection, timeout, invalid JSON,
invalid envelope, mismatched sensor, temporal-resolution, and HTTP failures map
to `HistoricalMetricsSourceError`. HistoryService retains the Phase 0.3E
translation to 404 or 502.

Valid partial channels, unknown channels, and invalid individual channel values
remain in the source envelope. The provider neither normalizes nor zero-fills
them; the existing adapter and serializer preserve their established behavior.

## Alternatives considered

### Import the global simulator object into CoreView

Rejected because it would construct another 13-server/1000-sensor dataset in
the web process, couple lifecycle and memory to Flask, and fail to validate the
real multi-process laboratory topology.

### Call the simulator directly from the Flask route

Rejected because the endpoint would acquire provider and transport knowledge,
breaking the HistoryService boundary established in ADR-008 and ADR-009.

### Add synthetic methods to PRTGClient

Rejected because the multichannel routes are laboratory-specific and are not a
real PRTG API contract. PRTGClient remains the transport for supported
PRTG-compatible operations.

### Ignore start/end and translate every range to a fixed point count

Rejected because it could return observations outside the requested interval
and misrepresent range semantics.

### Enable the provider automatically when server URLs look local

Rejected because synthetic data must be an explicit operator choice. URL shape
must not silently switch application behavior.

### Permit configured remote simulator hosts

Rejected for this laboratory phase. The provider is deliberately loopback-only
and cannot become an arbitrary outbound HTTP client.

## Consequences

Positive consequences:

- Normal CoreView laboratory startup can serve all five metric families from
  the actual simulator process.
- The public v2 endpoint, service, adapter, and serializer contracts remain
  unchanged.
- Points and range modes are both backed by real simulator timestamps.
- Tests exercise a real ephemeral localhost HTTP server and cleanly stop it.
- Synthetic activation is explicit, disabled by default, and loopback-only.
- Legacy collector, snapshot, SQLite, controls, history, and frontend remain
  unchanged.

Tradeoffs and remaining work:

- Range queries are limited to 1000 simulator ticks and may make two HTTP
  requests.
- A range containing no logical tick returns an upstream error; an empty-result
  HTTP contract has not been designed.
- Simulator logical time advances only through its explicit tick/advance
  controls and resets on restart.
- There is no normalized cache.
- Real PRTG multichannel acquisition remains out of scope.
- Frontend consumption remains deferred to Phase 0.3F.2.
