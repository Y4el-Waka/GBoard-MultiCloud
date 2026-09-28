# ADR-017: Local, reproducible frontend assets

## Context

The Phase 0.5B runtime still depended on Tailwind's browser CDN, cdnjs,
jsDelivr, and Google Fonts. Its allowlist detected unexpected origins but did
not make the laboratory browser-offline, fully version Chart.js/Toastr, or
include frontend assets in the image supply-chain evidence. Relaxing CSP or
retaining a CDN fallback would undermine the contained synthetic-lab goal.

## Decision

CoreView vendors the reviewed frontend runtime through exact npm development
dependencies and `package-lock.json`. A single small Node script builds
Tailwind, copies the required package distributions and licenses, and creates
a checksum manifest. Templates reference only versioned, same-origin files.

The Docker build uses a digest-pinned Node 22 Alpine stage and copies only the
generated vendor directory into the existing Python runtime image. No Node
tooling or npm dependency tree is shipped at runtime.

Existing inline template blocks remain in place to keep this phase focused;
the CSP authorizes them using exact SHA-256 hashes. CDN origins,
`unsafe-inline`, and `unsafe-eval` are not allowed. A real Chromium smoke runs
on a temporary Docker `--internal` network and also rejects non-laboratory
requests in the browser layer.

The source templates and dashboard behavior remain unchanged except for asset
locations. Chart.js remains the graph library. No frontend framework is added.

## Alternatives considered

- **Retain controlled CDN allowlists.** Rejected because first-load operation
  would still depend on external availability and mutable URLs.
- **Commit ad-hoc downloads without npm metadata.** Rejected because versions,
  integrity, transitive build inputs, licenses, and updates would be harder to
  audit.
- **Ship Node and build assets at container startup.** Rejected because it
  enlarges the runtime attack surface, needs writable state/network, and makes
  startup non-reproducible.
- **Replace the UI framework/chart library.** Rejected as an unrelated redesign
  with substantial regression risk.
- **Permit all inline code with CSP keywords.** Rejected; exact hashes preserve
  current markup without weakening the policy globally.

## Consequences

- Runtime rendering no longer requires external network access.
- Frontend source updates require an explicit npm build and reviewed generated
  diff; `static/vendor/manifest.json` makes the artifact set auditable.
- The repository and image grow by roughly the local font/icon/chart assets,
  while the runtime still excludes Node.
- Builds still require access to pinned base images and npm artifacts unless a
  separate trusted cache is provided; runtime-offline and build-offline are
  intentionally distinct claims.
- Package and browser-image vulnerabilities must be reviewed in addition to
  the Python runtime scan. The shipped runtime remains covered by the image
  SBOM/Trivy process.
- Exact CSP hashes couple reviewed inline-block edits to a deliberate policy
  update. Extraction to first-party files or per-request nonces can be
  considered later, but is not required for Phase 0.5C.
- GitHub Actions runs the frontend build/tests and offline browser smoke, but
  remote-run validation remains false until a real hosted job is observed.

