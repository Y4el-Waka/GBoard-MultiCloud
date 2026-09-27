# ADR-012: Shared synthetic sensor catalog

- Status: Accepted
- Date: 2026-09-24
- Phase: 0.3F.3

## Context

The legacy simulator selected names from an eight-entry list that restarted on
every server. The multichannel simulator separately selected canonical
families from a global ten-entry cycle. Manual frontend validation exposed the
result: a legacy Ping card could resolve to memory metadata, while a CPU card
could return traffic history.

An exhaustive baseline comparison found 809 mismatches among 1,000 sensors.
Only 191 legacy labels agreed with the multichannel family. The legacy set also
contained 234 HTTP/SNMP Uptime sensors with no supported Phase 0.3 family.
Numeric sensor IDs happened to be globally unique, but neither family
algorithm was keyed by the canonical `(server_id, sensor_id)` identity.

The simulator also emitted `srv-prtg-XX`, while the documented laboratory
configuration uses `srv_lab_XX`; the provider did not validate that source
server ID before HistoryService rebound samples to the configured identity.

## Decision

Create `SyntheticSensorCatalog`, keyed by `(server_id, sensor_id)`, before
building either representation. Each immutable entry contains:

- stable server ID and simulator slug;
- sensor ID;
- one canonical family;
- one compatible legacy display name;
- one primary PRTG-like source channel.

A global ten-entry pattern preserves the established distribution of 400 Ping,
200 CPU, 100 memory, 200 traffic, and 100 SFP sensors. The legacy simulator
uses catalog names. After multichannel profiles are built, each legacy
`lastvalue` is copied from its declared primary channel at tick zero. The
multichannel simulator must look up every profile in the catalog and has no
fallback family cycle.

Simulator server IDs are the documented `srv_lab_01` through `srv_lab_13`.
The synthetic provider rejects current/history metadata whose `server_id` does
not match the selected configured server. Equal numeric sensor IDs on distinct
servers remain valid and isolated because catalog lookups require both keys.

The legacy JSON field set, collector snapshot schema, SQLite, MF logic,
authentication, APIs, and frontend chart selection are unchanged. Legacy Ping
CSV remains a compatibility path; normalized multichannel history is the
family-aware historical contract.

## Alternatives considered

### Infer canonical family from the legacy name

Rejected. It would reintroduce mutable-name heuristics and duplicate mapping
logic in providers or frontend code.

### Rename only the mismatching cards

Rejected. Two independent algorithms could diverge again after topology or
distribution changes.

### Add family to the legacy public JSON

Rejected. The catalog can guarantee synthetic consistency without changing the
frozen legacy table or snapshot schemas.

### Ignore simulator `server_id`

Rejected. Rebinding a response from another server would defeat composite
identity and allow cross-server contamination when numeric IDs overlap.

## Consequences

Positive consequences:

- card label, legacy value, metadata, history, and chart family agree;
- all five supported families exist in both synthetic representations;
- distribution, stable IDs, seed reproducibility, clock, and scenarios remain;
- normal memory byte samples no longer acquire fractional-byte adapter issues;
- cross-server source identity mismatches fail safely.

Tradeoffs:

- synthetic legacy names and values deliberately changed from the Phase 0.1
  fixture content;
- the initial legacy value corresponds to logical tick zero, while later
  snapshot/history reads can represent different ticks and need not be
  numerically identical;
- this catalog is laboratory-only and must not become a production PRTG
  classification heuristic.
