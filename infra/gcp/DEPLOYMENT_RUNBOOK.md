# GCP deployment runbook (future, approval-gated)

Phase 1.1A did not execute these commands. Every mutating section below requires a future explicit approval, an authorized existing project and review of the current provider documentation.

## Approval GCP-1 — source, account and cost gate

Verify a clean, pushed commit whose CI is green. Review `COST_ESTIMATE.md`, confirm a useful Billing Budget exists, and verify the caller without copying the project number or project ID into public evidence:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1

test -z "$(git status --porcelain)"
export DEPLOY_SHA="$(git rev-parse HEAD)"
test "${#DEPLOY_SHA}" -eq 40

gcloud auth list
gcloud config get-value project
gcloud projects describe PROJECT_ID \
  --format='table(projectId,lifecycleState)'
```

Confirm that `PROJECT_ID` is the intended existing project, billing is enabled, the region/zone are allowed, and no organization policy prevents OS Login, IAP, Shielded VM, service-account attachment, API enablement or Public NAT. The project ID must appear in ignored `terraform.tfvars` both as `project_id` and in `allowed_project_ids`.

Resolve a concrete Debian 12 x86_64 image read-only, review deprecation and architecture, then copy its canonical immutable reference into ignored `terraform.tfvars`:

```bash
IMAGE_NAME="$(gcloud compute images describe-from-family debian-12 \
  --project debian-cloud \
  --format='value(name)')"
printf 'projects/debian-cloud/global/images/%s\n' "$IMAGE_NAME"
unset IMAGE_NAME
```

Review the returned image with `gcloud compute images describe IMAGE_NAME --project debian-cloud` before copying the canonical `projects/debian-cloud/global/images/IMAGE_NAME` reference into ignored `terraform.tfvars`. Do not store a family reference.

Set `image_tag` to the full `DEPLOY_SHA`. Choose an approved, non-overlapping RFC1918 subnet. Never put actual project IDs or operator credentials in versioned files.

**Approval GCP-1:** exact commit, project, region, image, CIDR, budget and operator identity are approved.

## Approval GCP-2 — remote state and read-only plan

Bootstrap and review the separate GCS state bucket according to `STATE_BACKEND.md`; that operation is not part of this stack. Then use Application Default Credentials and the ignored backend/input files:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1
cp infra/gcp/backend.example.hcl infra/gcp/backend.hcl
cp infra/gcp/terraform.tfvars.example infra/gcp/terraform.tfvars
chmod 0600 infra/gcp/backend.hcl infra/gcp/terraform.tfvars

gcloud auth application-default login
terraform -chdir=infra/gcp init \
  -reconfigure \
  -backend-config=backend.hcl
terraform -chdir=infra/gcp fmt -check -recursive
terraform -chdir=infra/gcp validate

umask 077
export PLAN_DIR="$(mktemp -d)"
export PLAN_FILE="$PLAN_DIR/coreview-gcp.tfplan"
terraform -chdir=infra/gcp plan \
  -out="$PLAN_FILE"
terraform -chdir=infra/gcp show -no-color "$PLAN_FILE" \
  > "$PLAN_DIR/coreview-gcp.txt"
sha256sum "$PLAN_FILE" > "$PLAN_FILE.sha256"
```

Review the complete plan: exact project guard; API enablement; no project resource; no external VM IP; only IAP-to-SSH ingress; bounded egress; Cloud NAT; repository-scoped reader role; no service-account key; pinned Debian image; `e2-small`; two 20 GiB `pd-balanced` disks; deletion protection; Shielded VM; immutable Artifact Registry; no secret values.

**Approval GCP-2:** state controls and the complete saved plan/hash are approved. Any input or source change invalidates the plan.

## Approval GCP-3 — create infrastructure

This is the first workload mutation and was not run in Phase 1.1A:

```bash
sha256sum --check "$PLAN_FILE.sha256"
terraform -chdir=infra/gcp apply "$PLAN_FILE"
```

Do not use `-target` or `-auto-approve`. Enabling APIs is part of this apply and requires `serviceusage.services.enable`. The repository is empty and CoreView must remain stopped because `/opt/coreview/.env` does not exist.

Verify the VM has no external address and the startup script completed:

