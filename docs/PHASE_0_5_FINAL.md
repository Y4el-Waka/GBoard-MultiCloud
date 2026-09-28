# Phase 0.5 final acceptance

## Status

Phase 0.5 is locally implemented and accepted for the synthetic laboratory.
Remote GitHub Actions acceptance remains open because this workspace has no
Git remote configured and no GitHub CLI/authenticated runner channel.

```text
local_acceptance_complete: true
github_actions_remote_validated: false
validated_against_real_prtg: false
phase_0_5_fully_closed: false
```

The final flag remains false solely because the requested remote workflow run
has not been observed. No remote success, run URL, commit SHA, duration, or
artifact is invented.

## Final laboratory architecture

One digest-pinned, non-root image runs as three constrained Compose services:

- simulator: 1 CPU, 128 MiB, publishes ports only on host loopback;
- collector: 1 CPU, 96 MiB, shares the simulator network namespace;
- app: 1 CPU, 128 MiB, shares the simulator network namespace.

The collector and app still reach the simulator at `127.0.0.1:9090`; no Docker
destination exception was added to the synthetic provider. Root filesystems
remain read-only, all capabilities are dropped, `no-new-privileges` remains
enabled, secrets are runtime-only, and the real PRTG provider remains disabled.

SQLite/snapshot and uploads remain separate named volumes. Normal `down`
preserves them; only the explicit documented `down --volumes` removes them.

## CI audit

The existing workflow was retained and minimally extended. It contains:

- checkout, Python, Node, and artifact Actions fixed by full commit SHA;
- Python dependencies installed from the hash-locked requirements file;
- `npm ci`, deterministic frontend rebuild, and generated-tree diff;
- Python and JavaScript suites plus compile/diff/Compose checks;
- ephemeral synthetic secrets;
- clean-volume Compose build and health-gated startup;
- full API smoke, bounded burst, and restricted soak;
- real Chromium smoke on an external-network-blocked network;
- persistence verification after app/collector restart;
- CycloneDX SBOM and complete Trivy report;
- 14-day evidence upload;
- unconditional Compose volume cleanup.

It does not publish images, use production credentials, contact real PRTG, or
deploy cloud infrastructure. A clean runner cannot obtain state from `.env`,
local SQLite/snapshots, ignored `servers.json`, old volumes, or a developer
virtual environment. The declared pinned base images and dependency registries
are still required for a cold build.

## Remote execution record

| Field | Result |
| --- | --- |
| Workflow | `.github/workflows/container-ci.yml` |
| Run URL/ID | not available — not executed |
| Remote commit SHA | not available — working tree not pushed |
| Duration | not available |
| Successful jobs | not observed |
| Failed jobs | not observed |
| Downloaded artifacts | none from GitHub; local evidence only |

### Exact authorized-run procedure

After reviewing and committing the complete Phase 0.5 changes to an authorized
GitHub repository:

```bash
reviewed_branch="$(git branch --show-current)"
test -n "$reviewed_branch"
git push --set-upstream origin "$reviewed_branch"
gh auth status
gh workflow run container-ci.yml --ref "$reviewed_branch"
run_id="$(gh run list --workflow container-ci.yml --branch "$reviewed_branch" \
  --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --exit-status
gh run view "$run_id" \
  --json url,headSha,status,conclusion,createdAt,updatedAt,jobs
gh run download "$run_id" --name coreview-container-evidence \
  --dir "artifacts/github-actions/$run_id"
```

The reviewer must confirm that `headSha` equals the pushed reviewed commit and
record the URL, duration, job conclusions, artifact checksum, smoke result,
browser report, SBOM, and vulnerability totals here. If GitHub CLI is not
permitted, dispatch the same workflow from the repository Actions page and
download `coreview-container-evidence` from that run. Do not provide a token to
the workflow or add production secrets.

## Resource-limit derivation

The Phase 0.5C one-hour baseline observed maxima of 64.04 MiB for app,
43.86 MiB for collector, and 60.75 MiB for simulator. The accepted memory
ceilings retain approximately two times those maxima. A full CPU per service
retains roughly three times the highest observed CPU peak while avoiding a
sub-core limit that could distort startup and health timing.

Docker inspection after startup confirmed:

| Service | Memory bytes | NanoCPUs |
| --- | ---: | ---: |
| simulator | 134,217,728 | 1,000,000,000 |
| collector | 100,663,296 | 1,000,000,000 |
| app | 134,217,728 | 1,000,000,000 |

No swap policy, PID ceiling, or longer timeout was added to make the test pass.

## Constrained local acceptance results

- clean-volume cold startup: healthy in 17.54 seconds;
- full smoke: 13 servers, 1,000 sensors, zero collector errors;
- Ping, CPU, memory, Traffic IN/OUT, complete/partial SFP: passed;
- legacy history and synthetic pause/resume: passed;
- burst: 180/180 successful with six workers, 63.253 requests/s;
- burst latency: mean 4.793 ms, p50 4.110 ms, p95 9.372 ms, max 35.968 ms;
- restricted soak: 302.198 seconds, 19 samples, zero failures;
- all services healthy, zero OOM, zero restarts;
- restricted total memory: mean 118.428 MiB, maximum 120.87 MiB;
- snapshot age: maximum/p95 4.904 seconds;
- restart persistence marker: passed;
- offline Chromium: no external requests, local failures, CSP errors, or
  unexpected console errors.

The image build used already validated local layers during this final runtime
acceptance. Phase 0.5B separately demonstrated a no-cache clean-copy build.
Remote cold-run timing may differ and must be recorded from the real Actions
run rather than inferred from this host.

## Supply-chain result

Phase 0.5D retains the same runtime image content and scanner policy. The final
local Trivy report must remain attached to acceptance evidence. The current
known baseline is:

| Scope | Critical | High | Medium | Low | Unknown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Debian 12.15 | 5 | 55 | 104 | 102 | 2 |
| Python packages | 0 | 0 | 0 | 0 | 0 |

No HIGH/CRITICAL record had a published fix in the database used for the
latest local run. The gate blocks fixed HIGH/CRITICAL findings and does not use
an ignore file. Passing that gate is not a declaration that the image is safe.

## Accepted risks and limits of scope

- Remote GitHub Actions behavior and timing are still unverified.
- Debian has unfixed Critical/High records requiring periodic review.
- Resource limits apply only to the deterministic synthetic laboratory.
- The five-minute constrained soak tests ceiling compatibility; the one-hour
  unconstrained run remains the better short-term memory-growth observation.
- A cold dependency build requires trusted registry/image access or a managed
  cache even though browser runtime is offline-capable.
- Shared networking couples app/collector network lifecycle to simulator by
  design.
- SQLite remains single-host laboratory storage.
- Real PRTG compatibility remains fixture-only and unvalidated against an
  authorized instance.

Terraform, cloud deployment, PostgreSQL, Redis, production hardening,
counter-to-rate, normalized-history persistence, and UI redesign remain
explicitly outside Phase 0.5.

## Closure action

Do not mark Phase 0.5 fully closed until the exact reviewed commit passes the
remote workflow and its artifacts are inspected. Once that evidence is added,
Phase 0.5 can close without further application changes.
