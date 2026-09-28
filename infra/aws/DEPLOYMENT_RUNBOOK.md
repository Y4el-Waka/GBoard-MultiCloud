# Phase 1.0B first-deployment runbook

This runbook separates read-only readiness checks from AWS mutations. It does
not authorize `terraform apply`, ECR publication, EC2 start/stop, secret
initialization or destruction. Obtain an explicit approval at every gate below.
Use only the authorized `coreview-lab` profile in `us-east-1`.

## Current bootstrap behavior

The reviewed EC2 user-data can finish while ECR is empty:

1. it installs Docker, curl and OpenSSL;
2. it downloads the checksum-pinned Docker Compose plugin;
3. it writes synthetic-only Compose and server configuration;
4. it installs the ECR login and interactive secret-initialization helpers;
5. it enables Docker, SSM Agent and the CoreView systemd unit.

It does **not** start CoreView. `coreview-lab.service` has
`ConditionPathExists=/opt/coreview/.env`, user-data never creates that file,
and the bootstrap uses `systemctl enable`, not `enable --now`. Therefore an
empty ECR repository does not make cloud-init fail and no image is pulled
prematurely. Amazon Linux 2023 includes AWS CLI v2 and normally includes SSM
Agent; both must still be checked after boot.

The bootstrap contains no application password or `APP_SECRET_KEY`. The image
URI, synthetic configuration and helper source are present in user-data and
Terraform state, but none is a credential. Later, the interactive helper reads
the laboratory password with terminal echo disabled, generates the application
secret locally, writes `/opt/coreview/.env` atomically as root mode `0600`, and
then starts CoreView. Never pass that password through Terraform, user-data,
SSM Run Command, shell arguments, tickets or CI variables.

If image pull/start fails after the helper has written `.env`, do not rerun the
helper: it intentionally refuses to overwrite secrets. Correct the image issue
and run `sudo systemctl start coreview-lab.service` in the same authorized SSM
shell.

## Gate 0: source, CI and image chain of custody (read-only)

Start from a clean, pushed commit:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1
export AWS_PROFILE=coreview-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1

test -z "$(git status --porcelain)"
export DEPLOY_SHA="$(git rev-parse HEAD)"
test "$DEPLOY_SHA" = "$(git rev-parse origin/master)"
test "${#DEPLOY_SHA}" -eq 40
```

Use the GitHub UI or an authenticated GitHub CLI to select a successful
`CoreView container CI` run whose `headSha` equals `DEPLOY_SHA`:

```bash
gh auth status
gh run list --workflow container-ci.yml --commit "$DEPLOY_SHA" \
  --json databaseId,headSha,status,conclusion,url
# Set only after reviewing the result above.
export CI_RUN_ID="<SUCCESSFUL_RUN_ID>"
gh run view "$CI_RUN_ID" --json headSha,status,conclusion,url
```

Download `coreview-container-evidence` and compare the validated image ID with
the local image intended for ECR. The existing workflow records inspection,
SBOM and vulnerability evidence but does not export the image archive:

```bash
umask 077
export CI_EVIDENCE_DIR="$(mktemp -d)"
gh run download "$CI_RUN_ID" --name coreview-container-evidence \
  --dir "$CI_EVIDENCE_DIR"
ci_inspect="$(find "$CI_EVIDENCE_DIR" -path '*/container/image-inspect.json' \
  -type f -print -quit)"
test -n "$ci_inspect"
ci_image_id="$(jq -r '.[0].Id' "$ci_inspect")"
local_image_id="$(docker image inspect coreview-cloud-lab:0.5c \
  --format '{{.Id}}')"
