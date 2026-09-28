# CoreView Cloud Lab — Phase 1.1 final review

## Verifiable status

- Architecture: **Implemented and frozen as an offline blueprint**.
- Terraform: **Static validation passed**.
- GCP authentication: **Not performed**.
- Real GCP plan: **Not executed**.
- Workload deployment: **Not executed**.
- GCP resources created or modified: **None**.
- AWS, CoreView application and frontend changes: **None**.

This document records source-level evidence only. It is not evidence that GCP accepted the configuration or that the workload ran on Compute Engine.

## Final architecture

CoreView remains one portable container image and three isolated processes on one host:

```text
Authorized operator
  -> IAP TCP forwarding
  -> SSH / OS Login on TCP 22
  -> private Compute Engine VM (no external IP)
       -> Docker Compose
            simulator
            collector --network=container:simulator
            app       --network=container:simulator
       -> /var/lib/docker on separate pd-balanced disk
       -> Artifact Registry pull through keyless service account
       -> outbound HTTPS/DNS/NTP through Cloud NAT
```

The dashboard and simulator bind only to VM loopback. No firewall rule exposes TCP 5067 or 9090. A future operator reaches the dashboard through SSH forwarding over IAP.

## Planned resource inventory

The static Terraform configuration contains 19 managed resource instances before provider-internal or API-side behavior:

| Quantity | Terraform resource | Purpose |
|---:|---|---|
| 5 | `google_project_service` | Compute, IAM, IAP, OS Login and Artifact Registry APIs |
| 1 | `google_compute_network` | Custom-mode VPC |
| 1 | `google_compute_subnetwork` | Private regional subnet with flow logs |
| 1 | `google_compute_router` | Cloud NAT control plane |
| 1 | `google_compute_router_nat` | Outbound path for the private VM |
| 5 | `google_compute_firewall` | IAP SSH ingress, HTTPS/DNS/NTP egress and final deny |
| 1 | `google_service_account` | Keyless VM runtime identity |
| 1 | `google_artifact_registry_repository` | Private immutable Docker repository |
| 1 | `google_artifact_registry_repository_iam_member` | Repository-scoped reader grant |
| 1 | `google_compute_disk` | Separate runtime disk |
| 1 | `google_compute_instance` | Shielded private CoreView host |

The boot disk is nested in the VM resource. The existing GCP project, human IAM assignments and proposed GCS backend are explicitly outside this stack. A real plan may expose provider/API details that static inspection cannot prove.

## Audit disposition

| Review area | Frozen decision | Evidence and residual risk |
|---|---|---|
| Shared project APIs | `disable_on_destroy=false` and `disable_dependent_services=false` | Workload destroy will not disable APIs that a pre-existing project may share. Future apply still needs API-enable permission. |
| Runtime IAM | Keyless service account; only repository-scoped `roles/artifactregistry.reader` | No service-account key resource. Human IAP/OS Login roles remain external and must be narrowly assigned. |
| Administrative ingress | IAP proxy range to TCP 22 only, targeted to the runtime service account | No 5067/9090 ingress. IAP remains SSH-dependent and is not equivalent to AWS Session Manager. |
| Private VM egress | Default internet route retained explicitly; Cloud Router/NAT; HTTPS/DNS/NTP allow rules and final deny | NAT supplies no unsolicited ingress. Firewall ports are bounded, but this is not domain-aware egress filtering. |
| Runtime disk | Ext4 at `/var/lib/docker`; UUID in `fstab`; Docker `RequiresMountsFor`; no `nofail` | Reboots preserve Docker volumes. Missing disk fails closed. Disk remains Terraform-owned and needs backup/retention approval before destroy. |
| Secrets | Interactive root-only `/opt/coreview/.env` after provisioning | No secret values in variables, state, metadata, startup template, outputs or image. This manual model is not unattended production secret management. |
| Empty registry | CoreView unit is enabled but gated by `ConditionPathExists=/opt/coreview/.env` | Bootstrap does not pull or start CoreView. Image pull occurs only after image publication and interactive secret initialization. |
| Startup lifecycle | Revisioned completion marker; marker written only after success | Reboots do not repeat package installation or stop a running Docker daemon. Failed bootstrap retries; template changes require a reviewed revision increment. |
| Debian image | Required canonical immutable reference matching `projects/debian-cloud/global/images/debian-12-...` | Family references are rejected. Exact image must be resolved and reviewed during a future authorized preflight. |
| Artifact lifecycle | Immutable tags plus Terraform `prevent_destroy` | Destroy cannot silently delete the repository. A future teardown must export required digests and remove the guard in a separately reviewed source change before repository deletion. |
| State | Proposed pre-existing versioned GCS bucket with locking | Bucket is not created or validated here and survives workload destroy. |
| Cost | Dated estimate with persistent/residual items called out | Not a quote or invoice; reprice before any authorized apply. |

