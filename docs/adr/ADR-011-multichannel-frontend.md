# ADR-011: Multichannel frontend integration

- Status: Accepted
- Date: 2026-09-24
- Phase: 0.3F.2

## Context

Phase 0.3F.1 completed the normalized path from the laboratory simulator to
`GET /api/v2/metrics/history`, but the dashboard still used only the legacy
Ping-oriented `/api/history` payload. Enabling v2 implicitly would make the
temporary laboratory provider a production default and would remove the
known-good rollback path.

The legacy collector snapshot does not contain a canonical sensor family. Its
sensor and server display names are mutable and cannot safely determine
whether a sensor is Ping, CPU, memory, traffic, or SFP. The v2 history API
requires this explicit family before it can normalize source channels.

The existing history modal owns pause/resume controls and a Chart.js lifecycle.
The smallest compatible change is to reuse that modal while keeping normalized
acquisition and presentation separate from the legacy chart parser.

## Decision

### Explicit feature flag

Add the typed application setting `COREVIEW_METRICS_UI_ENABLED`, disabled by
default. Flask renders only its boolean result as
`data-metrics-ui-enabled="true|false"` on the dashboard body. JavaScript never
reads environment variables or provider configuration.

When disabled, `dashboard.js` makes no v2 request and executes the unchanged
legacy history path. The provider and UI flags are intentionally independent:
an operator must explicitly enable both the synthetic provider and the UI.

### Explicit sensor metadata

Add the authenticated endpoint:

```text
GET /api/v2/metrics/sensor-metadata?server=...&sensor_id=...
```

It delegates to `HistoryService.get_normalized_sensor_metadata()`, which asks
the configured source for explicit metadata and returns the configured stable
`server_id`, source `sensor_id`, and canonical `sensor_type`. The synthetic
provider obtains those fields from the existing localhost-only
`multichannel.json` endpoint. No sensor-name matching is used.

The source method returns only allowlisted identity/type fields. PRTG-shaped
channel names, credentials, URLs, and full source payloads are not exposed.

The Phase 0.3C laboratory overlay deliberately assigns canonical families by
stable sensor ID rather than by the mutable legacy card label. Consequently,
the label on a legacy card is not evidence of its v2 family. The modal always
shows the resolved canonical family in its subtitle, and laboratory operators
must discover compatible IDs through the simulator inventory. Aligning legacy
labels with normalized families would require a separate synthetic-topology
migration and is not part of this frontend increment.

### Frontend boundary

Create `static/metrics-history.js`, a framework-free module that owns:

- metadata and history URL construction;
- same-origin requests and response validation;
- one active `AbortController` plus stale-response generation checks;
- metadata caching per server/sensor lookup;
- canonical channel-to-presentation grouping;
- visual unit formatting;
- Chart.js configuration and deterministic cleanup;
- the allowlisted fallback decision.

`dashboard.js` remains the modal orchestrator. It owns DOM state, creates
canvases using `createElement`, creates/destroys Chart.js instances, and keeps
pause/resume behavior in the existing application layer.

### Query mode and time presentation

The dashboard requests exactly 24 source points. It labels this as points,
never as 24 hours. The API's observed UTC range is displayed in the browser's
local timezone. The reusable client also supports explicit `start/end`, but
never permits mixing range and points parameters.

### Channel grouping

The UI consumes only canonical channel keys and API metadata:

- Ping: latency (`ms`) and packet loss/availability (`%`) are separate;
- CPU: utilization (`%`);
- memory: utilization (`%`) and used memory (`bytes`) are separate;
- traffic: IN/OUT share `bps`; utilization has a separate `%` chart;
- SFP: RX/TX share `dBm`; temperature, voltage, and bias current each use a
  unit-compatible chart.

Byte and bit-rate values remain unchanged in chart data. KB/MB/GB and
Kbps/Mbps/Gbps conversions occur only in ticks and tooltips. No universal SFP
thresholds are introduced.

Missing optional channels are omitted. Issues, unknown channels, and missing
canonical channels produce a discrete partial-data message without blocking
valid siblings or inventing zero values.

### Fallback policy

