# ADR-013: Read-only PRTG sensor discovery

- Status: Accepted
- Date: 2026-09-26
- Phase: 0.4A

## Context

Phase 0.3 proved the normalized metrics flow with a deterministic laboratory
catalog. That catalog is deliberately synthetic: its family assignment cannot
identify sensors from an actual PRTG installation. A future production metrics
provider needs source metadata, channel inventory, units, and historical CSV
shape before it can safely invoke `PRTGMetricAdapter`.

The original PRTG HTTP API exposes sensor tables, channel tables, and historic
CSV. These surfaces vary with sensor type, PRTG version, locale, and installed
plugins. In particular, traffic channel output can expose both accumulated
volume and current speed. Treating both as bit rates would manufacture invalid
metrics. The JSON channel table can also make repeated display/speed fields
ambiguous, whereas CSV preserves distinct column captions.

No authorized real PRTG instance was available for this phase. Therefore the
implementation must distinguish official documentation, sanitized fixture
evidence, and behavior still awaiting real-system validation.

## Decision

Add an application-independent `PRTGSensorDiscovery` boundary above
`PRTGClient`. Discovery is explicit and scoped to one selected sensor. It
performs two read-only calls:

1. `table.json?content=sensors` filtered by `objid` for source type, device,
   labels, tags, status, interval, and channel count;
2. `table.csv?content=channels` for channel names, optional IDs, last display
   values, raw values, and distinct volume/speed columns.

Historic CSV acquisition remains a separate, explicit, on-demand operation.
Discovery never fetches history automatically. It uses the existing
`historicdata.csv` transport with a caller-provided range and aggregation.

The discovery contract contains:

- canonical `SensorIdentity(server_id, sensor_id, device_id)`;
- sanitized source sensor metadata, with group/server names retained only as
  labels;
- source channels and optional channel IDs;
- every observed value representation, its source unit, and its semantic kind;
- a classification result with family, evidence, and reason;
- metadata describing the requirements for a later historical request.

Classification uses explicit PRTG source-type evidence plus compatible
canonical channel signatures. Display names are never classification input.
If the evidence is insufficient or conflicting, the result is `unknown`; a
known out-of-scope type is `unsupported`. Neither becomes Ping. Generic custom
sensors can only classify when their channel signature uniquely proves a
supported family.

Channel aliases and unit compatibility reuse `metrics.prtg_mappings` and
`metrics.definitions`. Discovery does not normalize numeric samples and does
not duplicate `PRTGMetricAdapter`. It characterizes source structure and
semantics only. Traffic volume/counter observations remain visibly distinct
from speed/rate observations; no counter-to-rate calculation is performed.

All discovery calls disable redirects, retain the configured TLS verification,
use fixed PRTG API paths, and inherit sanitized transport errors. No control
endpoint is exposed. The existing client behavior remains unchanged for legacy
callers; redirect suppression is opt-in at transport level and mandatory for
the new discovery methods.

The Phase 0.4A source-type registry and fixtures are provisional compatibility
evidence, not proof of behavior on a real installation. They must be extended
only from official documentation or sanitized, authorized captures.

## Alternatives considered

### Reuse the synthetic sensor catalog

Rejected. Its ID/family distribution is laboratory-only and contains no
evidence about real PRTG sensor metadata.

### Infer family from the visible sensor name

Rejected. Names are mutable, localized, and operator-controlled. They cannot
provide a stable semantic contract.

### Normalize source values during discovery

Rejected. `PRTGMetricAdapter` already owns aliases, numeric conversion, units,
timestamps, partial-channel handling, and adapter issues. Duplicating that work
would create divergent mappings.

### Fetch history for every discovered sensor

Rejected. It creates unnecessary load and conflicts with PRTG's documented
historic-data request limit. History characterization is selected-sensor and
on-demand only.

### Adopt Paessler API v2 immediately

Deferred. Paessler API v2 is distinct from CoreView's `/api/v2/...` contract,
uses different authentication and resources, and is documented as not yet
feature-complete. It may become a separate source implementation after
capability and version validation.

## Consequences

Positive consequences:

- production discovery has no dependency on Flask, SQLite, frontend code, or
  the synthetic catalog;
- identity remains stable across equal sensor IDs on different servers;
- unknown evidence fails closed instead of becoming Ping;
- rate and accumulated volume are observable and cannot be silently conflated;
- history load is bounded by an explicit selected-sensor action;
- the future provider can feed discovered source data into the existing
  adapter rather than grow a second normalizer.

Tradeoffs and risks:

- exact PRTG type tokens, channel headers, channel ID availability, locale
  behavior, and historic timestamp conventions still require authorized real
  validation;
- CSV parsing currently recognizes the sanitized English/Spanish header forms
  represented by fixtures, not every PRTG locale;
- a custom SFP sensor needs at least compatible RX and TX optical channels to
  classify as SFP; partial or vendor-specific sets remain unknown until mapped;
- this phase provides a reusable backend boundary, not a public route, CLI, or
  production metrics provider;
- redirects are rejected rather than followed, so administrators must
  configure the canonical authorized PRTG base URL.
