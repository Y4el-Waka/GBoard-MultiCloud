# CoreView Cloud Lab on Google Cloud

This directory contains the frozen Phase 1.1 GCP blueprint. It is independent from `infra/aws/` and targets an existing GCP project; it does not create a project.

Status:

- architecture implemented;
- local Terraform static validation completed;
- Phase 1.1B offline review completed;
- no GCP credentials used;
- no real GCP plan or deployment executed.

## Layout

| File | Responsibility |
|---|---|
| `versions.tf`, `.terraform.lock.hcl` | Exact Terraform/provider selection |
| `providers.tf` | Google provider and explicit project guard |
| `services.tf` | Required project APIs, preserved on destroy |
| `networking.tf` | Custom VPC, private subnet, Cloud Router/NAT and firewall policy |
| `iam.tf` | Keyless runtime service account and repository-scoped pull access |
| `artifact_registry.tf` | Private regional immutable Docker repository protected against accidental deletion |
| `compute.tf` | Shielded private VM plus persistent runtime disk |
| `templates/` | Secretless host bootstrap and production-like Compose topology |
| `backend.tf`, `backend.example.hcl` | Proposed pre-existing GCS backend |
| `DEPLOYMENT_RUNBOOK.md` | Future approval-gated deployment procedure |
| `DESTROY_RUNBOOK.md` | Future teardown and residual-cost procedure |
| `STATE_BACKEND.md` | Separate state bootstrap and recovery design |
| `COST_ESTIMATE.md` | Dated estimate and assumptions |

## Required local inputs

Copy `terraform.tfvars.example` to the ignored `terraform.tfvars` only for a future authorized plan. Required values are:

- `project_id`: one existing billing-enabled project;
- `allowed_project_ids`: explicit allow-list containing that project;
- `subnet_cidr`: approved, non-overlapping RFC1918 /24 through /28;
- `boot_image`: canonical immutable Debian 12 x86_64 image reference, never an image family;
- `image_tag`: immutable CI-reviewed image tag, preferably the full Git SHA.

No credential file, service-account key or secret belongs in Terraform variables.

## Static validation without credentials

```bash
terraform -chdir=infra/gcp fmt -check -recursive
terraform -chdir=infra/gcp init -backend=false -input=false
terraform -chdir=infra/gcp validate
.venv/bin/python -m unittest tests.test_gcp_terraform -v
```

These commands download and validate the provider locally. They do not prove IAM, quota, organization policy or real plan behavior.

The existing pinned Trivy 0.74.0 image was also run with networking disabled. After enabling VPC Flow Logs, its embedded Terraform checks reported no Critical, High or Medium findings and three accepted Low findings for the deliberate Google-managed-encryption/CMEK decision. No ignore file was added. The offline run lacked a current downloadable policy cache, so repeat the scan with a reviewed current policy bundle before a real plan.

## Administrative access model

The VM has no external IP. An authorized operator uses IAP TCP forwarding and OS Login:

```bash
gcloud compute ssh VM_NAME \
  --project PROJECT_ID \
  --zone us-east1-b \
  --tunnel-through-iap
```

Then the dashboard can be forwarded from its VM-loopback listener:

```bash
gcloud compute ssh VM_NAME \
  --project PROJECT_ID \
  --zone us-east1-b \
  --tunnel-through-iap \
  -- -N -L 5067:127.0.0.1:5067
```

IAP TCP forwarding is not the same service or network model as AWS Session Manager. IAP reaches `sshd` through Google's IAP proxy fleet, so the stack has one ingress rule: TCP 22 from the documented IAP proxy range, targeted to the runtime service account. OS Login maps IAM-authorized identities into the guest. No rule exposes 5067 or 9090.

## Runtime contract

The GCP Compose template preserves the local/AWS lab behavior:

- `COREVIEW_METRICS_PROVIDER=synthetic`;
- app and collector share the simulator network namespace;
- provider destinations remain loopback-only;
- host ports 5067 and 9090 bind to loopback;
- non-root containers, read-only roots, all capabilities dropped and `no-new-privileges`;
- measured memory/CPU limits from Phase 0.5;
- named volumes stored under `/var/lib/docker` on the attached runtime disk.

The host requires the runtime disk mount before Docker starts. Its persistent `fstab` entry deliberately omits `nofail`; a missing disk therefore fails closed instead of placing a new Docker state on the boot disk.

Startup is revisioned by `bootstrap_revision`. A successful run writes a root-only marker, repeated GCE startup-script invocations exit without reinstalling packages or stopping Docker, and a failed run leaves no marker so the next boot can retry. Increment the revision only for a reviewed bootstrap change.

The systemd unit has `ConditionPathExists=/opt/coreview/.env`. Bootstrap cannot start the application before a reviewed image is available and an operator has initialized secrets interactively. An empty Artifact Registry therefore leaves CoreView stopped rather than causing a premature image pull.