test "$local_image_id" = "$ci_image_id"
unset ci_image_id local_image_id
```

A mismatch is a hard stop. Rebuilding the same commit is not automatically the
same image. The safe follow-up is a separately approved CI change that exports
the tested image as a protected artifact, or an OIDC-based manual publication
job. Do not add AWS credentials to the existing workflow and do not claim an
unverified local rebuild is the CI-tested image.

Set ignored `terraform.tfvars` `image_tag` to the full 40-character
`DEPLOY_SHA`, not a short SHA. ECR is immutable, so a tag must identify one
reviewed image permanently.

**Approval A:** exact pushed commit has green remote CI; SBOM/scan evidence was
reviewed; local image ID matches CI evidence; full SHA is the planned tag.

## Gate 1: budget and caller (read-only)

The workload estimate is approximately USD 20.61/month before variable
transfer, state/version storage, Session Manager logging, tax or retained
resources. A Budget is an alert, not a hard cap.

Review the caller and all budget thresholds without copying the Account ID into
source or reports:

```bash
account_id="$(aws sts get-caller-identity --query Account --output text)"
aws budgets describe-budgets --account-id "$account_id" \
  --query 'Budgets[].{Name:BudgetName,Limit:BudgetLimit,TimeUnit:TimeUnit}'
aws budgets describe-budgets --account-id "$account_id" \
  --query 'Budgets[].BudgetName' --output text | tr '\t' '\n'
# For every reviewed name:
aws budgets describe-notifications-for-budget \
  --account-id "$account_id" --budget-name '<REVIEWED_BUDGET_NAME>'
unset account_id
```

Before EC2 creation, approve a monthly amount above the fixed baseline with
useful actual/forecast thresholds and a verified recipient. Updating a Budget
is a separate AWS mutation and is not part of Terraform. Explicitly accept any
decision to deploy with a lower threshold.

**Approval B:** cost owner accepts baseline/variable charges and Budget alerts
are useful and reachable.

## Gate 2: regenerate and review the definitive plan

Saved plan files contain the complete configuration and must be mode `0600`,
outside Git and retained only through the approval window. Regenerate after the
full image tag or any Terraform/template/operator input changes:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1/infra/aws
chmod 0600 backend.hcl terraform.tfvars
terraform init -reconfigure -backend-config=backend.hcl
terraform fmt -check -recursive
terraform validate

umask 077
export PLAN_DIR="$(mktemp -d)"
export PLAN_FILE="$PLAN_DIR/coreview-first-deployment.tfplan"
terraform plan -out="$PLAN_FILE"
terraform show -no-color "$PLAN_FILE" > "$PLAN_DIR/coreview-first-deployment.txt"
sha256sum "$PLAN_FILE" > "$PLAN_FILE.sha256"
```

`terraform plan` uses a short-lived S3 lockfile; authorize that backend write.
It must still report exactly `16 to add, 0 to change, 0 to destroy`. Verify the
full tag and resource count without displaying the Account ID or repository URL:

```bash
test "$(terraform show -json "$PLAN_FILE" | jq -r '.variables.image_tag.value')" \
  = "$DEPLOY_SHA"
test "$(terraform show -json "$PLAN_FILE" | jq \
  '[.resource_changes[] | select(.change.actions == ["create"])] | length')" \
  -eq 16
terraform show -json "$PLAN_FILE" | jq -r '
  .resource_changes[]
  | select(.change.actions == ["create"])
  | .address' | sort
```

Reconfirm: no security-group ingress; `t3.small`; reviewed AL2023 AMI; no key
pair; IMDSv2 required/hop limit 1; encrypted 20-GiB gp3 with
`delete_on_termination=true`; immutable/non-force-delete ECR; no secret values
in plan output. Keep the plan SHA-256 with the approval record.

**Approval C:** clean commit, exact inputs, 16-create plan, plan hash, IAM,
network, AMI and cost have human approval. A saved-plan apply does not prompt,
so this approval must occur immediately before the next command.

## Operator permission separation

Use separate short-lived roles where the account permits it:

- **Terraform operator:** daily backend S3 permissions from `STATE_BACKEND.md`;
  `sts:GetCallerIdentity`; EC2/VPC read, create and tag operations needed by the
  16 reviewed resources; ECR repository create/read/tag; IAM role, inline
  policy, managed-policy attachment and instance-profile create/read/tag; and
  `iam:PassRole` restricted to the new CoreView instance role. A later destroy
  also needs the corresponding detach/delete operations and is separately
  authorized. Account SCPs, permission boundaries and provider read calls must
  be checked before apply; the read-only preflight did not prove create access.
- **ECR publisher:** only registry authorization, layer upload, `ecr:PutImage`
  and repository/image reads for the one CoreView repository. It needs no EC2,
  IAM, SSM or Terraform-state administration.
