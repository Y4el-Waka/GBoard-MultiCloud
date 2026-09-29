# OCI deployment runbook (future authorized use only)

Phase 1.3B did not execute these commands against OCI. Every mutation below requires a current account/compartment review and explicit approval.

## Approval gates

- **Approval OCI-1 — identity/preflight:** authorized tenancy, compartment, region, CLI security-token profile and operator roles.
- **Approval OCI-2 — backend:** separate private/versioned Object Storage backend and routine state policy.
- **Approval OCI-3 — plan/cost:** immutable OS image, AD/shape capacity, CIDRs, package/checksum inputs, real plan, budget and residual costs.
- **Approval OCI-4 — infrastructure:** apply the saved reviewed plan only.
- **Approval OCI-5 — release:** publish the exact CI-validated `linux/amd64` image to the created OCIR repository.
- **Approval OCI-6 — runtime:** initialize synthetic secrets, start the workload and run smoke tests.

Never combine these approvals or use `-auto-approve`.

## 1. Authorized preflight

Authenticate with a short-lived OCI CLI session outside the repository:

```bash
oci session authenticate \
  --profile-name COREVIEW_LAB \
  --region us-ashburn-1

oci iam tenancy get --profile COREVIEW_LAB
oci iam compartment get \
  --profile COREVIEW_LAB \
  --compartment-id "$APPROVED_COMPARTMENT_OCID"
```

Do not paste complete OCIDs into tickets or public logs. Confirm that both values match the local allow-lists before continuing. Validate policies separately for the Terraform operator, state operator, image publisher and Bastion operator.

Resolve—not create—the availability domain and exact Oracle Linux 9 x86_64 platform image:

```bash
oci iam availability-domain list \
  --profile COREVIEW_LAB \
  --compartment-id "$APPROVED_TENANCY_OCID"

oci compute image list \
  --profile COREVIEW_LAB \
  --compartment-id "$APPROVED_TENANCY_OCID" \
  --operating-system "Oracle Linux" \
  --operating-system-version "9" \
  --shape VM.Standard.E4.Flex \
  --sort-by TIMECREATED \
  --sort-order DESC
```

Manually review the selected image's architecture, launch compatibility, image state and minimum boot size. Put the exact OCID—not a display-name lookup—in ignored `terraform.tfvars`. Check E4 quota/capacity, Bastion limits, Block Volume limits, OCIR limits, non-overlapping CIDRs and current prices.

On a disposable instance using that exact image, resolve Docker repository metadata, exact RPM NEVRAs, the full credential-helper source commit, source archive checksum and reproducible `linux/amd64` binary checksum. Independently review the pinned helper: Oracle documents the pattern, but the referenced implementation is third-party demonstration code. The bootstrap places it behind a wrapper that accepts only the configured OCIR host and rejects static credential storage. Those values are required inputs; placeholders deliberately prevent a plan.

## 2. Backend and local files

Complete the separately approved process in `STATE_BACKEND.md`. Then:

```bash
cp infra/oci/terraform.tfvars.example infra/oci/terraform.tfvars
cp infra/oci/backend.example.hcl infra/oci/backend.hcl
chmod 600 infra/oci/terraform.tfvars infra/oci/backend.hcl

terraform -chdir=infra/oci init \
  -reconfigure \
  -backend-config=backend.hcl
terraform -chdir=infra/oci validate
```

Both copied files are ignored. Never place credentials or application secrets in them.

## 3. Plan and review

```bash
umask 077
terraform -chdir=infra/oci plan \
  -input=false \
  -out=/tmp/coreview-oci.tfplan
terraform -chdir=infra/oci show -no-color /tmp/coreview-oci.tfplan \
  > /tmp/coreview-oci-plan.txt
chmod 600 /tmp/coreview-oci.tfplan /tmp/coreview-oci-plan.txt
```

Approval OCI-3 reviews every resource, replacement, output, IAM statement, route, NSG rule, volume flag, public-IP field, tag and cost. The plan is sensitive and never committed. Reject any public VM address, Internet Gateway, inbound 5067/9090, broad IAM, mutable source image or secret-bearing metadata.

## 4. Apply reviewed infrastructure

Only after Approval OCI-4:

```bash
terraform -chdir=infra/oci apply -input=false /tmp/coreview-oci.tfplan
```

The registry is initially empty. Bootstrap installs the host and enables, but does not start, `coreview-lab.service` because `/opt/coreview/.env` does not exist. Compute creation precedes the exact-instance Dynamic Group and policy without forming a dependency cycle; the first later pull has a bounded ten-minute retry window for IAM propagation and otherwise fails closed. Confirm cloud-init completion through the private administration path before publishing.

## 5. Publish the exact reviewed image

