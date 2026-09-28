# Docker Compose Synthetic Laboratory

Phase 0.5A packages the existing synthetic CoreView laboratory without changing
the legacy APIs, metrics contracts, collector, simulator semantics, or
frontend. It is intended for local development and demonstrations only.

Phase 0.5B fixes the base image and Python artifacts, adds CI, CycloneDX SBOM,
Trivy scanning, and measured resource evidence. See
`docs/CONTAINER_SUPPLY_CHAIN.md` for the exact digests, policy, and update
procedure.

Phase 0.5C builds all browser assets locally, tightens CSP, adds a real
external-network-blocked Chromium smoke, and provides an isolated one-hour
resource/health soak. See `docs/OFFLINE_FRONTEND.md`.

## Prerequisites

- Docker Engine 24 or newer;
- Docker Compose v2 with `--wait` support;
- ports `127.0.0.1:5067` and `127.0.0.1:9090` available;
- network access while obtaining pinned base images/dependencies for a cold
  build (normal browser runtime itself is offline-capable).

Docker is optional for normal development. The `.venv` procedure in `README.md`
continues to work.

## Architecture

```text
host 127.0.0.1:9090 ─┐
                      │       one private network namespace
host 127.0.0.1:5067 ─┼─ simulator container: Flask simulator :9090
                      ├─ collector container: monitor.py -> /runtime
                      └─ app container: Gunicorn/Flask :5067 -> /runtime

coreview-runtime named volume: SQLite + collector snapshot
coreview-uploads named volume: optional user alert sounds
Docker local log driver: bounded per-service operational logs
```

`app` and `collector` use `network_mode: service:simulator`. Consequently the
existing server URLs remain `http://127.0.0.1:9090/prtg-XX`, and
`SyntheticHistoricalMetricsSource` retains its strict loopback validation. The
runtime bridge is dedicated to this Compose project, and no container port is
bound to a public host interface. Docker requires a non-internal bridge for
host port forwarding; provider and smoke policy still reject non-loopback
destinations even though the container namespace itself has normal bridge
routing.

## Audited entrypoints and runtime behavior

| Component | Entrypoint | Readiness evidence | Import/startup behavior |
| --- | --- | --- | --- |
| Simulator | one-worker Gunicorn serving `datos_simulados:app` | `/healthz` reports synthetic, 13 servers, 1,000 sensors | Import constructs the deterministic topology from fixed environment settings; access logging is disabled so legacy credential queries are not recorded |
| Collector | `python -u monitor.py` | fresh snapshot with 13 servers, 1,000 sensors, zero errors | Import loads `.env`/settings; process initializes monitoring tables and loops every five seconds in Compose |
| CoreView | `gunicorn ... app:app` | `/healthz` plus available collector snapshot | Import loads settings and `create_app()` initializes SQLite, configured users, and server mappings |

The 13 PRTG-compatible definitions are the reviewed synthetic fixture
`docker/servers.synthetic.json`. It is mounted read-only, contains only
loopback URLs and synthetic credentials, and is deliberately not copied into
the image.

Known compatibility constraints before containerization were:

- the synthetic provider rejects bridge-service DNS names;
- the collector exposes no health endpoint;
- Flask health alone does not prove the collector has stabilized;
- SQLite and snapshot paths default beside source code;
- `app = create_app()` has import-time database side effects;
- the simulator's logical clock and pause state are in-memory;
- the existing fixed dark/card UI has no selectable light/list modes.

The Compose design handles the runtime boundaries without changing production
modules. Remaining application lifecycle and UI-mode constraints are
documented limitations.

## Required runtime secrets

Compose refuses to render unless both values are present. Set them in the
current shell; do not put them in a tracked file or shell history.

```bash
export COREVIEW_LAB_APP_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
read -rsp 'Synthetic lab admin password: ' COREVIEW_LAB_ADMIN_PASSWORD
echo
export COREVIEW_LAB_ADMIN_PASSWORD
```

