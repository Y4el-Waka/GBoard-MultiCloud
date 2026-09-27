# Real PRTG Compatibility Validation

> Phase 0.4C operational procedure. No real PRTG validation has been executed
> for the repository baseline described here.

```text
implementation_complete_with_fixtures: true
validated_against_real_prtg: false
```

This document is both the runbook and the compatibility register. It must only
be used with a PRTG instance that is owned by the operator or covered by
explicit authorization. A successful command is not, by itself, a production
compatibility declaration: evidence must be sanitized and reviewed, and the
matrix row must be updated deliberately.

## Status vocabulary

Only these values are valid in the compatibility matrix:

- `verified_real`: selected sensor was exercised against an authorized real
  PRTG, the complete path was checked, and sanitized evidence was reviewed;
- `verified_fixture_only`: behavior is covered only by repository fixtures;
- `partially_supported`: real evidence confirms only a subset of expected
  channels or behavior;
- `unsupported`: reviewed evidence demonstrates an incompatible or explicitly
  out-of-scope sensor;
- `not_tested`: no adequate test exists for that row.

Never promote a row to `verified_real` from fixtures, documentation, a mock,
or an unreviewed smoke output.

## Initial implementation audit

| Control | Current implementation | Evidence now | Remaining real check |
| --- | --- | --- | --- |
| Provider activation | `COREVIEW_METRICS_PROVIDER=prtg`; default is `disabled` | Config and composition tests | Confirm deployment environment uses the intended setting |
| TLS | `prtg` mode requires `PRTG_VERIFY_SSL=true`; non-loopback HTTP is rejected | Unit/fixture tests | Verify the authorized certificate chain; never bypass validation |
| Redirects | Sensor, channel, and history calls pass `allow_redirects=False` | Client/discovery tests | Confirm canonical base URL needs no redirect |
| Identity | Discovery verifies returned `objid`; provider verifies `server_id + sensor_id` | Fixture HTTP tests | Confirm selected real sensor ID and configured stable server ID |
| Classification | Explicit source type plus compatible channel signatures; fail-closed unknown/unsupported | Five fixture families | Validate actual installed sensor types and captions |
| Metadata cache | Bounded TTL/LRU keyed by `(server_id, sensor_id)`; failed auth/transport is not cached | Provider tests | Observe repeated selected-sensor metadata only; no inventory sweep |
| History budget | Sliding maximum of five calls per minute per server ID | Deterministic tests | Run sequentially and verify 502 budget response without load testing PRTG |
| Process model | Configuration accepts only expected process count `1` | Config/provider tests | Confirm the actual WSGI deployment has one process |
| Errors | Not found, auth, transport, invalid response, unknown/unsupported, and rate budget remain distinct internally | Service/API tests | Verify safe behavior for an authorized invalid sensor and bad credential only if approved |
| Timezone | Explicit IANA source timezone; normalized samples stay UTC-aware | UTC and localized fixtures | Confirm PRTG timezone, locale, DST policy, and timestamp convention |
| Evidence output | Smoke emits allowlisted structure, aliases source columns, omits values/names/raw CSV, and leaves validation false | Smoke sanitization tests | Manual review remains mandatory |

Audit correction in Phase 0.4C: the earlier smoke output marked
`validated_against_real_prtg: true` after one successful sensor query. It now
reports `smoke_execution_succeeded: true`, keeps
`validated_against_real_prtg: false`, assigns `not_tested`, and requires manual
evidence review. It also requires the expected family and aborts before the
history request when classification differs.

## Fixture compatibility baseline

These rows describe sanitized local fixtures only.

| Family | Fixture source type | Sanitized channels and source units | Canonical result | Historic fixture | Status | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| Ping | `ping` | Ping Time `ms`; Packet Loss `%`; Availability `%` | `latency_ms`, `packet_loss_pct`, `availability_pct` | Semicolon; `Date Time`; naive source timestamps with explicit timezone | `verified_fixture_only` | Real type token, locale and empty intervals unverified |
| CPU | `snmpcpu` | CPU Load `%` | `cpu_usage_pct` | Semicolon; English and Spanish timestamp header fixture; dot/comma decimals | `verified_fixture_only` | WMI/vendor CPU types are not promoted |
| Memory | `snmpmemory` | Memory Usage `%`; Used Memory `MByte` | `memory_usage_pct`, `memory_used_bytes` | Semicolon; naive timestamps | `verified_fixture_only` | Installed memory captions and locale remain unverified |
| Traffic | `snmptraffic` | IN/OUT volume `MByte`; IN/OUT speed `Mbit/s`; utilization `%` | Rate columns become `traffic_in_bps`, `traffic_out_bps`; volume remains non-rate; utilization stays `%` | Semicolon; naive timestamps | `verified_fixture_only` | Counter/volume-to-rate is intentionally unsupported |
| SFP | `snmpcustomadvanced` fixture | RX/TX `dBm`; temperature `°C`; voltage `V`; bias `mA` | Five independent canonical channels; partial RX/TX fixture also works | Semicolon; aware `Z` timestamps | `verified_fixture_only` | Vendor captions, hardware availability and optical channel IDs unverified |

