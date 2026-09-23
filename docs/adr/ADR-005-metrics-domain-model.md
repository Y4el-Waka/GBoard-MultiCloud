# ADR-005: Metrics domain model

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3A

## Context

The existing CoreView snapshot and historic payloads were designed around
PRTG's sensor records and a Ping-oriented chart contract. A sensor currently
exposes a formatted `lastvalue`, while historic parsing recognizes specific CSV
column names. That representation cannot safely describe several simultaneous
channels with different units, such as traffic in/out or SFP optical and
electrical measurements.

Phase 0.3 needs a canonical metric vocabulary before provider parsing,
persistence, APIs, or frontend changes begin. Phase 0.3A must be additive: the
current collector, history parser, snapshot, APIs, SQLite schema, and UI remain
the compatibility baseline and do not consume the new model yet.

## Decision

Create the framework-independent `metrics` package with two small modules:

- `metrics.models` contains immutable domain dataclasses and small semantic
  enums.
- `metrics.definitions` contains canonical units, channel definitions, and
  predefined sensor channel sets.

The package imports no Flask, PRTG client, HTTP, SQLite, collector, or frontend
code. Importing it has no runtime or filesystem side effects.

### Sensor and channel separation

`SensorIdentity` identifies a sensor by `(server_id, sensor_id)`. Both values are
provider-issued/configured stable identifiers. `device_id` may be carried as
context but does not participate in equality or hashing. Mutable names such as
`group_name`, `grupo`, server display name, device label, and sensor label are
metadata, not identity.

`MetricChannelDefinition` describes one normalized channel through a canonical
key, display label, canonical unit, numeric value type, semantic, optional
description, optional expected range, and extensible metadata. Canonical keys
are plain validated strings rather than members of a closed enum, so new custom
channels can be added without modifying the model base.

`SensorMetricDefinition` describes the channel subset a sensor type can expose.
Predefined definitions exist for Ping, CPU, memory, traffic, and SFP, but a
sensor definition may contain any non-empty subset of channel definitions.

### Units and numeric values

Canonical storage units in this phase are:

- milliseconds: `ms`
- percentage: `%`
- bytes: `bytes`
- bits per second: `bps`
- optical power: `dBm`
- temperature: `°C`
- voltage: `V`
- current: `mA`

`MetricSample.value` is an `int` or `float`, never a display string. Boolean,
non-numeric, NaN, and infinite values are rejected. Formatting such as Mbps,
localized percentages, or unit suffixes belongs to presentation code and is not
stored in a normalized sample.

Expected minimum and maximum values on channel definitions are descriptive
metadata. The base sample model does not reject values outside those ranges,
because provider interpretation and alert policy are not universal structural
rules.

### Time and sample metadata

`MetricSample` contains sensor identity, channel key, numeric value, timestamp,
and unit. It can also carry optional quality, source-channel name, and source
metadata for traceability.

New metric timestamps must be timezone-aware. Samples accept any aware timezone
and normalize it to UTC during construction. This decision applies only to the
new domain; legacy external timestamp strings remain unchanged.

### Metric semantics

A small `MetricSemantic` enum distinguishes `gauge`, `counter`, `rate`, and
`state`. Current traffic-in and traffic-out definitions are rates in `bps`.
Future PRTG adapters may ingest raw counters, but counter-to-rate calculation is
not part of Phase 0.3A.

### PRTG adapter boundary

A later PRTG channel adapter will translate provider sensor types, source
channel names, raw units, and values into canonical channel keys and
`MetricSample` objects. That mapping belongs above `PRTGClient`: the transport
client must continue returning generic source data without knowing metrics.

The adapter will retain source-channel information as traceability metadata. It
must not use PRTG column labels as canonical identity and must not require
changes to the base models for every new channel.

### Legacy compatibility

No current module imports `metrics` in Phase 0.3A. The snapshot `lastvalue`,
historic Ping datasets, Flask APIs, SQLite schema, simulator endpoints, and
frontend remain unchanged. Future phases will introduce explicit compatibility
adapters and parity tests before any current consumer changes.

## Alternatives considered

### Continue using formatted strings

Rejected because values such as `42 %`, `85 Mbit/s`, and `-4.2 dBm` mix data
with presentation, prevent reliable arithmetic, and obscure canonical units.

### Use one record per sensor with fixed metric fields

Rejected because different sensors expose different channel subsets and custom
SNMP channels would require continual changes to the central model.

### Create enums for every sensor type and channel key

Rejected because a closed catalog would hinder disk, jitter, HTTP, errors,
discards, and custom channels. Only the small, stable concepts of numeric value
type and time-series semantic use enums.

### Put normalization into `PRTGClient`

Rejected because transport must remain provider-protocol focused and usable by
legacy consumers. Channel interpretation belongs to a later adapter.

### Replace the legacy snapshot immediately

Rejected because Phase 0.3A is a domain-design phase. Changing the collector,
APIs, history payload, or frontend before parity tests would create avoidable
regression risk.

### Enforce channel ranges in every sample

Rejected because ranges can be provider-, device-, configuration-, or policy-
specific. Structural validation is universal; channel policy is a separate
layer.

## Consequences

Positive consequences:

- Stable identity no longer depends on mutable server display names.
- Sensors can expose several independently typed and unit-bearing channels.
- Normalized samples are numeric, finite, timezone-aware, and UTC-based.
- Known units and definitions are consistent without closing the model to custom
  channels or units.
- Provider mapping, persistence, API design, and presentation can evolve around
  one independent domain vocabulary.
- Synthetic multichannel fixtures provide a deterministic contract before
  integration begins.

Tradeoffs and remaining work:

- The new model is intentionally unused by the current application and does not
  yet prove PRTG channel mapping behavior.
- Source quality values remain extensible strings until real provider evidence
  justifies a closed quality vocabulary.
- Expected ranges are not enforcement or alert thresholds.
- There is no aggregation, counter delta, rate calculation, resampling, storage,
  retention, or serialization API yet.
- Stable server identity exists only in the new domain; legacy persistence and
  public contracts still use server names.
- Phase 0.3B must define explicit, tested PRTG-to-canonical mappings without
  changing `PRTGClient` semantics.