```bash
VM_NAME="$(terraform -chdir=infra/gcp output -raw instance_name)"
ZONE="$(terraform -chdir=infra/gcp output -raw zone)"
gcloud compute instances describe "$VM_NAME" \
  --project PROJECT_ID \
  --zone "$ZONE" \
  --format='table(name,status,networkInterfaces[].networkIP,networkInterfaces[].accessConfigs[].natIP)'

gcloud compute ssh "$VM_NAME" \
  --project PROJECT_ID \
  --zone "$ZONE" \
  --tunnel-through-iap \
  --command='sudo systemctl status google-startup-scripts.service --no-pager; test ! -e /opt/coreview/.env'
```

**Approval GCP-3:** the created resources match the reviewed plan and bootstrap is healthy without runtime secrets.

## Approval GCP-4 — publish the exact reviewed image

Artifact Registry publication is separate from Terraform. Load the exact private CI image artifact for `DEPLOY_SHA`, verify its checksum and Image ID against evidence from the same CI run, then authenticate and push that same local image without rebuilding:

```bash
REGION="$(terraform -chdir=infra/gcp output -raw region)"
IMAGE_REF="$(terraform -chdir=infra/gcp output -raw image_reference)"

sha256sum --check coreview-cloud-lab-image.tar.sha256
docker load --input coreview-cloud-lab-image.tar
docker image inspect coreview-cloud-lab:0.5c \
  --format '{{.Id}} {{.Os}}/{{.Architecture}}'

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker tag coreview-cloud-lab:0.5c "$IMAGE_REF"
docker push "$IMAGE_REF"
gcloud artifacts docker images describe "$IMAGE_REF" \
  --project PROJECT_ID \
  --format='value(image_summary.digest)'
```

A rebuild at the same commit is not automatically equivalent to the image validated by CI. Store the pushed digest with the CI run/attempt evidence. Do not use `latest`.

**Approval GCP-4:** checksum, Image ID, architecture, commit and pushed digest belong to the same reviewed artifact.

## Approval GCP-5 — initialize secrets and start

The operator requires IAP tunnel access, OS Login and, when applicable, `iam.serviceAccounts.actAs` on the attached runtime service account. Open an interactive authorized shell and type a synthetic-only password at the hidden prompt:

```bash
gcloud compute ssh "$VM_NAME" \
  --project PROJECT_ID \
  --zone "$ZONE" \
  --tunnel-through-iap

sudo /opt/coreview/bin/initialize-lab-secrets
sudo systemctl status coreview-lab.service --no-pager
sudo docker compose --env-file /opt/coreview/.env \
  --file /opt/coreview/compose.yaml ps
```

Do not pass the password in command arguments, metadata, Terraform, tickets or logs.

**Approval GCP-5:** exact image exists and the authorized operator may create local runtime secrets/start the lab.

## Approval GCP-6 — tunnel and smoke

Use SSH forwarding through IAP; the firewall remains limited to TCP 22. Keep this session open:

```bash
gcloud compute ssh "$VM_NAME" \
  --project PROJECT_ID \
  --zone "$ZONE" \
  --tunnel-through-iap \
  -- -N \
  -L 5067:127.0.0.1:5067 \
  -L 9090:127.0.0.1:9090
```

In another local shell, enter the same synthetic-only password without saving it and run the repository smoke test:

```bash
read -r -s -p 'Synthetic lab password: ' COREVIEW_LAB_ADMIN_PASSWORD
printf '\n'
export COREVIEW_LAB_ADMIN_PASSWORD
export COREVIEW_LAB_APP_URL='http://127.0.0.1:5067'
export COREVIEW_LAB_SIMULATOR_URL='http://127.0.0.1:9090'
.venv/bin/python scripts/docker_lab_smoke.py
unset COREVIEW_LAB_ADMIN_PASSWORD
```

Expected evidence is synthetic only: 13 logical servers, 1,000 sensors, collector errors 0, authentication, legacy APIs, metadata v2 and Ping/CPU/Memory/Traffic/SFP histories. Sanitize all evidence before retention.

**Approval GCP-6:** smoke and billing/resource inventory are reviewed; the short-lived lab may remain running for the approved window.

## Stop/start

Stopping Compute Engine stops VM compute charges but disks, Artifact Registry, GCS and possibly other retained resources remain billable:

```bash
gcloud compute instances stop "$VM_NAME" --project PROJECT_ID --zone "$ZONE"
gcloud compute instances start "$VM_NAME" --project PROJECT_ID --zone "$ZONE"
```

Both are cloud mutations and require the appropriate operational approval. The systemd unit starts automatically after reboot only when the local secret file remains present.
