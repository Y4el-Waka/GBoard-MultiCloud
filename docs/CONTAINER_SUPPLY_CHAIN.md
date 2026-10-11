# Container Supply Chain and CI

Phase 0.5B makes the synthetic laboratory build inputs explicit and produces
auditable evidence for each container build. It does not claim production
hardening, real PRTG validation, or bit-for-bit deterministic OCI manifests.

## Audited build inputs

The runtime image is based on the official Python image:

- reference: `python:3.12-slim-bookworm`;
- immutable Linux/amd64 digest:
  `sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`;
- resolved version during validation: Python 3.12.15 on Debian 12.15;
- validated target: Linux/amd64;
- local image ID observed:
  `sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`.

The digest is part of the `FROM` instruction and recorded in OCI labels. The
human-readable tag remains present so an update reviewer can understand the
base lineage. A base update is deliberate: resolve a new digest, scan it,
rebuild without cache, execute all tests/smoke, and update the digest test and
this document together.

On 2026-10-10 the runtime base was deliberately refreshed within the same
Python 3.12/Bookworm lineage. The new immutable image contains Debian fixes
for `libpcre2-8-0` (`10.42-1+deb12u2`) and `perl-base`
(`5.36.0-7+deb12u4`) that were not present in the previous digest. The Node
build-stage digest did not change.

Phase 0.5C also has a build-only frontend stage:

- reference: `node:22-alpine`;
- immutable digest:
  `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402`;
- resolved validation version: Node 22.23.3 on Linux/amd64.

It installs exact `package-lock.json` development dependencies with `npm ci`
and emits `static/vendor`. Only that output enters the Python runtime. Node,
npm, Playwright, Tailwind tooling, and `node_modules` do not.

The Dockerfile copies only Python modules, `metrics/`, templates, reviewed
static assets, generated vendor output, and the container healthcheck. It does
not use `COPY .`, and `.dockerignore`
excludes developer configuration, databases, snapshots, uploads, tests,
documentation, caches, and generated evidence.

## Python dependency strategy

The dependency files have separate roles:

- `requirements.in`: exact direct dependencies imported or executed by the
  application;
- `requirements.constraints`: reviewed transitive versions retained unless a
  security correction required a change;
- `requirements.lock`: pip-compiled exact resolution with SHA-256 hashes;
- `requirements.txt`: compatibility include for the lock used by existing
  local setup instructions.

The container installs `requirements.lock` with `--require-hashes`. It then
removes pip from the runtime image because package installation is not a
runtime responsibility.

The Phase 0.5B baseline updated only dependencies with observed fixed security
releases, plus the Flask-required Blinker compatibility change:

| Package | Before | Phase 0.5B | Reason |
| --- | --- | --- | --- |
| Flask | 3.0.3 | 3.1.3 | first observed fixed release |
| Werkzeug | 3.0.6 | 3.1.6 | first release covering the observed fixes |
| requests | 2.32.4 | 2.33.0 | first observed fixed release |
| urllib3 | 2.2.3 | 2.7.0 | covers all four observed HIGH fixes |
| python-dotenv | 1.0.1 | 1.2.2 | first observed fixed release |
| idna | 3.14 | 3.15 | first observed fixed release |
| blinker | 1.8.2 | 1.9.0 | required by Flask 3.1.3 |

### 2026-10-10 security maintenance

The container gate subsequently identified CVE-2026-97687 and
CVE-2026-97689 in the directly pinned `urllib3==2.7.0`; both have a fixed
release at 2.8.0. `requests==2.33.0` permits `urllib3>=1.26,<3`, so the direct
pin and hash lock were updated to `urllib3==2.8.0` without changing the rest
of the Python resolution.

The same scan found eight fixable HIGH/CRITICAL Debian records in the old
runtime digest: CVE-2026-103111 in `libpcre2-8-0` and CVE-2026-13221,
CVE-2026-42496, CVE-2026-8376, CVE-2026-42497, CVE-2026-48962,
CVE-2026-57432, and CVE-2026-57433 in `perl-base`. The runtime base digest was
therefore refreshed rather than adding an unpinned package upgrade to the
Dockerfile. No Trivy severity, fixability, or exit-code policy changed.

The other resolved versions were retained. Pinning and hashing dependencies
does not prove that they are vulnerability-free.

The duplicate unversioned `python-dotenv` line was removed.
`importlib_metadata` and `zipp` also disappeared from the image because the
Python 3.12 resolution does not require that compatibility backport. The final
image measured 48,990,418 bytes versus 49,042,787 bytes for the Phase 0.5A
image on the same host.

### Regenerating the lock

Use Python 3.12 and the documented pip-tools version in a disposable virtual
environment:

```bash
python3.12 -m venv /tmp/coreview-pip-tools
/tmp/coreview-pip-tools/bin/python -m pip install pip-tools==7.6.1
/tmp/coreview-pip-tools/bin/pip-compile \
  --generate-hashes \
  --strip-extras \
  --constraint requirements.constraints \
  --output-file requirements.lock \
  requirements.in
```