Fallback to `/api/history` is permitted only when:

- the UI flag is disabled;
- the configured normalized provider returns `502 upstream_error`;
- v2 returns `404 server_not_found` or `404 sensor_not_found`;
- an active modal has already selected legacy fallback for one of those
  reasons.

The modal identifies legacy mode. A fallback decision is retained for the
active sensor so the 30-second refresh does not repeatedly probe a failed
provider. Invalid requests, authentication failures, contract violations, and
unexpected server errors remain visible and are not hidden behind legacy data.

### UI states and lifecycle

The reused modal has explicit loading, success, empty, partial, and error
states. Selecting another sensor, closing the modal, or refreshing cancels or
invalidates older work. Every legacy or normalized Chart.js instance is
destroyed before its canvas is reused or removed.

Dynamic diagnostic text is assigned with `textContent`. Source messages,
stack traces, credentials, URLs, and raw payloads are not rendered.

## Alternatives considered

### Infer the family from sensor names

Rejected because names are mutable, provider-specific, and not canonical.

### Add `sensor_type` to the legacy snapshot

Rejected because it would change the collector compatibility contract solely
for an opt-in UI feature.

### Try each sensor type until one succeeds

Rejected because it creates ambiguous traffic, hides invalid requests, and
multiplies upstream work.

### Replace `/api/history`

Rejected because it removes the rollback path and couples legacy Ping charts
to the experimental contract.

### Put Chart.js configuration in the API

Rejected because colors, axes, scaling, and browser-local time are
presentation concerns.

### Introduce a frontend framework or chart library

Rejected because the existing dashboard and Chart.js are sufficient for this
increment and a full redesign remains out of scope.

## Consequences

Positive consequences:

- all five metric families can be inspected from the current modal;
- traffic directions and incompatible units remain separate;
- the legacy dashboard remains the default and rollback path;
- sensor family resolution uses explicit provider metadata;
- overlapping requests and duplicate Chart.js instances are controlled;
- frontend tests use Node's built-in test runner with no package dependency.

Tradeoffs and remaining work:

- the normalized UI requests a fixed 24 points; a richer range selector is
  deferred to the future dashboard redesign;
- metadata is cached only in the browser session, while normalized history is
  intentionally uncached;
- the metadata endpoint is experimental alongside the v2 history endpoint;
- only the laboratory synthetic provider implements metadata acquisition;
- Phase 0.3C's stable-ID overlay can expose a canonical family different from
  a legacy card's mutable display label; the modal identifies the resolved v2
  family explicitly;
- actual real-PRTG provider behavior, persistent multichannel storage,
  aggregation, and counter-to-rate remain outside Phase 0.3;
- visual validation still depends on the browser/CDN availability used by the
  current dashboard.

### Frontend integrity baseline

Phase 0.3F.2 intentionally changes the history modal, its controller, and its
styles. After automated and browser validation, the frozen SHA-256 baseline is:

- `templates/dashboard_prtg.html`: `77775dc04e573366a5281e3db5118315817734109d1c80d16f00b4240ebd95c2`;
- `static/dashboard.js`: `9ad78ad6747f7e8bd7b2017b60807c1d2972c55fb1caa61633a81ed8d6ebbc21`;
- `static/dashboard.css`: `cc5bd7666368d69610dd45105667c6a65d0ad5b2244f2874505b8f795324791b`;
- `static/metrics-history.js`: `3ac3e169dd8e3f620ba5ea81ff5c5ca35e3d8cdca69b1a6eb27b598474bf0f7c`.

The unchanged login template and background retain their previous hashes. The
integrity test remains active and now also covers the new frontend client.

### Executed visual validation

The localhost laboratory was exercised with 13 synthetic servers, 1000 legacy
sensors, the synthetic normalized provider, and an authenticated Flask
session. Browser checks covered Ping, CPU, memory, bidirectional traffic, full
SFP, partial SFP, rapid sensor changes, closing/reopening the modal, and the
feature flag disabled. The disabled flag produced the visible `Historico
legacy` mode and no v2 chart panels. The browser console contained no errors.
All screenshots used synthetic TEST-NET data only.