- **SSM operator:** instance/managed-node reads plus `ssm:StartSession`,
  `ssm:ResumeSession` and `ssm:TerminateSession`, scoped to the tagged instance
  and approved shell/port-forwarding documents. It needs no ECR push or
  infrastructure-create access.
- **Budget reviewer:** `budgets:ViewBudget`; changing thresholds is a separate,
  explicitly approved billing mutation.

Do not combine these roles merely for convenience and do not store permanent
AWS access keys in Terraform, GitHub, user-data or the host.

## Gate 3: create the reviewed infrastructure (future mutation)

Do not run during plan-only/readiness work:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1/infra/aws
export AWS_PROFILE=coreview-lab AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
sha256sum --check "$PLAN_FILE.sha256"
terraform apply "$PLAN_FILE"
```

Do not substitute an unsaved `terraform apply`, use `-target`, or add
`-auto-approve`. Applying a saved plan already proceeds without an interactive
Terraform confirmation.

After apply, wait for EC2 checks and SSM registration; do not initialize secrets:

```bash
instance_id="$(terraform output -raw instance_id)"
aws ec2 wait instance-status-ok --instance-ids "$instance_id"
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$instance_id" \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

Open an SSM shell and audit bootstrap:

```bash
aws ssm start-session --target "$instance_id"
```

Inside EC2:

```bash
sudo cloud-init status --wait
sudo systemctl status amazon-ssm-agent --no-pager
aws --version
test ! -e /opt/coreview/.env
sudo systemctl is-enabled coreview-lab.service
sudo systemctl is-active coreview-lab.service && exit 1 || true
sudo journalctl -u cloud-final --no-pager -n 200
```

Expected: cloud-init complete, `.env` absent, CoreView enabled but inactive.

**Approval D:** all 16 resources exist as planned, bootstrap is complete, SSM is
healthy, ECR is empty and CoreView has not started.

## Gate 4: publish and verify the exact reviewed image (future mutation)

The publication identity needs `ecr:GetAuthorizationToken` plus
`ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`,
`ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage` and read access
to verify the one repository. It does not need IAM, EC2 or repository-delete
permissions.

```bash
repository_url="$(terraform output -raw ecr_repository_url)"
registry="${repository_url%%/*}"
repository_name="${repository_url#*/}"
remote_ref="$repository_url:$DEPLOY_SHA"

test "$(aws ecr list-images --repository-name "$repository_name" \
  --query 'length(imageIds)' --output text)" -eq 0
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$registry"
docker tag coreview-cloud-lab:0.5c "$remote_ref"

umask 077
push_log="$(mktemp)"
docker push "$remote_ref" | tee "$push_log"
pushed_digest="$(awk '/^digest: sha256:/ {print $2}' "$push_log" | tail -n 1)"
ecr_digest="$(aws ecr describe-images --repository-name "$repository_name" \
  --image-ids imageTag="$DEPLOY_SHA" \
  --query 'imageDetails[0].imageDigest' --output text)"
test -n "$pushed_digest"
test "$pushed_digest" = "$ecr_digest"
printf 'Verified ECR digest: %s\n' "$ecr_digest"
unset repository_url registry remote_ref pushed_digest ecr_digest
```

Protect and later dispose of `push_log`; it contains repository identifiers but
must contain no credentials. The local Docker image ID and ECR manifest digest
are different digest domains and are not expected to match. The chain is:
CI image ID equals local image ID, then the digest emitted by `docker push`
equals the digest returned by ECR.

Review the ECR scan after it completes. An immutable tag cannot be overwritten;
a mismatch requires a new reviewed full-SHA tag and a regenerated Terraform
plan, not tag reuse.

**Approval E:** exact reviewed image exists under the planned immutable tag,
digest comparison passes and scan findings are accepted.

## Gate 5: initialize secrets and start (future host mutation)

The operator requires `ssm:StartSession` scoped to the tagged instance and the
reviewed `SSM-SessionManagerRunShell`/`AWS-StartPortForwardingSession`
documents, plus the corresponding describe, resume and terminate permissions.
The EC2 role remains pull-only.

