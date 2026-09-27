# Synthetic Sensor Consistency Report

## Baseline diagnosis

Phase 0.3F.2 validation compared every legacy sensor with its multichannel
profile. Results before remediation:

| Measure | Result |
| --- | ---: |
| Sensors inspected | 1,000 |
| Family matches | 191 |
| Family mismatches | 809 |
| Duplicate numeric sensor IDs | 0 |
| Legacy sensors outside the five canonical families | 234 |

Legacy semantic counts were 247 Ping, 130 CPU, 130 memory, 259 traffic, and
234 unsupported HTTP/SNMP Uptime. The independent multichannel cycle produced
400 Ping, 200 CPU, 100 memory, 200 traffic, and 100 SFP.

Representative mismatches included:

- `srv-prtg-01/100002`: legacy Memory, multichannel Ping;
- `srv-prtg-01/100004`: legacy Traffic Out, multichannel CPU;
- `srv-prtg-01/100009`: legacy CPU Load, multichannel SFP.

Metadata v2 read the multichannel result, so it faithfully exposed the wrong
relationship rather than causing it. The frontend also behaved correctly: it
trusted explicit metadata and revealed the simulator inconsistency.

## Root cause

`datos_simulados.py` used an eight-entry name cycle reset per server.
`simulator_multichannel.py` used a separate global ten-entry family cycle.
There was no shared catalog or invariant joining legacy name, primary value,
metadata family, and historical channels. Source server IDs also differed from
the IDs in `servers.example.json` and were not validated by the provider.

## Remediation

The shared `SyntheticSensorCatalog` now owns the immutable composite identity,
family, display name, and primary channel. Both simulators consume it. Normal
legacy values originate from the same tick-zero channel formulas used by
multichannel history. The provider enforces configured source server identity.

The resulting distribution remains:

| Family | Sensors |
| --- | ---: |
| Ping | 400 |
| CPU | 200 |
| Memory | 100 |
| Traffic | 200 |
| SFP | 100 |

## Temporal interpretation

The initial legacy `lastvalue` equals the catalog primary channel at logical
tick zero. A collector snapshot and a requested historical window can observe
different ticks, so numeric equality is not promised across different times.
Identity, family, channel semantics, and units must remain equal.

## Regression coverage

Automated validation now:

- traverses all 1,000 catalog entries;
- compares legacy name/value with current and historical multichannel data;
- adapts historical samples and verifies family-compatible canonical keys;
- proves Ping cannot return memory channels and CPU cannot return traffic;
- preserves deterministic catalog/distribution across seeds;
- proves equal numeric IDs on two servers stay isolated;
- executes real localhost legacy API → collector → snapshot → metadata v2 →
  history v2 integration for all five families.