This is a separate Approval OCI-5. Load the private CI artifact, verify its checksum, Image ID, architecture, config and evidence from the same run. Do not rebuild. The publisher needs push only to the CoreView repository and no VM/state permissions.

OCI's standard Docker publisher flow requires an OCIR auth token. Generate one only under separate approval, keep it outside Terraform and use an isolated Docker config; revoke it immediately after publication. Never pass it as a command-line argument.

```bash
export COREVIEW_DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG="$COREVIEW_DOCKER_CONFIG"
read -r -p 'OCIR publisher username: ' OCIR_USER
read -r -s -p 'Short-lived OCIR auth token: ' OCIR_TOKEN
printf '\n'
printf '%s' "$OCIR_TOKEN" | docker login --username "$OCIR_USER" --password-stdin iad.ocir.io
unset OCIR_TOKEN OCIR_USER

docker image inspect coreview-cloud-lab:0.5c
docker tag coreview-cloud-lab:0.5c \
  "iad.ocir.io/$OCI_NAMESPACE/coreview:$RELEASE_SHA"
docker push "iad.ocir.io/$OCI_NAMESPACE/coreview:$RELEASE_SHA"
docker buildx imagetools inspect \
  "iad.ocir.io/$OCI_NAMESPACE/coreview:$RELEASE_SHA"

docker logout iad.ocir.io
rm -rf -- "$COREVIEW_DOCKER_CONFIG"
unset COREVIEW_DOCKER_CONFIG DOCKER_CONFIG
```

Record the returned `linux/amd64` manifest digest in the private release evidence. Revoke the publisher auth token. Runtime selects `repository@sha256:...`; tags are not trusted for execution.

## 6. Create a short-lived Bastion SSH transport

Obtain the private instance IP and Bastion OCID from Terraform without posting them publicly. Create the ephemeral session outside Terraform so it is not persisted in state:

```bash
oci bastion session create-port-forwarding \
  --profile COREVIEW_LAB \
  --bastion-id "$BASTION_OCID" \
  --target-private-ip "$INSTANCE_PRIVATE_IP" \
  --target-port 22 \
  --ssh-public-key-file "$HOME/.ssh/coreview_oci.pub" \
  --session-ttl-in-seconds 3600 \
  --wait-for-state SUCCEEDED
```

Use the `ssh-metadata.command` returned by OCI, replacing only the documented local-port placeholder with `22022`. Conceptually:

```bash
ssh -i "$HOME/.ssh/coreview_oci" -N \
  -L 22022:"$INSTANCE_PRIVATE_IP":22 \
  -p 22 "$BASTION_SESSION_OCID"@host.bastion.us-ashburn-1.oci.oraclecloud.com
```

In a second terminal, use that SSH transport to reach the guest and forward the loopback services:

```bash
ssh -i "$HOME/.ssh/coreview_oci" \
  -p 22022 \
  -N \
  -L 5067:127.0.0.1:5067 \
  -L 9090:127.0.0.1:9090 \
  opc@127.0.0.1
```

This second hop is mandatory: OCI Bastion targets the guest's private port 22, not guest loopback 5067/9090.

## 7. Initialize secrets and start

Through a separate interactive SSH session over local port 22022:

```bash
ssh -i "$HOME/.ssh/coreview_oci" -p 22022 opc@127.0.0.1
sudo cloud-init status --wait
sudo test -f /opt/coreview/.bootstrap-1-complete
sudo systemctl is-enabled coreview-lab.service
sudo test ! -e /opt/coreview/.env
sudo /opt/coreview/bin/initialize-lab-secrets sha256:APPROVED_MANIFEST_DIGEST
```

The initializer asks for the synthetic admin password without echo, generates the Flask secret in the guest, writes a root-only file on the guest boot filesystem outside Terraform metadata, and starts the systemd service. It never prints either secret.

## 8. Smoke and persistence

With the nested forward running:

```bash
curl --fail --silent --show-error http://127.0.0.1:5067/health
curl --fail --silent --show-error http://127.0.0.1:9090/health
```

Use the existing authenticated Docker smoke procedure to prove 13 logical servers, 1,000 sensors, zero collector errors, API v2 families, legacy fallback and pause/resume. Then perform an authorized reboot, verify the runtime volume is mounted before Docker, and repeat the smoke.

## 9. Digest update and rollback

Do not change Terraform `user_data` for releases. Through SSH:

```bash
sudo /opt/coreview/bin/update-runtime-image sha256:NEW_APPROVED_MANIFEST_DIGEST
```

The script restores the previous environment and attempts the prior service on failure. Keep the previous OCIR manifest reachable until post-update validation succeeds.

## 10. End administration

Terminate both SSH processes and explicitly delete/terminate the Bastion session if it has not expired. Remove temporary plans, plan text, auth-token material and isolated Docker config. Preserve only sanitized approval evidence.
