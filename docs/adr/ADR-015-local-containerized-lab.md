# ADR-015: Local containerized synthetic laboratory

- Status: Accepted
- Date: 2026-09-27
- Phase: 0.5A

## Context

CoreView already runs locally as three independent processes: the synthetic
PRTG-compatible simulator, the collector, and the Flask application. Their
runtime state was historically placed beside the source tree and startup
depended on three manually ordered terminals.

The normalized laboratory provider deliberately accepts only localhost or a
loopback IP. A conventional Compose bridge would give the simulator a service
DNS name and non-loopback address. Relaxing the provider to accept that name
would turn a narrowly constrained source into a more general outbound HTTP
client and weaken ADR-010.

The current modules also have lifecycle constraints that container composition
must respect:

- importing `datos_simulados.py` constructs the deterministic synthetic
  topology;
- importing `monitor.py` loads environment-derived settings;
- importing `app.py` loads settings and `app = create_app()` initializes
  SQLite, users, and server configuration;
- the collector has no HTTP endpoint, so liveness alone cannot prove it has
  produced a valid snapshot;
- the application health endpoint can be reachable before the collector is
  stable unless composition enforces ordering.

## Decision

Build one immutable, non-root Python image and run it as three Compose services:

- `simulator` runs `datos_simulados.py` with 13 servers, 1,000 sensors, a fixed
  seed, and its existing health endpoint;
- `collector` runs `monitor.py` and writes SQLite/snapshot state to a named
  volume;
- `app` runs the existing WSGI object through one Gunicorn worker with threads.

The services remain separate containers, but `collector` and `app` use
`network_mode: service:simulator`. They therefore share the simulator's network
namespace and reach it at the unchanged `127.0.0.1:9090` URLs. The synthetic
provider's loopback validation remains unmodified. The namespace attaches to a
dedicated Compose bridge, and host publication is restricted to
`127.0.0.1:9090` and `127.0.0.1:5067`.

Healthchecks represent availability rather than process existence:

1. simulator health requires the synthetic marker and exact 13/1,000 topology;
2. collector health requires a recent parseable snapshot with 13 configured
   servers, 1,000 sensors, and zero server errors;
3. application health requires its HTTP endpoint, 13 configured servers, and
   an existing collector snapshot.

Compose health dependencies start the collector after the simulator is ready
and Flask after a successful collector cycle. `docker compose up --wait` is the
synchronization mechanism; arbitrary startup sleeps are not used.

SQLite and the collector snapshot share the `coreview-runtime` named volume.
User alert uploads use `coreview-uploads`. Simulator metrics remain a pure
function of seed, catalog, scenario, and logical tick; no simulator volume is
needed, and its in-memory pause/tick state intentionally resets on simulator
restart. Operational logs use Docker's bounded `local` logging driver rather
than application log files in the source tree.

The versioned synthetic server configuration is mounted read-only at runtime
and is not copied into the image. Application secret and laboratory admin
password are required from the invoking environment and are not build
arguments or image layers. The real PRTG provider remains disabled;
`COREVIEW_METRICS_PROVIDER=synthetic` and the multichannel UI flag are explicit.

Every service uses a read-only root filesystem, UID/GID 10001, no added Linux
capabilities, `no-new-privileges`, an ephemeral constrained `/tmp`, and a
dedicated runtime bridge. The provider and smoke independently reject every
non-loopback destination. This is reasonable laboratory hardening, not a
production security claim.

## Alternatives considered

### Run all processes in one container under a shell or supervisor

Rejected because process health, restart policy, logs, and failure ownership
would be coupled. The current processes already have independent entrypoints.

### Use the normal Compose bridge and `http://simulator:9090`

Rejected because it requires weakening the loopback-only provider policy or
adding a Docker-specific destination exception to production source code.

### Use host networking

Rejected because behavior differs by platform, port exposure is harder to
constrain, and it provides a broader network surface than a private shared
namespace.

### Permit an arbitrary configurable simulator allowlist

Rejected for this phase. It expands SSRF policy and would need a new trusted
destination model. Sharing the namespace satisfies the existing contract
without product-code changes.

### Bind-mount the repository for live development

Rejected as the reproducible default because host files and permissions could
affect runtime. Local `.venv` development remains supported separately.

### Persist simulator logical state

Rejected because current simulator behavior is deterministic but intentionally
in-memory. Adding persistence would change its semantics and is unnecessary for
collector/database restart durability.

## Consequences

Positive consequences:

- a clean checkout can build and start the complete synthetic laboratory with
  Docker Compose;
- loopback enforcement remains intact and is covered independently from Docker
  networking;
- startup waits for meaningful service readiness;
- SQLite, snapshot, uploads, and logs no longer depend on repository runtime
  files;
- normal restart/stop preserves named volumes, while destructive cleanup is an
  explicit separate command;
- the same smoke validates authentication, collector output, legacy history,
  five normalized families, partial SFP, pause/resume, and persistence.

Tradeoffs and limitations:

- sharing a network namespace means published ports belong to the `simulator`
  service even though port 5067 is served by the `app` container;
- the server-admin UI cannot modify the read-only synthetic server fixture;
- simulator pause/tick state resets when the simulator container restarts;
- SQLite is suitable for this single-host lab, not horizontal scaling;
- frontend libraries currently loaded from public CDNs still require browser
  network access for a complete uncached visual experience;
- build-time package installation requires access to the configured Python
  package index;
- no compatibility with real PRTG or production hardening is implied.