Fixture provenance is recorded in
`tests/fixtures/prtg_discovery/manifest.json`; it explicitly states that no
real PRTG produced those files.

## Real compatibility matrix

No authorized instance was available during this task. All real rows therefore
remain `not_tested`, including SFP.

| Expected family | Real PRTG type | PRTG version | Discovered channels | Source units | Canonical channels | Historic format | Timezone/DST | Status | Sanitized evidence | Limitations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ping | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | `not_tested` | None | No authorized real execution |
| CPU | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | `not_tested` | None | No authorized real execution |
| Memory | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | `not_tested` | None | No authorized real execution |
| Traffic | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | `not_tested` | None | Must prove rate/volume separation |
| SFP | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | Not recorded | `not_tested` | None | No authorized optical hardware confirmed |

For multiple real sensor types in one family, add separate rows. Do not merge
evidence from different PRTG versions, providers, locales, or sensor types.

## Authorization and prerequisites

Before any network request, record outside the repository:

- owner/authorizer and approved time window;
- approved stable CoreView server ID and selected PRTG sensor IDs;
- permission to perform only `table.json`, `table.csv`, and
  `historicdata.csv` GET requests;
- least-privilege read-only username;
- PRTG version, installation timezone, locale, and expected sensor family;
- actual application process count of one;
- rollback/contact procedure, although the validation makes no state change.

Do not proceed if authorization is ambiguous, the certificate cannot be
validated, the URL redirects, the account can modify sensors unnecessarily,
or the selected IDs have not been reviewed.

Runtime-only configuration belongs in ignored `.env`/`servers.json`. The base
URL must be canonical HTTPS with no embedded credentials, query string, or
fragment. Never paste credentials into a shell command, document, screenshot,
or evidence file.

## Controlled smoke sequence

Run one selected sensor at a time. The command performs two metadata GETs
(sensor and channels, normally cached for the following step) and exactly one
historic-data GET. It never calls pause/resume.

```bash
.venv/bin/python scripts/prtg_metrics_smoke.py \
  --server-id <configured-stable-id> \
  --sensor-id <approved-selected-sensor-id> \
  --expected-family ping \
  --points 3 \
  --confirm-read-only-authorized
```

Repeat only for approved sensors, replacing `--expected-family` with `cpu`,
`memory`, `traffic`, or `sfp`. Do not parallelize. Leave at least 15 seconds
between history commands and stop on `upstream_rate_limited`; the configured
sliding budget remains authoritative. Selecting five sensors does not grant
five simultaneous requests.

The JSON output deliberately contains:

- stable server/sensor identity and expected/detected family;
- a sanitized PRTG source-type token;
- source-channel aliases (`channel_1`, etc.), source units, semantic kinds,
  canonical keys, and issue codes;
- delimiter class, column counts, timestamp mode, empty/invalid counts,
  aggregation, and observed UTC range;
- canonical adapter channels, units, counts, issue codes, and unknown count.

It deliberately excludes raw CSV, sample values, source channel/sensor/device
names, hostname, URL, credentials, tags, messages, and full upstream errors.
Output still requires manual review before retention.

## End-to-end parity checklist

For each candidate row, verify sequentially:

1. Discovery returns the configured `server_id` and exact selected
   `sensor_id`; returned PRTG `objid` is not merely the first table row.
2. Expected family equals discovered family. A mismatch is investigated, not
   overridden from the frontend.
3. `/api/v2/metrics/sensor-metadata` returns the same identity and family.
4. `/api/v2/metrics/history` returns only canonical numeric channels with
   canonical units and UTC `Z` timestamps.
5. Traffic speed/rate becomes bps; accumulated volume/counters do not.
6. Empty/invalid source values become issues or absent samples, never zero.
7. The feature-flagged frontend renders the same family/channels and groups
   incompatible units separately.