## Minimal corrections made during Phase 1.1B

1. Preserved shared project APIs explicitly on destroy and added regression coverage.
2. Made retention of the default internet route explicit because Cloud NAT requires an internet route.
3. Changed the runtime mount to fail closed and bound Docker startup to the mount.
4. Added a versioned successful-bootstrap marker so GCE startup scripts are idempotent across reboots.
5. Corrected the Debian image-resolution runbook to output the canonical reference accepted by the variable contract.
6. Added Artifact Registry deletion protection and a deliberate keep/abandon/delete teardown decision.
7. Updated runbooks and architecture documentation without changing application behavior.

## Cost baseline

Dated 2026-09-28 for `us-east1`, before tax:

- continuous 730-hour scenario: approximately **USD 21.35/month** plus variable transfer, logging, GCS and artifact storage;
- 24-hour scenario: approximately **USD 0.69** before variable usage;
- compute stops when the VM stops, while disks, Artifact Registry, GCS state/object versions, snapshots and logs can continue billing;
- Billing Budgets are alerts, not hard spending limits.

The arithmetic and official source links are in `infra/gcp/COST_ESTIMATE.md`. Prices and free-tier availability must be rechecked before deployment.

## Validation evidence

The offline review runs:

```bash
terraform -chdir=infra/gcp fmt -check -recursive
terraform -chdir=infra/gcp init -backend=false -input=false
terraform -chdir=infra/gcp validate
.venv/bin/python -m unittest tests.test_gcp_terraform -v
.venv/bin/python -m unittest discover -s tests -v
node --test tests/frontend/test_metrics_history.js
.venv/bin/python -m compileall .
git diff --check
```

Phase 1.1B recorded 13 focused GCP tests, 302 Python tests and 18 JavaScript tests passing. Terraform initialized with the locked provider and validated without a backend or GCP credentials. No `terraform plan` was run.

The prior offline Trivy pass used pinned Trivy 0.74.0 with its embedded checks: 0 Critical, 0 High, 0 Medium and 3 accepted Low findings related to the deliberate Google-managed-encryption/CMEK decision. This is not a current online policy/database scan.

## AWS and GCP trade-offs

| Capability | AWS blueprint | GCP blueprint | Trade-off |
|---|---|---|---|
| Administrative path | SSM Session Manager | IAP TCP forwarding + OS Login | GCP needs SSH and narrowly scoped TCP 22 ingress from the IAP proxy range; AWS SSM uses an agent/control channel. |
| VM addressing | Public IPv4 for outbound, no SG ingress | No external VM IP, Public NAT | GCP reduces direct addressing but adds NAT gateway/address/processing cost. |
| Runtime identity | Instance profile | Attached service account | Both are keyless; IAM scopes and attachment semantics differ. |
| Registry | ECR, non-force-delete | Artifact Registry, immutable tags, Terraform `prevent_destroy` | Both require explicit retention decisions, with different provider lifecycle controls. |
| Persistent data | Encrypted gp3 EBS | Google-managed encrypted pd-balanced disk | GCP uses a separate disk mounted as Docker root and fails closed if absent. |
| Host image | Pinned AL2023 AMI | Future pinned Debian 12 image reference | Both require a fresh authorized image-resolution preflight. |
| Remote state | S3 backend already bootstrapped for AWS | Proposed GCS backend only | GCP backend remains entirely uncreated and unvalidated. |

## Requirements before any real deployment

1. Approve an existing billing-enabled project and organization-policy boundary.
2. Verify operator, Service Usage, Compute, IAM, IAP and Artifact Registry permissions without committing project identifiers.
3. Confirm quota, `us-east1`/zone availability and a non-overlapping RFC1918 subnet.
4. Resolve a current Debian 12 x86_64 image name and copy only its canonical immutable reference into ignored tfvars.
5. Bootstrap and validate the GCS backend under a separate approval.
6. Authenticate with Application Default Credentials and produce a complete real plan; review API enablement, IAM, networking, deletion behavior and cost.
7. Publish only the exact CI-validated image under an immutable full-SHA tag.
8. Apply, initialize synthetic-only secrets, tunnel and smoke-test only under separate explicit approvals.
9. Capture sanitized evidence and re-evaluate sizing, reliability and billing from actual observations.

## Accepted limitations

This is a single-zone, single-VM laboratory with local SQLite and no high availability. Docker installation is operationally pinned only at the Compose-binary layer; Debian packages are not a byte-identical host image. Secrets require an operator. Cloud NAT is not a domain-filtering proxy. CMEK, automated recovery, multi-region operation and production PRTG connectivity remain out of scope.

**Final state: static validated / real plan not executed / not deployed.**
