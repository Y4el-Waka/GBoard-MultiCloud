# Phase 0 Audit — CoreView Cloud Foundation

## Goal

Convert the existing production-derived CoreView codebase into a portable, reproducible and publishable local lab before implementing any cloud provider architecture.

## Current baseline

- `app.py`: Flask web application, authentication, local users, audit, PRTG administration, history, pause/resume operations and dashboard APIs.
- `monitor.py`: multi-PRTG collector, status normalization, event persistence, MF SLA state, metrics window and atomic JSON output.
- SQLite is currently shared by application and collector responsibilities.
- Runtime sensor state is exchanged through `estado_sensores_prtg.json`.

## Findings

1. The removed production server configuration was not the only sensitive artifact. The previous runtime state JSON contained production-derived names and network addresses. Runtime state must never be versioned.
2. `app.py` is large and owns several unrelated responsibilities. Refactoring should be incremental to avoid changing behavior while the synthetic lab is being established.
3. PRTG access exists in both `app.py` and `monitor.py`. A future shared PRTG client/provider layer will reduce duplication.
4. Configuration is spread across module-level constants and environment variables in both processes. A shared configuration module is a good next refactor.
5. The current JSON boundary between collector and web application is useful for the first local lab because it lets the frontend remain unchanged while the data source is replaced.

## Phase 0.1 decision

Do not rewrite CoreView first. Emulate the PRTG endpoints already consumed by the application and collector.

`datos_simulados.py` provides synthetic logical PRTG instances. The existing collector can therefore exercise its real normalization, persistence, event and MF logic without access to production infrastructure.

## Next refactor targets

1. `config.py`: centralize environment parsing, paths and shared defaults.
2. `prtg_client.py`: centralize authentication, HTTP sessions, table/history calls and pause/resume operations.
3. Split database concerns from `app.py` and `monitor.py` without changing the schema yet.
4. Add automated tests around status normalization, MF SLA and the synthetic PRTG contract.
5. Containerize only after the local Python workflow is deterministic.
