# ADR-006: PRTG metrics adapter

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3B

## Context

Phase 0.3A introduced provider-neutral sensor identity, channel definitions,
canonical units, and numeric UTC samples. CoreView still receives provider data
in PRTG-oriented forms: the table contract exposes a formatted `lastvalue`, and
historic data is returned as CSV with provider channel labels. Neither source
form can become canonical identity, and neither should be interpreted by
`PRTGClient`, Flask, the collector, or the domain models.

Phase 0.3B needs a tested boundary from representative synthetic PRTG source
data to the canonical domain. It must remain additive and must not connect to
the current collector, history service, simulator, snapshot, APIs, persistence,
or frontend.

## Decision

Create two provider-specific modules under `metrics`:

- `metrics.prtg_mappings` contains declarative source-channel aliases and source
  unit conversions.
- `metrics.prtg_adapter` contains `PRTGMetricAdapter` and its small result/error
  model.

The domain modules do not import the adapter. The adapter imports and validates
against the Phase 0.3A channel and sensor definitions, so canonical units and
supported channel sets are not duplicated.

The public adapter entries are:

- `adapt_sensor(source, server_id, sensor_type, source_timezone=None)` for a
  legacy PRTG table record or a PRTG-shaped multichannel record;
- `adapt_historic_csv(text, server_id, sensor_id, sensor_type,
  source_timezone, ...)` for the source CSV contract returned by PRTG.

`adapt_historic_csv` is independent from the legacy `HistoryService` parser. It
only decodes rows and sends every non-time column through the same channel
mapping used for live observations. It does not create frontend datasets.

### Source-to-canonical mapping and aliases

PRTG labels are normalized for case, whitespace, a byte-order mark, and an
optional `(RAW)` suffix. Their meaning comes only from an explicit alias table;
the adapter does not classify channels using sensor-name substrings.

Examples include:

- `Ping Time`, `Ping`, and `Latency` -> `latency_ms`;
- `Packet Loss` -> `packet_loss_pct`;
- `CPU Load` and `CPU Usage` -> `cpu_usage_pct`;
- `Traffic In` / `Traffic Out` -> `traffic_in_bps` /
  `traffic_out_bps`;
- `RX Power` / `TX Power` -> `rx_power_dbm` / `tx_power_dbm`.

Provider-specific aliases can be supplied when constructing the adapter, but
they must target an existing canonical definition and cannot conflict with a
built-in alias. Adding a source alias therefore does not require changing the
adapter algorithm or the domain model.

`Coverage` from the current historical CSV is deliberately not treated as
`availability_pct`: data coverage and service availability are not universally
equivalent. It remains an observable unknown channel until provider semantics
justify a mapping.

### Unit and value normalization

Values are parsed into finite numbers before samples are constructed. Invalid,
NaN, and infinite values are issues; they never become zero.

The adapter performs explicit conversions to Phase 0.3A canonical units:

- `bit/s`, `Kbit/s`, `Mbit/s`, and `Gbit/s` -> `bps`, using decimal network
  factors;
- `Byte`, `KByte`, `MByte`, and `GByte` -> `bytes`, using explicit decimal
  factors for this adapter contract;
- `ms`, `%`, `dBm`, `°C`, `V`, and `mA` retain their canonical scale.

Unknown units are reported. Recognized units that disagree with a canonical
channel definition are reported as unit mismatches. Display formatting is not
stored in `MetricSample`.

### Time normalization

Aware source timestamps are converted to UTC. A naive timestamp is accepted
only when the caller explicitly supplies its source timezone. Otherwise that
channel receives an `invalid_timestamp` issue. The adapter never silently
assumes that a naive PRTG timestamp is UTC.

### Identity and source metadata

The caller supplies stable `server_id`; the PRTG `objid` supplies `sensor_id`,
and `parentid` may supply device context. Group, server, device, and sensor names
remain selected source metadata and do not participate in identity.

Only a small allowlist of traceability fields is copied into samples, including
source channel, source unit, source sensor type, display labels, and status. The
complete provider payload, URLs, credentials, and arbitrary large structures
are not copied or logged.

### Partial channels and error philosophy

The adapter is strict for the sensor envelope and tolerant per channel:

- missing sensor identity, unsupported sensor type, malformed channel
  collection, or malformed historic structure raises `PRTGAdapterError`;
- unknown channel labels are returned in `unknown_channels`;
- known channels with missing metadata, invalid values or timestamps, unknown
  units, unit mismatches, or unsupported counter semantics are returned in
  `issues`;
- valid sibling channels still produce `MetricSample` values.

This prevents one optional or provider-specific channel from discarding an
entire SFP, traffic, or other multichannel sensor.

### Traffic counters

Traffic inputs expressed as rates are normalized to `bps`. A source explicitly
marked as a counter is reported as `counter_unsupported`; Phase 0.3B does not
calculate deltas, handle resets/wraparound, or derive rates. That processing
requires state and belongs in a later Metrics Engine layer.

## Alternatives considered

### Put mappings in `PRTGClient`

Rejected because the client owns HTTP transport and must remain usable by both
legacy and future consumers without metric semantics.

### Add source-name conditions to the domain model

Rejected because it would make provider-neutral models depend on PRTG naming
and force core changes for every alias.

### Reuse the legacy HistoryService parser

Rejected because that parser builds a Ping-oriented frontend response, uses
legacy dataset keys, and would couple the new adapter to application services.

### Fail the complete sensor on any bad channel

Rejected because optional and vendor-specific channels are normal in PRTG and
SFP sensors frequently expose only a subset of diagnostics.

### Infer availability from Coverage or memory percentage from used bytes

Rejected because those conversions require semantics or capacity information
not present in the source contract.

## Consequences

Positive consequences:

- PRTG vocabulary and units have one explicit, tested boundary.
- Ping, CPU, memory, traffic, and SFP source observations produce the same
  provider-neutral `MetricSample` model.
- Unknown and invalid data are observable without becoming false zero values or
  destroying valid sibling channels.
- Naive timestamps cannot enter the new domain without an explicit timezone.
- The adapter can be extended with aliases while the domain and transport remain
  unchanged.
- Existing production behavior remains untouched because no current consumer
  imports the adapter.

Tradeoffs and remaining work:

- The current simulator does not expose the multichannel fixture contract yet.
- `PRTGClient`, `monitor.py`, and `HistoryService` do not invoke the adapter.
- The decimal interpretation of PRTG byte prefixes is explicit but may need a
  provider/profile option if real source evidence requires binary scaling.
- Historic provider columns without canonical semantics remain unknown; no
  guesses are made.
- Counter-to-rate calculation, deduplication, quality policy, aggregation,
  storage, retention, API serialization, and frontend integration remain out of
  scope.
