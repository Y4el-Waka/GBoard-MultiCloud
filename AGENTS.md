# CoreView Cloud Lab - Engineering Instructions

## Project purpose

CoreView Cloud Lab is a sanitized, local-first version of a real monitoring platform originally designed to aggregate multiple PRTG environments into a single operational dashboard.

This repository must remain completely independent from any real company infrastructure.

The project will eventually be used to study and document equivalent deployments on:

* AWS
* Google Cloud Platform
* Microsoft Azure
* Oracle Cloud Infrastructure

The application must remain cloud-agnostic.

## Current architecture

The main components currently are:

* `app.py`: Flask web application, authentication, dashboard routes, APIs, users, auditing and application logic.
* `monitor.py`: monitoring collector and PRTG data processing.
* `datos_simulados.py`: synthetic PRTG simulator.
* `servers.json`: local synthetic PRTG server definitions.
* `templates/`: current UI.
* `static/`: frontend assets.

The synthetic PRTG simulator represents multiple fake PRTG servers and must remain compatible with the subset of the PRTG API consumed by CoreView.

## Critical rules

1. Do not introduce or reconstruct any real company data.
2. Do not add real IP addresses, hostnames, customer names, employee names, credentials, internal domains or infrastructure information.
3. Use synthetic data only.
4. Never commit secrets.
5. Never place credentials directly in source code.
6. Keep `.env`, `servers.json`, runtime databases, logs and generated monitoring state ignored by Git.
7. Preserve `servers.example.json` and `.env.example` as safe examples.
8. Use documentation IP ranges when IP addresses are needed:

   * `192.0.2.0/24`
   * `198.51.100.0/24`
   * `203.0.113.0/24`

## Refactoring philosophy

Do not rewrite the complete application from scratch.

Refactor incrementally.

The current application is working and must remain working after every meaningful change.

Prefer extracting responsibilities from existing files instead of redesigning everything at once.

Avoid unnecessary abstractions.

Do not change the UI unless the task explicitly requests UI changes.

Do not remove existing features simply because they complicate the refactor.

## Current Phase

We are currently working on Phase 0: CoreView Cloud Foundation.

Phase 0.1 is complete:

* Synthetic PRTG environment works.
* Approximately 1,000 synthetic sensors are supported.
* 13 synthetic PRTG environments are supported.
* Existing CoreView UI works with synthetic data.
* Production/company monitoring data has been removed.

The next target is Phase 0.2: backend refactoring.

## Desired Phase 0.2 architecture

Gradually separate responsibilities currently duplicated or embedded inside `app.py` and `monitor.py`.

Target components include:

* `config.py`
* `prtg_client.py`

Potential service modules may later include:

* `services/monitoring_service.py`
* `services/history_service.py`
* `services/auth_service.py`

Do not create all modules simply for architectural appearance. Extract them only when they have a clear responsibility.

### config.py

Centralize:

* environment variables
* paths
* timeouts
* application configuration
* PRTG configuration defaults
* runtime configuration

### prtg_client.py

Centralize communication with PRTG-compatible APIs:

* HTTP sessions
* authentication
* retries
* timeouts
* sensor requests
* device requests
* historic data requests
* pause/resume operations
* API error handling

Both the real PRTG implementation and the synthetic simulator must remain compatible with this client.

## Future Metrics Engine

The current chart/history implementation is biased toward Ping sensors.

This is a known limitation.

Do not solve it using scattered conditions based only on sensor names.

A future metrics layer must support sensor/channel-specific measurements such as:

### Ping

* latency_ms
* packet_loss_pct
* availability

### CPU

* usage_pct

### Memory

* usage_pct
* used_bytes

### Traffic

* traffic_in_bps
* traffic_out_bps
* utilization_pct

### SFP

* rx_power_dbm
* tx_power_dbm
* temperature_c
* voltage_v
* bias_current_ma

The architecture should recognize that a sensor may expose multiple channels with different units.

Keep future support for this model in mind during the current refactor, but do not redesign the UI yet.

## Quality requirements

Before considering a task complete:

* Run relevant tests if present.
* Run Python syntax/import validation.
* Check that the Flask application can start.
* Check that the simulator can start.
* Check that `monitor.py` can consume the simulator.
* Preserve backward compatibility where reasonably possible.
* Review `git diff`.
* Do not leave debugging code.
* Do not silently swallow exceptions.
* Prefer structured and meaningful logging.
* Add documentation when introducing an architectural decision.

## Change discipline

For large tasks:

1. Inspect the existing implementation.
2. Explain the current data flow.
3. Identify dependencies.
4. Produce a concrete implementation plan.
5. Make incremental changes.
6. Validate each meaningful step.
7. Review the complete diff.
8. Summarize changes, risks and remaining work.

Do not perform a massive rewrite in one step.

## UI

The current UI is intentionally temporary.

Preserve its behavior and appearance during backend refactoring.

A dedicated UI/UX redesign will happen later.

## Ultimate objective

The repository should eventually provide one application that can be deployed through equivalent architectures on AWS, GCP, Azure and OCI while keeping the application layer portable.

Core principle:

**One application. Four cloud architectures.**
