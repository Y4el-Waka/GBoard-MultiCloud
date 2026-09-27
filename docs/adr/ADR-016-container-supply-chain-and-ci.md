# ADR-016: Container supply chain and CI evidence

- Status: Accepted
- Date: 2026-09-27
- Phase: 0.5B

## Context

ADR-015 established a contained synthetic laboratory, but its base image was
selected only by a mutable tag, Python dependencies contained a duplicate
unpinned requirement, no SBOM or vulnerability policy existed, and the full
Docker smoke was a manual procedure. Frontend runtime assets also remain
dependent on several public CDNs.

The goal is reproducible and auditable laboratory construction without adding
product features, publishing images, weakening runtime protections, or
claiming that version pins eliminate security defects.

## Decision

Pin the official Python 3.12 slim-bookworm base by its multi-platform digest
and retain the readable tag. Linux/amd64 is the validated platform.

Separate direct dependency intent, reviewed transitive constraints, and the
generated lock. Install the lock using pip hash verification and remove pip
from the runtime image.

Use one scanner, Trivy 0.74.0 pinned by image digest, for both a CycloneDX SBOM
and the complete JSON vulnerability report. CI blocks fixed HIGH/CRITICAL
findings but preserves and uploads all severities and unfixed findings. No CVE
is silently ignored.

Add a GitHub Actions workflow that creates ephemeral synthetic secrets and
runs tests, static checks, a clean Compose build, health-gated startup, full
smoke, persistence smoke, evidence generation, resource snapshot, and
unconditional cleanup. The workflow never publishes or deploys the image.
Third-party actions are referenced by immutable commit SHA.

Keep current frontend files unchanged. Freeze the observed remote URLs with an
allowlist test and document offline migration separately because Tailwind
compilation, license inventory, local asset packaging, CSP changes, and
network-blocked browser validation form a distinct frontend supply-chain task.

Do not introduce resource limits from one short measurement. Preserve the
measured baseline and require an isolated soak before setting limits.

## Alternatives considered

### Depend only on exact Python versions without hashes

Rejected because the resolver could still consume a different distribution
artifact. Exact versions plus SHA-256 hashes make accepted artifacts explicit.

### Commit generated SBOM and vulnerability output

Rejected because findings depend on the image digest and advisory database
time. CI artifacts preserve evidence per run without creating constantly stale
source files.

### Use separate SBOM and vulnerability tools

Rejected for this phase. Trivy produces both required formats, so a second
tool would expand maintenance and trust without a demonstrated need.

### Fail CI on every unfixed HIGH or CRITICAL finding

Rejected as the initial laboratory policy because the pinned official Debian
base currently has numerous vendor-unfixed records, including five CRITICAL.
The complete report remains mandatory; fixed HIGH/CRITICAL findings block.
Production policy must be stricter and based on reachability and supported base
lifecycles.

### Vendor all frontend assets now

Deferred. Tailwind browser runtime cannot be replaced by merely copying one
file while preserving the current class set. Licensing, exact package
versions, CSP, and offline browser verification require a focused change.

### Add CPU and memory limits from the first measured run

Rejected because point samples on a shared development host are not a safe
capacity basis.

## Consequences

Positive consequences:

- the base image, scanner, actions, dependency versions, and dependency
  artifacts are explicit;
- a clean copy can build without personal configuration or prior volumes;
- every CI run is designed to produce an image inspection, CycloneDX SBOM,
  vulnerability JSON, and resource snapshot;
- corrected Python dependencies remove the observed fixable HIGH findings;
- runtime remains non-root, read-only, capability-free, loopback-published,
  and synthetic-only.

Tradeoffs and accepted risks:

- a pinned digest deliberately stops automatic base security updates;
- Debian advisory results still include 5 CRITICAL and 55 HIGH unfixed records
  in the measured scan;
- the GitHub workflow is locally reviewed but has not run on a remote runner;
- build output is content-controlled but not claimed bit-for-bit identical
  because OCI provenance and build metadata may differ;
- browser operation still depends on audited public CDN URLs;
- no resource limit is enforced yet.