The password must be at least eight characters. It is a local synthetic-lab
credential, not a real or reused password. Compose passes both values only at
runtime; the Docker build has no secret build arguments.

## Validate, build, and start

```bash
docker compose config --quiet
docker compose up --detach --build --wait --wait-timeout 180
docker compose ps
```

Startup order is readiness-driven:

1. simulator topology becomes healthy;
2. collector starts and must publish a current zero-error snapshot;
3. Flask/Gunicorn starts and must serve health with that snapshot available.

There is no startup `sleep` dependency.

Open [http://127.0.0.1:5067](http://127.0.0.1:5067) and log in as
`lab-admin` with the password supplied above. The normalized provider and UI
are explicitly enabled. The real PRTG provider is not configured.

## Automated smoke

Run from the repository while the Compose project is healthy:

```bash
.venv/bin/python scripts/docker_lab_smoke.py
```

The smoke discovers valid IDs from the simulator rather than hardcoding them.
It verifies:

- simulator 13/1,000 topology;
- authenticated session and dashboard;
- collector snapshot with zero errors;
- metadata and normalized history for Ping, CPU, memory, traffic, and SFP;
- simultaneous Traffic IN/OUT in bps;
- a partial SFP without invented channels;
- numeric values and ordered UTC timestamps;
- legacy Ping history;
- synthetic pause and resume;
- a SQLite pin used as a persistence marker.

It accepts only loopback application/simulator URLs and disables proxy use.
Missing components produce a nonzero exit with the failed boundary.

Generate the SBOM and vulnerability evidence after the image is built:

```bash
bash scripts/container_supply_chain.sh artifacts/container
```

The artifacts are local/CI outputs and are intentionally ignored by Git.

### Offline Chromium smoke

After the services are healthy, validate the actual UI with external network
access blocked:

```bash
scripts/run_offline_frontend_smoke.sh
```

This creates a temporary Docker `--internal` network and a digest-pinned
Playwright/Chromium container. It exercises login, views, all five normalized
families, partial SFP, legacy fallback, filters, and pause/resume. Screenshots
and the machine-readable result are written under
`artifacts/offline-frontend/`. The temporary network is removed even on
failure. Exact scope and limitations are in `docs/OFFLINE_FRONTEND.md`.

### One-hour soak and resource report

```bash
.venv/bin/python scripts/docker_lab_soak.py \
  --duration 3600 \
  --interval 30 \
  --output artifacts/soak
```

Every sample records per-service CPU, memory and PIDs; Docker health,
restarts, and OOM state; application health; snapshot age; and the stable
13-server/1,000-sensor/zero-error contract. `samples.jsonl` retains raw
measurements and `report.json` records mean/p95/max summaries. This probe does
not request mass histories or create synthetic load.

The completed Phase 0.5C run lasted 3,602.188 seconds and collected 114 real
samples. All services stayed healthy with zero restarts/OOM events and the
13/1,000/zero-error contract held throughout. Total memory averaged
125.061 MiB (p95 138.55 MiB, max 150.41 MiB). Detailed per-service CPU,
memory, slopes, and the deliberately unenforced candidate limits are recorded
in `docs/CONTAINER_SUPPLY_CHAIN.md`.

### Phase 0.5A execution record

The procedure was executed on 2026-09-27 with Docker Engine 29.1.3 and Docker
Compose 2.40.3. A clean named-volume startup reached three healthy services.
The smoke reported 13 servers, 1,000 sensors, zero collector errors, all five
normalized families, bidirectional traffic, full and partial SFP, legacy
history, and successful pause/resume. After restarting only `app` and
`collector`, the second smoke found the preexisting SQLite pin and reported
`sqlite_persistence_verified_after_restart: true`.

Image inspection confirmed UID/GID 10001, no runtime secret variables in image
configuration, and no `.env`, runtime server file, database, snapshot, or
synthetic mounted fixture inside the image. The final test verified that normal
`down` preserved both named volumes and that the explicit `down --volumes`
command removed only the project volumes. No real PRTG or cloud endpoint was
contacted. Resource consumption was not benchmarked.

## Persistence restart check

The first smoke creates an idempotent synthetic pin. Restart only the stateful
consumers, wait for health, and require that the pin existed before the second
smoke writes it again:

```bash
docker compose restart collector app
docker compose up --detach --wait --wait-timeout 120
COREVIEW_LAB_EXPECT_PERSISTED=1 \
  .venv/bin/python scripts/docker_lab_smoke.py
```

This validates application-owned SQLite state through a normal container
restart. The collector snapshot is regenerated in the same persistent runtime
volume. Restarting the simulator intentionally resets its in-memory tick and
pause state while retaining deterministic IDs/families for the configured seed.

## Health and logs

```bash
docker compose ps
docker compose logs --tail=100 simulator
docker compose logs --tail=100 collector
docker compose logs --tail=100 app
docker compose logs --follow collector app
```

- `running` means only that the process exists;
- simulator `healthy` means its complete synthetic topology is available;
- collector `healthy` means a fresh 13/1,000/zero-error snapshot exists;
- app `healthy` means HTTP is available and sees the collector snapshot;
- dashboard availability is `http://127.0.0.1:5067/dashboard` after login.

Logs use Docker's `local` driver with three 10 MiB files per service. No
application log file is written into the repository or shared SQLite volume.

## Manual API examples

Find current synthetic IDs without assumptions:

```bash
curl --fail --silent http://127.0.0.1:9090/prtg-01/api/multichannel.json \
  | python -c '
import json, sys
found = {}
for sensor in json.load(sys.stdin)["sensors"]:
    found.setdefault(sensor["sensor_type"], sensor["objid"])
for family in ("ping", "cpu", "memory", "traffic", "sfp"):
    print(f"{family}: {found[family]}")
'
```

The automated smoke performs authenticated metadata/history requests for each
printed family. `docs/api/METRICS_API_V2.md` contains the complete query
contract. Never paste the admin password into a committed script or document.

## Stop, restart, and cleanup

Stop and remove containers/network while retaining SQLite, snapshot, and
uploads:

```bash
docker compose down
```

Start later with the same exported credentials:

```bash
docker compose up --detach --wait --wait-timeout 180
```

Destructive cleanup is explicit. It removes only this Compose project's named
volumes after showing them for review:

```bash
docker volume ls --filter label=com.docker.compose.project=coreview-cloud-lab
docker compose down --volumes --remove-orphans
```

The final command permanently deletes the laboratory SQLite database,
snapshot, and uploaded alert sounds. It does not delete repository files or
unrelated Docker volumes.

## Security properties

- host ports bind only to `127.0.0.1`;
- runtime uses a dedicated bridge; only loopback host bindings are published;
- synthetic provider remains loopback-only and ignores proxy variables;
- real provider remains disabled and its TLS policy is unchanged;
- root filesystems are read-only;
- processes run as UID/GID 10001 with all capabilities dropped;
- `.env`, local server config, databases, snapshots, logs, tests, and uploads
  are excluded from the image context;
- required login/session secrets are runtime environment values, not image
  layers;
- synthetic server fixture is read-only and not embedded in the image.

## Limitations

- This is not a production deployment or real-PRTG validation.
- The shared namespace is intentional for loopback security but couples the
  three services' network lifecycle to `simulator`.
- Server CRUD is not supported against the read-only synthetic fixture.
- SQLite remains single-host laboratory persistence.
- Simulator logical state is not durable.
- Browser runtime is offline-capable, but a cold image/npm build still needs
  its pinned artifacts or an explicitly managed trusted cache.
- No normalized-history persistence, counter-to-rate processing, Redis,
  PostgreSQL, Kubernetes, or cloud resources were added.
- Phase 0.5B measured approximately 116.25 MiB total during its smoke sample.
  The longer Phase 0.5C measurements and resource-limit decision are recorded
  in `CONTAINER_SUPPLY_CHAIN.md`.
