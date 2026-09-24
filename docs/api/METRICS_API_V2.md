# Metrics History API v2

> **Experimental - Phase 0.3**

This opt-in API exposes normalized multichannel history without changing the
legacy `/api/history` contract or the current dashboard.

## Endpoint and authentication

```http
GET /api/v2/metrics/history
```

The endpoint uses the existing CoreView session authentication. Requests
without a valid session receive the same `401` response used by the existing
monitoring APIs. API keys are not supported in this phase.

## Query parameters

All requests require:

| Parameter | Meaning |
| --- | --- |
| `server` | Server identifier accepted by current CoreView lookup (currently its configured display name). |
| `sensor_id` | Source sensor identifier. |
| `sensor_type` | Canonical family: `ping`, `cpu`, `memory`, `traffic`, or `sfp`. |

Exactly one query mode is required.

### Points mode

```http
GET /api/v2/metrics/history?server=PRTG%20LAB%2001&sensor_id=100001&sensor_type=traffic&points=24
```

`points` must be an integer from 1 through 1000. It cannot be combined
with `start` or `end`.

### Range mode

```http
GET /api/v2/metrics/history?server=PRTG%20LAB%2001&sensor_id=100001&sensor_type=traffic&start=2026-09-23T12%3A00%3A00Z&end=2026-09-23T18%3A00%3A00Z
```

`start` and `end` must both be valid ISO-8601 timestamps with an explicit
timezone, and `start` must be earlier than `end`. Naive timestamps are
rejected. The provider receives UTC-aware boundaries. The response range is
the observed range, which can be narrower than the requested range.

## Status codes

| Status | Meaning |
| --- | --- |
| `200` | Normalized result. Partial channels, non-fatal issues, and unknown channels are successful results. |
| `400` | Invalid query, incompatible modes, unsupported sensor type, or invalid range. |
| `401` | No valid CoreView session. |
| `404` | Configured server or source sensor not found. |
| `502` | Provider/source failure, including an unconfigured normalized provider. |
| `500` | Unexpected application failure; no upstream detail or stack trace is exposed. |

Error codes are `invalid_request`, `server_not_found`,
`sensor_not_found`, `upstream_error`, and `internal_error`.

## JSON contract

```json
{
  "schema_version": "2.0",
  "sensor": {
    "server_id": "srv-prtg-01",
    "sensor_id": "100001",
    "sensor_type": "traffic"
  },
  "range": {
    "start": "2026-09-23T12:00:00Z",
    "end": "2026-09-23T12:05:00Z"
  },
  "channels": {
    "traffic_in_bps": {
      "label": "Traffic in",
      "unit": "bps",
      "semantic": "rate",
      "value_type": "number",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": 85000000}
      ]
    }
  },
  "issues": [],
  "unknown_channels": []
}
```

Channel keys and units are canonical. Values are JSON numbers, not formatted
strings. Timestamps are chronological ISO-8601 UTC values ending in `Z`.
Labels, semantics, and value types come from the metrics domain definitions.
There is no Chart.js, CSS, axis, or color configuration.

## Ping example

```json
{
  "channels": {
    "latency_ms": {
      "label": "Latency",
      "unit": "ms",
      "semantic": "gauge",
      "value_type": "number",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": 12.5},
        {"timestamp": "2026-09-23T12:05:00Z", "value": 14.0}
      ]
    },
    "packet_loss_pct": {
      "label": "Packet loss",
      "unit": "%",
      "semantic": "gauge",
      "value_type": "number",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": 0.0},
        {"timestamp": "2026-09-23T12:05:00Z", "value": 1.0}
      ]
    }
  }
}
```

## Traffic example

Traffic directions remain separate:

```json
{
  "channels": {
    "traffic_in_bps": {
      "unit": "bps",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": 85000000}
      ]
    },
    "traffic_out_bps": {
      "unit": "bps",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": 22000000}
      ]
    }
  }
}
```

## Partial SFP example

Missing optional channels are omitted rather than treated as an error:

```json
{
  "channels": {
    "rx_power_dbm": {
      "unit": "dBm",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": -3.8}
      ]
    },
    "tx_power_dbm": {
      "unit": "dBm",
      "samples": [
        {"timestamp": "2026-09-23T12:00:00Z", "value": -2.0}
      ]
    }
  }
}
```

## Issues and unknown channels

Non-fatal source problems remain observable without fabricating zeros:

```json
{
  "issues": [
    {
      "code": "invalid_value",
      "channel": "Temperature",
      "channel_index": 2,
      "message": "channel value is not a finite numeric observation"
    }
  ],
  "unknown_channels": [
    {
      "source_channel": "Laser Health Index",
      "source_unit": "%",
      "channel_index": 5,
      "reason": "unmapped_channel"
    }
  ]
}
```

Only allowlisted fields are serialized. Raw source payloads, credentials,
authenticated URLs, and stack traces are never included.

## Current limitations

- A normalized provider must be injected into `create_app()`. With no
  provider, this endpoint returns `502` while legacy features keep working.
- Phase 0.3E validates the synthetic provider path; a real PRTG-backed
  multichannel provider is future work.
- There is no normalized cache. The legacy cache is unchanged and separate.
- Counter-to-rate, aggregation, resampling, and normalized storage are absent.
- The dashboard does not call this endpoint.
- Request-side `server` lookup still uses the configured name; response
  identity always uses the configured stable `server_id`.