Start an interactive SSM shell and run:

```bash
sudo cloud-init status --wait
sudo /opt/coreview/bin/initialize-lab-secrets
sudo systemctl status coreview-lab.service --no-pager
sudo docker compose --env-file /opt/coreview/.env \
  --file /opt/coreview/compose.yaml ps
```

Enter a unique synthetic-only password at the hidden prompt. Check Session
Manager account preferences before entry; do not copy credentials into session
commands or logs.

## Gate 6: tunnel and synthetic smoke

Open two SSM tunnels in separate local terminals because the existing smoke
checks both app and simulator loopback endpoints:

```bash
instance_id="$(terraform output -raw instance_id)"
aws ssm start-session --target "$instance_id" \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=5067,localPortNumber=5067'
```

```bash
instance_id="$(terraform output -raw instance_id)"
aws ssm start-session --target "$instance_id" \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=9090,localPortNumber=9090'
```

In a third terminal, read the password without echo and run the existing smoke:

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1
read -r -s -p 'Synthetic lab password: ' COREVIEW_LAB_ADMIN_PASSWORD; printf '\n'
export COREVIEW_LAB_ADMIN_PASSWORD
export COREVIEW_LAB_APP_URL=http://127.0.0.1:5067
export COREVIEW_LAB_SIMULATOR_URL=http://127.0.0.1:9090
python scripts/docker_lab_smoke.py
unset COREVIEW_LAB_ADMIN_PASSWORD
```

Record 13 servers, 1,000 sensors, zero collector errors, login, legacy history,
metadata/history v2 for all five families, pause/resume and persistence. Confirm
zero SG ingress, loopback-only listeners and the real PRTG provider disabled.

**Approval F:** smoke and security acceptance pass. Only then call the first
deployment accepted.

## Stop, start and persistence

Application-only stop/start through SSM preserves Docker volumes and EBS:

```bash
sudo systemctl stop coreview-lab.service
sudo systemctl start coreview-lab.service
```

Stopping EC2 is a separate AWS mutation. It stops compute charges, but the root
EBS volume, ECR images, backend versions and retained logs still cost money. The
ephemeral public IPv4 can change on start; SSM uses the instance ID:

```bash
instance_id="$(terraform output -raw instance_id)"
aws ec2 stop-instances --instance-ids "$instance_id"
aws ec2 wait instance-stopped --instance-ids "$instance_id"
# Later, after approval:
aws ec2 start-instances --instance-ids "$instance_id"
aws ec2 wait instance-status-ok --instance-ids "$instance_id"
```

With `.env` present, the enabled systemd unit starts the stack after reboot and
refreshes the ECR login. Verify SSM and application health again.

## Rollback and failure handling

There is no automatic Terraform rollback:

- **Apply fails partially:** preserve logs/state, run a fresh plan, review it and
  fix forward. Do not reuse a now-stale plan or issue a blind destroy.
- **Cloud-init fails:** CoreView remains stopped. Inspect `cloud-init status
  --long`, `/var/log/cloud-init-output.log`, and relevant journals through SSM;
  correct the minimal cause and replace the instance only through a new plan.
- **Image publication fails:** do not initialize secrets. Because ECR tags are
  immutable, inspect the repository and use a new reviewed tag if any manifest
  was accepted; never overwrite evidence.
- **Start fails after secret creation:** retain `.env`, correct image/runtime,
  then `sudo systemctl start coreview-lab.service`.
- **Smoke fails:** stop `coreview-lab.service`, retain logs and state, and
  diagnose before deciding between fix-forward and destruction.
- **Abandon deployment:** inventory ECR and any data to retain, generate and
  review a saved `terraform plan -destroy`, then apply it only after separate
  destructive approval. Follow `DESTROY_RUNBOOK.md`.

The root EBS volume has `delete_on_termination=true`; destroy loses SQLite,
snapshot, uploads, runtime secrets and host logs unless explicitly backed up.
ECR `force_delete=false` blocks repository destruction while images remain.
The separately bootstrapped versioned S3 backend, its lock/history, Budget,
Session Manager/CloudTrail logs and manual snapshots are outside workload
destroy and require independent retention/cleanup review.
