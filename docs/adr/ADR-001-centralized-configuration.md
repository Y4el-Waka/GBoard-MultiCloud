# ADR-001: Centralized configuration

- Status: Accepted
- Date: 2026-09-23
- Phase: 0.2B

## Context

`app.py` and `monitor.py` independently loaded `.env`, parsed the same environment
variables, resolved runtime paths, configured HTTP retry pools, and loaded the
same PRTG server definitions. The implementations had already drifted in a few
observable ways:

- `REQUEST_TIMEOUT` defaulted to 20 seconds in the application and 10 seconds in
  the monitor.
- Boolean parsing was not identical. In particular, the application accepted
  `y` for environment booleans while the monitor did not.
- The application added valid server records to its mapping before evaluating
  `enabled` and `type`, while the monitor excluded disabled and non-PRTG records.
- Relative runtime paths could depend on the process working directory in some
  code paths and on the repository directory in others.

Phase 0.2B must remove configuration duplication without changing HTTP behavior,
the public API, monitoring output, persistence, or UI behavior.

## Decision

Create `config.py` as the single module responsible for configuration loading,
typed parsing, path resolution, and PRTG server configuration parsing.

The module exposes frozen dataclasses for shared, application, monitor, and HTTP
settings. `app.py` and `monitor.py` load their own profile once during import and
retain module-level aliases for the names used by their existing logic. This
keeps the migration incremental and avoids a broad rewrite.

`.env` loading is explicit through `load_environment()`. It uses the project
`.env` file when `python-dotenv` is available and preserves already-set process
environment variables. Merely importing `config.py` does not load `.env`, which
allows tests to pass isolated environment mappings directly to the loaders.

The application and monitor retain separate profile defaults. Most importantly,
their effective request timeouts remain 20 and 10 seconds respectively. Their
historical boolean parsing differences are also retained where changing them
could alter behavior.

Relative paths for `DB_PATH`, `OUTPUT_PATH`, and `PRTG_SERVERS_FILE` are resolved
against `BASE_DIR`, the project/runtime directory containing `config.py`.
Absolute paths are preserved unchanged. This makes path behavior independent of
the shell working directory. Deployments that intentionally relied on a relative
path being resolved from another working directory must switch to an absolute
path during this transition.

Server configuration uses the existing precedence: a non-blank
`PRTG_SERVERS_JSON` value wins over `PRTG_SERVERS_FILE`; otherwise the configured
file is read. Password and passhash authentication forms remain accepted. File
normalization and atomic writes remain available for the application's server
management routes.

Two explicit server-loader profiles preserve the current discrepancy:

- The monitor filters disabled and non-PRTG records.
- The application retains its historical behavior of exposing otherwise valid
  disabled and non-PRTG records as enabled PRTG entries.

This discrepancy is compatibility debt. It must be resolved only as a separately
approved behavior change with updated characterization tests.

`config.py` contains no Flask routes, HTTP requests, monitoring business logic,
history parsing, MF processing, or pause/resume operations.

## Alternatives considered

### Keep configuration duplicated

Rejected because it permits defaults and validation rules to drift further and
makes the next client extraction riskier.

### Force one shared set of defaults and server rules

Rejected because it would silently change current behavior, especially request
timeouts and disabled/non-PRTG server handling.

### Move directly to a framework or dependency-injection configuration system

Rejected as unnecessary complexity for the current application and contrary to
the incremental refactoring requirement.

### Combine configuration centralization with the PRTG client extraction

Rejected because separating those changes makes regressions easier to locate and
keeps Phase 0.2B limited to configuration.

## Consequences

Positive consequences:

- Configuration parsing and server-source precedence have one tested home.
- Settings can be tested with synthetic environment mappings and temporary paths
  without reading runtime files.
- `app.py` and `monitor.py` preserve their existing public globals and effective
  defaults while consuming typed settings.
- Runtime paths are predictable regardless of the current working directory.

Tradeoffs and remaining debt:

- Settings are still created at module import time to preserve the existing
  application lifecycle. Dynamic environment changes require a module reload or
  an explicit settings load.
- The two server-loader compatibility profiles intentionally duplicate a small
  amount of normalization until their behavioral discrepancy is resolved.
- The insecure development secret default remains for backward compatibility;
  production-like deployments must set `APP_SECRET_KEY` or `SECRET_KEY`.
- HTTP sessions, authentication parameters, request execution, retry adapters,
  historical requests, and pause/resume calls remain in `app.py` and
  `monitor.py`. They are candidates for the separately scoped Phase 0.2C PRTG
  client extraction.
