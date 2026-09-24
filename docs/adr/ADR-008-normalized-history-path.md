# ADR-008: Normalized history path

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3D

## Context

CoreView's current historical path asks PRTGClient for CSV and converts it with
a Ping-oriented parser into datasets consumed directly by /api/history and
Chart.js. That behavior is characterized, cached, and must remain stable. It
cannot represent CPU, memory, bidirectional traffic, or partial SFP channels
without mixing provider parsing, domain semantics, and presentation concerns.

Phase 0.3A introduced canonical samples, 0.3B introduced PRTGMetricAdapter, and
0.3C introduced deterministic PRTG-shaped multichannel histories. Phase 0.3D
needs to join those pieces behind HistoryService without changing the legacy
API, cache, parser, frontend, collector, snapshot, or persistence.

## Decision

Keep two explicit paths in HistoryService:

~~~text
legacy:
PRTG historic CSV -> parse_history_csv -> Ping datasets -> current API/UI

normalized, opt-in:
HistoricalMetricsSource -> PRTGMetricAdapter -> NormalizedHistoryResult
~~~

get_history() and parse_history_csv() are unchanged. Flask continues to call
only get_history(). The new get_normalized_history() method is invoked directly
by tests and future backend integrations; no public HTTP route is added.

### Source/provider boundary

HistoricalMetricsSource is a minimal structural protocol with one operation,
fetch_history(server, sensor_id, points, start, end). It returns a mapping
whose samples collection contains PRTG-shaped sensor observations. The service
does not know whether the provider uses the synthetic simulator, a fixture,
PRTGClient, or another future provider.

The provider receives normalized aware UTC query boundaries. It owns source
acquisition only. Channel aliases, values, units, timestamps, and issues are
interpreted exclusively by the existing PRTGMetricAdapter.

Provider implementations signal expected acquisition failures through
HistoricalMetricsSourceError; transport failures from a future PRTG-backed
provider may also use PRTGClientError. Missing source sensors use
HistoricalMetricsSensorNotFoundError and are translated to
HistorySensorNotFoundError. Structural adapter failures become
HistoryUpstreamError without logging payloads or credentials.

### Result model

NormalizedHistoryResult is an immutable service result containing:

- stable SensorIdentity(server_id, sensor_id);
- normalized sensor type;
- ordered canonical MetricSample values;
- definitions for channels that produced valid samples;
- adapter UnknownChannel values;
- adapter AdapterIssue values;
- observed UTC-aware start and end timestamps.

by_channel() builds an on-demand mapping from canonical channel key to its
sample tuple. The result stores one canonical sample collection rather than a
second duplicated series index. It contains no colors, axes, Chart.js datasets,
formatted rates, Flask responses, or serialization policy.

The configured server's stable id is authoritative. Display names and
group_name remain source metadata and do not participate in identity.

### Ordering and deduplication

Samples are sorted by (timestamp, channel_key). When a provider supplies more
than one value for the same canonical channel at the same timestamp, provider
order is authoritative and the last observation wins. This is deterministic,
small, and sufficient for the current in-memory integration. Issues and unknown
channels are not hidden or collapsed.

### Issue propagation and partial channels

Per-channel adapter issues and unknown channels are accumulated across all
source timestamps. Invalid values, units, or timestamps never become zero.
Valid siblings still produce samples, so a bad traffic-IN observation does not
discard traffic OUT or utilization, and a partial SFP sensor remains valid.

Only a structurally unusable payload, mismatched identity, missing history
collection, or complete source acquisition failure rejects the operation.

### Timezone

Every normalized sample remains a timezone-aware UTC datetime. Optional query
boundaries must be aware and are converted to UTC before crossing the provider
boundary. The service does not serialize them to strings.

Legacy timestamps remain naive strings because changing them would alter the
current API contract.

### Ping parity

Parity tests generate one synthetic Ping history and pass equivalent source
values through both paths. They verify equal point counts, order, timestamps,
latency, and packet loss.

The known semantic differences are intentional:

- legacy timestamps are naive ISO strings; normalized timestamps are aware UTC
  datetime values;
- normalized history retains availability_pct; the legacy parser does not
  classify Availability;
- legacy minimum, maximum, downtime, and coverage datasets have no automatic
  canonical equivalent in the current Ping definition;
- coverage is not inferred as service availability.

### Cache

Phase 0.3D adds no normalized cache. Every normalized call invokes its provider.
The bounded legacy cache, keys, TTL, invalidation, and observable behavior are
unchanged. This avoids mixing incompatible payload types or introducing a
second cache lifecycle before a normalized API and query semantics exist.

If caching is later required, it must use a separate key namespace that
includes stable server ID, sensor ID, sensor type, query range, source profile,
and adapter/version semantics.

### Legacy parser retirement

The legacy parser can be considered for retirement only after:

1. a versioned normalized metrics API exists;
2. real PRTG source profiles prove parity for required Ping semantics;
3. the frontend has migrated without losing its existing datasets;
4. cache and serialization contracts are versioned;
5. the compatibility window and rollback path are documented.

Until then, it remains the implementation behind /api/history.

## Alternatives considered

### Replace get_history() with normalized samples

Rejected because it would change the public JSON, cache values, timestamp
format, datasets, units, axes, and frontend behavior.

### Reuse the legacy parser before creating MetricSample values

Rejected because its fixed Ping classifications and presentation metadata
would contaminate the provider-neutral domain.

### Add multichannel methods to Flask first

Rejected because Phase 0.3D is a backend integration and does not yet have a
versioned serialization contract.

### Put simulator calls directly in HistoryService

Rejected because production history must be able to use real PRTG or another
provider without simulator-specific branches.

### Add a normalized cache now

Rejected because direct deterministic tests do not need one and cache keys
cannot be finalized before API/query semantics are decided.

## Consequences

Positive consequences:

- HistoryService now proves the complete source-to-domain integration.
- Ping parity is measured without touching the current public path.
- CPU, memory, traffic, and SFP histories are available as canonical samples.
- Stable identity, UTC, unknown channels, and typed issues survive the service
  boundary.
- The simulator is only one injected provider and is not imported by production
  history code.
- Flask, Chart.js, SQLite, the collector, and the snapshot remain unchanged.

Tradeoffs and remaining work:

- A production PRTG-backed multichannel source provider is not wired yet.
- Normalized calls have no cache and may repeat upstream work.
- Unknown channels and issues retain adapter observation details but are not yet
  serialized for an API.
- Query range semantics need finalization before a public metrics endpoint.
- Counter-to-rate processing, aggregation, resampling, retention, and durable
  storage remain out of scope.
