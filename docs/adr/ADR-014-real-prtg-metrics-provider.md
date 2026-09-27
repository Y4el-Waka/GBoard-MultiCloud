# ADR-014: Real PRTG metrics provider, read-only

## Context

Phase 0.4A introduced read-only discovery for one selected PRTG sensor but did
not connect that evidence to CoreView's normalized history path. Phase 0.4B
needs a provider for metadata and history while preserving the synthetic
provider, the legacy collector/history path, and the public v2 contract. No
authorized real PRTG instance was available for this increment.

Original PRTG history is comparatively expensive and installation-dependent:
timestamps can be naive, captions/locales vary, and Paessler documents a
historic-data request budget. A provider must therefore fail closed instead of
guessing a family, unit, timestamp, or missing value.

## Decision

Add `RealHistoricalMetricsSource` as an opt-in implementation of the existing
`HistoricalMetricsSource` protocol. The composition root creates it only when
`COREVIEW_METRICS_PROVIDER=prtg`; the default remains `disabled`, and
`synthetic` remains a separate explicit option.

The provider:

- resolves identity as configured `server_id + sensor_id`;
- delegates metadata and exact-sensor verification to
  `PRTGSensorDiscovery`;
- returns raw `historicdata.csv` plus source timezone/aggregation metadata;
- leaves aliases, parsing, unit conversion, UTC normalization, issues, and
  unknown channels to the existing `PRTGMetricAdapter` through
  `HistoryService`;
- never imports the synthetic catalog;
- permits plain HTTP only for loopback fixture/laboratory servers and requires
  TLS verification for configured `prtg` mode;
- rejects redirects through the discovery/client calls;
- caches successful discovery results under `(server_id, sensor_id)` with a
  bounded TTL/LRU cache, without caching authentication/transport failures;
- applies a sliding-window history budget per configured `server_id`, with a
  configurable maximum no greater than five requests per minute;
- supports one process only in this experimental phase, because the in-memory
  request budget and metadata cache cannot coordinate across processes.

Points mode maps `N` to a real request window of `N * avg` ending at the
provider clock. Range mode forwards the exact aware bounds. Returned samples
are filtered by the requested mode after adaptation; the provider never
fabricates or resamples timestamps to meet a requested count.

Typed source/domain errors distinguish not found, unknown, unsupported,
authentication, invalid response, transport, and local rate budget. Flask
continues to own HTTP serialization and emits allowlisted messages only.

An explicit manual smoke command requires
`--confirm-read-only-authorized`, selects one configured server and sensor,
and prints only a normalized summary. It is never invoked by unittest, CI, or
application startup.

## Alternatives considered

### Normalize inside the provider

Rejected because it would duplicate the tested mappings, locale handling, and
unit conversion in `PRTGMetricAdapter`.

### Fall back to synthetic data when PRTG fails

Rejected because a successful-looking synthetic response would hide an
operational or authentication failure and mix identities across providers.

### Query every sensor or prefetch histories

Rejected because discovery/history must remain selected-sensor, on-demand,
and within the PRTG request budget.

### Shared multi-process rate limiting or distributed cache

Deferred. Redis/database coordination would expand scope. Configuration
therefore rejects an expected process count other than one.

### Treat accumulated volume as traffic rate

Rejected. Counter-to-rate requires elapsed-time semantics and remains out of
scope; volume observations stay non-canonical/observable rather than becoming
false bps samples.

## Consequences

- The public v2 API and existing frontend can use a real-source-shaped path
  without changes to channel mappings.
- Synthetic and disabled behavior remain independent and regression-tested.
- Metadata lookup is bounded and efficient, but each process would otherwise
  have an independent cache/budget; production deployment must currently use
  one application process for `prtg` mode.
- Server-name lookup remains a legacy request concern; normalized response
  identity remains the stable configured server ID.
- Exact source captions, localization, aggregation, sensor-type aliases, DST,
  and installed-version behavior remain unverified against real PRTG.
- `implementation_complete_with_fixtures: true`.
- `validated_against_real_prtg: false` until the manual authorized smoke test
  succeeds and its sanitized evidence is reviewed.