Review the complete diff and build/test/scan before accepting a regenerated
lock. The lock targets the validated Python 3.12/Linux runtime; another Python
minor or operating system requires explicit validation.

## SBOM and vulnerability reports

One pinned tool performs both operations:

- Trivy 0.74.0;
- image digest:
  `sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`.

Generate evidence for the already-built image:

```bash
bash scripts/container_supply_chain.sh artifacts/container
```

Outputs are intentionally ignored by Git:

- `image-inspect.json`: OCI image configuration and labels;
- `sbom.cdx.json`: CycloneDX SBOM generated by Trivy;
- `vulnerabilities.json`: complete machine-readable vulnerability report.

The Trivy cache is a Docker volume named `coreview-trivy-cache`. Reports do not
contain Compose runtime environment variables, databases, snapshots, or local
server configuration.

## Vulnerability policy and measured result

The complete report always records every detected severity and unfixed
finding. CI blocks when Trivy finds a HIGH or CRITICAL issue for which its
current advisory database reports a fixed version:

```text
--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1
```

There is no `.trivyignore` and no hidden CVE exception list. Adding one requires
a written owner, scope, reason, expiry, and compensating control.

The security-maintenance result is refreshed by the final validation command.
The table below records the 2026-10-10 completed scan against
`coreview-cloud-lab:0.5c`:

| Scope | Critical | High | Medium | Low | Unknown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Debian 12.15 packages | 2 | 55 | 117 | 101 | 4 |
| Python packages | 0 | 0 | 0 | 0 | 0 |
| Total | 2 | 55 | 117 | 101 | 4 |

Of the remaining findings, Trivy reported one MEDIUM record with a published
fix; no remaining HIGH/CRITICAL finding had a published Debian fix in the
database used by this run. The two CRITICAL records were associated with
Debian's `libsqlite3-0` and `zlib1g` packages. The 55 HIGH records were also
unfixed according to that database. They remain an accepted laboratory
baseline risk, not a claim of safety, and must be re-evaluated whenever the
base digest or Trivy database changes.

The pre-change image reported 5 CRITICAL, 59 HIGH, 117 MEDIUM, 104 LOW, and 2
UNKNOWN findings. Updating the affected Python packages and removing pip from
runtime eliminated the 19 Python findings, including four HIGH issues.

Severity alone is not exploitability analysis. The gate is a conservative,
repeatable policy for fixed HIGH/CRITICAL findings; human review remains
required for exposure, reachability, and unfixed base-image risk.

## CI workflow

`.github/workflows/container-ci.yml` defines one Linux/amd64 GitHub Actions job:

1. immutable-SHA checkout and Python setup actions;
2. hash-locked dependency installation;
3. pinned Node setup plus `npm ci` and deterministic frontend rebuild check;
4. ephemeral laboratory secret generation;
5. Python and JavaScript tests;
6. compile, diff, and Compose validation;
7. destructive cleanup of only this job's prior Compose volumes;
8. Docker build and health-gated startup;
9. full API smoke, bounded API burst, and five-minute restricted soak;
10. external-network-blocked Chromium smoke;
11. persistence restart smoke and resource snapshot;
12. CycloneDX SBOM and complete Trivy report;
13. upload of container/browser/resource evidence for 14 days;
14. unconditional Compose volume cleanup.

The workflow does not push images, access production secrets, contact real
PRTG, or deploy cloud resources. GitHub-hosted execution has not been observed
from this workspace; the workflow is created and its equivalent commands have
been validated locally, but remote-run status remains **not validated**:

```text
github_actions_remote_validated: false
```

## Clean-copy validation

The local validation copied only the paths returned by:

```bash
git ls-files --cached --others --exclude-standard
```

into a new temporary directory. That copy had no `.env`, `.venv`, local
`servers.json`, database, snapshot, or previous volumes. A new virtual
environment installed the hash-locked dependencies; Docker then rebuilt without
cache, created new named volumes, reached all healthchecks, and passed the full
13-server/1,000-sensor/zero-error smoke. The temporary Compose project and its
volumes were removed afterward.

This proves clean-copy operability of the proposed working tree. The GitHub
workflow will provide independent clean-checkout evidence after it runs on a
remote runner.

## Frontend supply chain

Phase 0.5C replaced the audited Tailwind, cdnjs, jsDelivr, and Google Fonts
requests with exact npm inputs and same-origin output. Package integrity is
locked, distributed licenses are retained, and
`static/vendor/manifest.json` holds SHA-256 for every runtime file. CI rebuilds
the directory and fails on a generated diff. A real Chromium test then runs on
an internal-only Docker network and uploads screenshots/report as evidence.

The exact seven-package inventory, licenses, CSP, Playwright image digest, and
update process are documented in `docs/OFFLINE_FRONTEND.md`. This proves
runtime-offline behavior for the tested synthetic matrix; it does not claim a
cold dependency build can run without a trusted package/image source.

## Resource measurement

Short-run method on 2026-09-27:

- Docker Engine 29.1.3, Compose 2.40.3, Linux/amd64;
- host memory reported by Docker: 23.18 GiB;
- clean named-volume startup using health-gated `docker compose up --wait`;
- point-in-time `docker stats --no-stream` samples during startup, smoke, and
  idle state;