8. Readings taken at different moments are checked for semantic coherence,
   not exact numeric equality.
9. No legacy API, collector snapshot, MF, pause/resume, or synthetic provider
   behavior changes.

Record browser validation only with sanitized screenshots. A visual check is
not evidence of source parsing unless discovery/history evidence is also
reviewed.

## Localization checklist

Record the actually observed behavior; never add speculative aliases:

- CSV delimiter and quoting;
- decimal separator and thousands separator;
- timestamp header and all channel/RAW header shapes;
- whether timestamps are naive or offset-aware;
- configured source timezone and DST behavior for the sampled date;
- empty cells, missing intervals, error rows, and coverage columns;
- channel RAW availability and relationship to display values;
- requested versus returned aggregation interval;
- rate, volume, counter, gauge, and calculated semantics.

DST validation requires authorized samples around a real transition. Do not
manufacture a DST conclusion from a normal-date smoke.

## Evidence sanitization and review

1. Keep any unavoidable raw capture in a temporary, access-restricted location
   outside the repository. Do not attach or commit it.
2. Replace server, device, probe, group, sensor, channel, customer, and operator
   names with deterministic aliases such as `server_1`, `sensor_ping_1`, and
   `channel_1`.
3. Replace real host/IP information with no address at all or a TEST-NET
   address where a shape example requires one.
4. Remove credentials, cookies, tokens, passhash/password fields, URLs, tags,
   messages, operational descriptions, and query strings.
5. Keep only PRTG version, source type token, structural headers, units,
   timestamp shapes, sanitized IDs/aliases, adapter result, and minimal
   anonymized numeric examples when essential.
6. Review the sanitized file manually for secrets and operational clues.
7. Add it first as a fixture reproducing an observed incompatibility, then add
   a regression test, then make the smallest mapping/parser correction.
8. Run sanitization and full regression suites before changing a matrix row.

Never overwrite a fixture with an unreviewed response. Preserve provenance:
authorization class, capture date, PRTG version, locale/timezone, transformations
performed, reviewer, and the statement that secrets/raw identities were
removed.

## Security validation checklist

- [ ] Provider remains disabled by default.
- [ ] Authorized configuration uses HTTPS and `PRTG_VERIFY_SSL=true`.
- [ ] Invalid/untrusted TLS fails closed; no `verify=false` workaround.
- [ ] Redirect response is rejected rather than followed.
- [ ] URL has no userinfo, query string, or fragment.
- [ ] Logs/output contain no password, passhash, token, authenticated URL, raw
      payload, hostname, customer/device name, or stack trace.
- [ ] Returned `server_id + sensor_id` matches the selected identity.
- [ ] Metadata cache is separated by compound identity.
- [ ] Historic budget is active per server and commands are sequential.
- [ ] Actual deployment uses one application process.
- [ ] Account is read-only and no control endpoint was invoked.

Do not deliberately break a production certificate, redirect, or credential
unless the instance owner explicitly authorizes that negative test. Repository
unit/fixture tests cover these failure paths meanwhile.

## Correction workflow

For an incompatibility:

1. leave the matrix row `not_tested`, `partially_supported`, or `unsupported`;
2. record only sanitized evidence and provenance;
3. reproduce the exact structural behavior with a local fixture;
4. add a failing regression test;
5. update discovery mappings or adapter parsing minimally;
6. run Python, JavaScript, compile, sanitization, and diff checks;
7. repeat the authorized selected-sensor smoke;
8. update the row to `verified_real` only after manual evidence and full-path
   review.

Frontend conditions must not compensate for normalization defects.

## Current result and declaration gate

What was executed for Phase 0.4C preparation:

- implementation audit against source and existing tests;
- local tests for smoke authorization, expected-family mismatch, one-request
  capture, evidence allowlisting, and secret/raw-data omission;
- existing sanitized fixture integration and complete regression suite.

What was not executed:

- no external DNS/HTTP/TLS request;
- no real PRTG login, metadata request, historic request, API parity check, or
  frontend check;
- no TLS-negative test against external infrastructure;
- no real locale, timezone, DST, aggregation, or hardware validation.

Therefore `validated_against_real_prtg` remains `false`. It may become true
only when at least the intended supported matrix has reviewed `verified_real`
rows for the exact deployment claim being made. Unsupported or unavailable
families, including absent SFP hardware, must remain explicit rather than being
silently inferred from another sensor family.
