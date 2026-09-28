# ADR-019: GCP single-VM synthetic lab architecture

## Context

CoreView already runs as three hardened containers (`simulator`, `collector` and `app`) whose app and collector share the simulator network namespace. Phase 1.1A needs a GCP-native infrastructure implementation that preserves that contract without creating a GCP project, requesting credentials, opening the dashboard publicly or claiming a real deployment.

The AWS blueprint uses a public IPv4 address for outbound traffic and Session Manager for administration. Copying that design literally would ignore material differences in GCP networking and administrative access.

## Decision

Use an existing, explicitly allow-listed GCP project in `us-east1` and deploy one private-only Compute Engine `e2-small` VM in a custom-mode VPC:

- The VM has no external IP. Public NAT on a regional Cloud Router supplies controlled outbound access for bootstrap and image pulls.
- Administrative access uses IAP TCP forwarding to SSH plus OS Login. The only ingress rule allows TCP 22 from Google's documented IAP proxy range and targets the runtime service account. There is no ingress for CoreView or the simulator.
- The dashboard remains bound to VM loopback and is reached through an IAP tunnel to port 5067.
- The runtime service account has no user-managed key and only `roles/artifactregistry.reader` on the dedicated private repository.
- Human IAP and OS Login roles are assigned outside this workload stack to named operators. The stack does not grant its own administrators.
- Artifact Registry is regional, private, uses immutable Docker tags and has Terraform `prevent_destroy`. The deployed tag should be a full CI-reviewed Git SHA.
- Debian 12 x86_64 is supplied as a required canonical immutable image reference. Image-family references are rejected.
- Shielded VM features, OS Login and project SSH-key blocking are explicit.
- The boot disk and a separate runtime disk are `pd-balanced` and use Google-managed encryption. Docker data and named volumes live on the runtime disk, and Docker requires that mount before starting.
- Runtime secrets are entered interactively after provisioning through an authorized IAP/OS Login session. They are absent from Terraform, metadata, startup script, outputs and images.
- The bootstrap installs Docker, verifies a pinned Compose binary, writes the synthetic configuration and defers service startup until the secret file exists. A versioned completion marker makes later GCE startup-script invocations idempotent.
- Terraform state uses a separately bootstrapped GCS bucket with locking and Object Versioning; this stack never creates that bucket.

## Alternatives considered

### External VM address with source-IP-restricted SSH

Rejected. It reduces Cloud NAT cost but adds a directly addressed VM and depends on stable operator source addresses. It also weakens the administrative boundary compared with IAP.

### IAP tunnel directly to the dashboard port

Rejected for the initial design. IAP TCP could forward other ports, but limiting the VPC ingress rule to SSH reduces exposed administrative protocols. The operator tunnels to the VM and accesses the loopback service.

### Identity-Aware Proxy for a web load balancer

Deferred. It requires an HTTPS load-balancing stack, certificates and additional public components that are disproportionate for a single-user synthetic lab.

### Secret Manager during bootstrap

Deferred. It would place a secret resource or reference in the deployment lifecycle and grant the VM secret access. Interactive initialization keeps first-deployment credentials outside Terraform while the lab remains single-host. Secret Manager is the preferred reevaluation point for unattended deployments.

### Cloud Run, GKE or managed multi-service deployment

Rejected for this phase. The application still depends on shared network namespace semantics and SQLite/local runtime persistence. Replatforming would be a separate application architecture change.

### Customer-managed encryption keys

Deferred. Google-managed encryption satisfies this laboratory baseline. CMEK would add key rotation, IAM and key-destruction recovery obligations that are not justified for synthetic-only data.

## Consequences

- There is no public dashboard or public SSH endpoint.
- IAP is not equivalent to AWS Session Manager: it still uses SSH, needs OS Login permissions and requires ingress from the documented Google proxy range.
- Cloud NAT adds a fixed hourly/IP cost and data-processing charges.
- A missing runtime disk prevents Docker from starting instead of silently creating state on the boot disk.
- The runtime disk survives normal reboots and preserves Docker named volumes; repository and disk destruction remain deliberate and are covered by the destroy runbook.
- The repository is initially empty and the service will not start before a reviewed image and runtime secrets exist.
- Package installation from Debian repositories is operationally reproducible but not bit-for-bit immutable; a future hardened image pipeline should pre-bake Docker.
- Static validation proves Terraform schema and repository controls, not project permissions, quota, plan correctness against an account or workload operation.