- only containers prefixed `coreview-cloud-lab-` were included.

Observed maxima:

| Service | Startup CPU | Startup memory | Smoke CPU | Smoke memory | Idle memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| simulator | 28.12% | 40.35 MiB | 0.04% | 42.08 MiB | 41.86 MiB |
| collector | 37.50% | 41.02 MiB | 23.35% | 27.28 MiB | 27.28 MiB |
| app | 27.81% | 44.54 MiB | 6.04% | 46.89 MiB | 46.78 MiB |

The simultaneous smoke sample used approximately 116.25 MiB total and 29.43%
aggregate point-in-time CPU. The highest all-service startup sample used about
112.35 MiB and 52.32% aggregate CPU. These are short-run samples, not capacity
or percentile measurements; the host was also running unrelated containers.
Phase 0.5C then ran the health-aware sampler for 3,602.188 seconds at a nominal
30-second interval. The 114 real samples retained Docker stats plus application
contract checks. Every sample found all three containers healthy, no restart or
OOM, 13 servers, 1,000 sensors, and zero collector errors. Snapshot age stayed
below 5.057 seconds (p95 4.773 seconds).

| Service | CPU mean | CPU p95 | CPU max | Memory mean | Memory p95 | Memory max | First → last | Linear slope |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| app | 5.122% | 27.05% | 28.74% | 50.004 MiB | 51.17 MiB | 64.04 MiB | 49.00 → 49.27 MiB | +0.749 MiB/h |
| collector | 6.695% | 25.47% | 33.07% | 29.639 MiB | 30.88 MiB | 43.86 MiB | 28.71 → 28.56 MiB | +0.612 MiB/h |
| simulator | 6.032% | 27.72% | 29.48% | 45.417 MiB | 53.85 MiB | 60.75 MiB | 44.30 → 44.07 MiB | -0.247 MiB/h |

Total memory averaged 125.061 MiB, with p95 138.55 MiB and maximum
150.41 MiB. The small positive fitted slopes for app and collector coexist
with nearly flat first/last readings and are not sufficient evidence of a
leak over one hour.

Phase 0.5D accepted the evidence-based ceilings after a clean-volume startup,
full smoke, controlled burst, restricted soak, offline browser smoke, and
persistence restart:

| Service | CPU limit | Memory limit | 0.5C maximum | Restricted maximum | Restricted memory headroom |
| --- | ---: | ---: | ---: | ---: | ---: |
| app | 1 CPU | 128 MiB | 64.04 MiB | 49.88 MiB | 61.0% |
| collector | 1 CPU | 96 MiB | 43.86 MiB | 29.17 MiB | 69.6% |
| simulator | 1 CPU | 128 MiB | 60.75 MiB | 43.16 MiB | 66.3% |

Docker inspection confirmed `NanoCpus=1000000000` for each service and memory
limits of 134,217,728, 100,663,296, and 134,217,728 bytes respectively. The
shared network namespace and loopback-only provider policy are unchanged.

The clean-volume cold start reached all healthchecks in 17.54 seconds on the
validation host (the image build reused already validated layers). The
controlled burst issued 180 mixed health, snapshot, metadata, normalized
history, and legacy-history requests with six workers: 180 succeeded, zero
failed, throughput was 63.253 requests/s, latency p95 was 9.372 ms, and maximum
latency was 35.968 ms.

The restricted soak lasted 302.198 seconds with 19 samples at a nominal
15-second interval. All samples preserved 13 servers, 1,000 sensors, zero
collector errors, healthy containers, zero OOM events, and zero restarts.
Snapshot age remained below 4.904 seconds.

| Service | CPU mean | CPU p95/max | Memory mean | Memory p95/max | First → last |
| --- | ---: | ---: | ---: | ---: | ---: |
| app | 2.847% | 26.74% | 48.631 MiB | 49.88 MiB | 48.81 → 48.61 MiB |
| collector | 3.812% | 25.83% | 27.978 MiB | 29.17 MiB | 27.60 → 29.17 MiB |
| simulator | 4.268% | 28.88% | 41.819 MiB | 43.16 MiB | 41.94 → 41.30 MiB |

With only 19 samples the nearest-rank p95 equals the maximum and short-window
linear slopes are not meaningful leak evidence. The earlier one-hour run
remains the memory-growth reference; the restricted run proves the chosen
ceilings do not break the representative laboratory acceptance sequence.
Raw evidence is written beneath `artifacts/resource-limits/` and remains an
ignored local/CI artifact.

## Update procedure

1. Review upstream advisories and release notes for one intended change.
2. Update direct input or transitive constraint explicitly.
3. Regenerate and review `requirements.lock` hashes.
4. Update the base or Trivy digest only after resolving its platform/version.
5. Run Python, JavaScript, compile, Compose config, clean build, healthchecks,
   full smoke, persistence smoke, SBOM, and scan.
6. Compare vulnerability totals and investigate every new HIGH/CRITICAL.
7. Update measured evidence and accepted risks; never rewrite results to imply
   zero findings.
