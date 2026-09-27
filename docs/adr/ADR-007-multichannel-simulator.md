# ADR-007: Synthetic multichannel PRTG simulator

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.3C

## Context

Phase 0.3A defined provider-neutral metric samples, and Phase 0.3B added an
independent PRTG-to-canonical adapter. The existing simulator still exposes the
legacy table and Ping-history contracts consumed by the collector and current
UI. Changing those responses to carry new channels would alter the frozen
snapshot path before parity exists.

Phase 0.3C needs realistic source data for Ping, CPU, memory, traffic, and SFP,
including time evolution, partial channels, historical series, and controlled
faults. It must exercise `PRTGMetricAdapter` in tests without making the current
collector, application, `HistoryService`, or transport client consume the new
Metrics Engine.

## Decision

Preserve every legacy simulator route and add two opt-in JSON routes:

- `/<server>/api/multichannel.json` returns one sensor when `id` is supplied or
  the server's multichannel sensors otherwise.
- `/<server>/api/multichannel_history.json` returns a requested number of
  historical multichannel source samples for one sensor.

The existing devices table, sensors table, Ping CSV, healthcheck, timed pause,
indefinite pause, and resume behavior are unchanged. Logical ticks never mutate
legacy `lastvalue` or legacy endpoint payloads.

The multichannel behavior lives in `simulator_multichannel.py` rather than
expanding Flask route code with metric formulas. The helper has no Flask,
`PRTGMetricAdapter`, domain-model, HTTP-client, database, or frontend
dependency. `datos_simulados.py` composes it only after the legacy dataset is
fully built.

### Deterministic family assignment and seed

Legacy sensor IDs are reused as provider `objid` values. As amended by
ADR-012, one shared synthetic catalog assigns the family, legacy display name,
and primary source channel before either representation is built. A fixed
ten-entry catalog pattern assigns the 1,000 default sensors as:

- 400 Ping;
- 200 CPU;
- 100 memory;
- 200 traffic;
- 100 SFP.

Legacy cards derive their names and initial `lastvalue` from that same catalog;
multichannel profiles look up the catalog by `(server_id, sensor_id)` and never
run a second family algorithm. Profiles use a SHA-256 derived seed based on
simulator seed, server slug, and sensor ID. This creates a private PRNG per
profile. The legacy initial value is selected from the profile's declared
primary channel at logical tick zero, avoiding independently randomized values.

The seed remains available through the existing constructor and CLI argument.
`SIMULATOR_SEED` supplies the default when explicitly configured. Identical
seed, topology, tick, and scenario produce identical source values.

### Logical clock

The multichannel simulator owns an aware UTC start time, a configurable tick
interval, and a current integer tick. `Simulator.tick(steps)` advances that
clock without sleeping. Values are deterministic functions of profile and tick,
so repeated reads at the same tick do not introduce independent random noise.

The default interval is 300 seconds and may be configured by constructor, CLI,
or `SIMULATOR_TICK_SECONDS`. Tests may provide an explicit aware start time.
Current and historical samples emit ISO-8601 UTC timestamps ending in `Z`.

The current endpoint can opt in to advancement with `advance=N`; direct tests
normally call `tick()` so no wall-clock waiting is required.

### PRTG-like source values

The simulator emits provider labels and formatted source values, for example:

- `Ping Time: 14.2 ms`;
- `Traffic In: 85 Mbit/s`;
- `RX Power: -4.1 dBm`.

It does not emit canonical keys or normalized numeric values. Traffic rotates
through `Kbit/s`, `Mbit/s`, and `Gbit/s`; memory rotates through `KByte`,
`MByte`, and `GByte`; SFP uses `dBm`, `°C`, `V`, and `mA`. Conversion and
validation remain the responsibility of `PRTGMetricAdapter`.

Memory percentage and used bytes are both emitted because the simulator knows
its synthetic total capacity. The adapter does not infer either measurement
from the other.

### Stateful trends

Each profile has deterministic baseline and phase parameters. Values evolve by
small continuous functions rather than a new random draw per request:

- Ping latency uses bounded jitter; loss is normally low.
- CPU varies gradually around a profile baseline.
- Memory drifts slowly and remains paired with used bytes.
- Traffic IN and OUT use distinct curves and occasional deterministic peaks.
- SFP optical/electrical values vary in small increments.

No universal SFP alert thresholds are encoded.

### Partial channels and scenarios

SFP profiles deterministically cycle through channel sets of 5, 3, 2, and 1
channels. A `partial_channels` scenario explicitly limits the returned SFP
subset further. Missing optional channels are normal source behavior.

The deliberately small scenario set is:

- `normal`;
- `cpu_spike`;
- `traffic_saturation`;
- `sfp_degradation`;
- `packet_loss`;
- `partial_channels`;
- `unknown_channel`;
- `invalid_value`.

The trend scenarios increase or degrade their named measurement progressively
with logical ticks. `unknown_channel` adds `Laser Health Index` while preserving
known channels. `invalid_value` defaults to `N/A`.

For resilience tests, an optional fault selector supports `n_a`, `dashes`,
`unknown_unit`, and `invalid_timestamp`. Faults and non-normal scenarios are
never enabled by default.

### Historical series

Historical responses contain complete PRTG-like sensor samples at ordered
logical timestamps. Each sample carries the channels appropriate to its family,
so traffic history includes IN, OUT, and utilization while SFP history retains
the profile's available optical/electrical subset.

History generation does not modify the current logical tick and does not touch
the legacy Ping CSV endpoint.

### Adapter relationship

Production simulator code does not import `metrics` or normalize data. Tests
pass source objects from current and historical multichannel responses into
`PRTGMetricAdapter` and assert canonical `MetricSample` output. This validates
the provider boundary while keeping all existing production consumers on the
legacy path.

## Alternatives considered

### Add channels to the existing sensors table

Rejected because the current table field set is characterized and consumed by
the collector. Adding fields silently would change a legacy contract.

### Replace the legacy Ping CSV with multichannel history

Rejected because `HistoryService` and the frontend depend on its current column
and dataset behavior. Migration belongs to Phase 0.3D.

### Generate new random values on every request

Rejected because it produces discontinuous series and makes regression tests
non-reproducible.

### Persist every generated sample

Rejected because Phase 0.3C is a source simulator, not a storage engine. Pure
tick-based generation is deterministic and sufficient for adapter/history
integration tests.

### Import canonical definitions into the simulator

Rejected because the simulator must imitate provider source vocabulary. Sharing
canonical keys or units would blur the adapter boundary and risk generating
already-normalized data.

## Consequences

Positive consequences:

- The legacy 13-server/1,000-sensor collector path remains unchanged.
- Legacy names/values and normalized histories now share one family catalog.
- Every required sensor family has deterministic, evolving PRTG-like channels.
- Tests can advance time and scenarios instantly without sleeps.
- Partial, unknown, invalid-value, unknown-unit, and invalid-timestamp cases are
  reproducible and opt-in.
- Current and historical simulator data normalize successfully through the
  Phase 0.3B adapter.
- The simulator still contains only synthetic identities and documentation IPs.

Tradeoffs and remaining work:

- Logical state is in-process and resets when the simulator restarts.
- Historical samples are regenerated from deterministic formulas rather than
  persisted observations.
- Scenario selection is simulator-wide by default, with per-request overrides;
  it is not a production alert model.
- Provider fidelity is intentionally limited to the source contract needed by
  the Metrics Engine tests.
- `PRTGClient`, the collector, and `HistoryService` do not consume the new
  endpoints yet.
- Aggregation, retention, counter-to-rate state, compatibility datasets, and
  public metrics APIs remain out of scope.
