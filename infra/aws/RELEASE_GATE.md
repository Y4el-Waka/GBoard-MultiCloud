# AWS release evidence and deployment gate

This document defines the evidence required before the first AWS deployment.
It authorizes no AWS mutation.

## CI evidence boundary

The current `CoreView container CI` workflow runs Python and JavaScript tests,
static validation, Docker Compose startup, synthetic smoke, a bounded burst,
restricted soak, offline-browser smoke, persistence checks, SBOM generation
and a Trivy vulnerability gate against `coreview-cloud-lab:0.5c`.

The `coreview-container-evidence` artifact contains, when its producers finish:

- Docker resource snapshots and bounded burst/soak reports;
- offline-browser report and screenshots;
- `container/image-inspect.json` for the tested local image;
- CycloneDX SBOM and the complete Trivy JSON report.

The GitHub run metadata and logs, not the artifact alone, prove which commit was
checked out and whether each workflow step succeeded. The artifact does not
contain the Docker image archive, an OCI registry manifest digest, a signature,
SLSA provenance, the source commit as a separate file, or evidence that any
image was published to ECR.

## Compare CI and local image evidence

Use only a successful run whose full `headSha` equals the clean release commit.
Download the artifact with authenticated GitHub access, then compare stable
image identity and runtime fields:

```bash
export RELEASE_SHA="$(git rev-parse HEAD)"
test "${#RELEASE_SHA}" -eq 40
gh run list --workflow container-ci.yml --commit "$RELEASE_SHA" \
  --json databaseId,headSha,status,conclusion,url
export CI_RUN_ID="<SUCCESSFUL_RUN_ID>"
gh run view "$CI_RUN_ID" --json headSha,status,conclusion,url

umask 077
export CI_EVIDENCE_DIR="$(mktemp -d)"
gh run download "$CI_RUN_ID" --name coreview-container-evidence \
  --dir "$CI_EVIDENCE_DIR"
ci_inspect="$(find "$CI_EVIDENCE_DIR" -path '*/container/image-inspect.json' \
  -type f -print -quit)"
test -n "$ci_inspect"

jq -S '.[0] | {Id,Os,Architecture,Variant,Config,RootFS}' "$ci_inspect" \
  > "$CI_EVIDENCE_DIR/ci-image.normalized.json"
docker image inspect coreview-cloud-lab:0.5c \
  | jq -S '.[0] | {Id,Os,Architecture,Variant,Config,RootFS}' \
  > "$CI_EVIDENCE_DIR/local-image.normalized.json"
diff --unified "$CI_EVIDENCE_DIR/ci-image.normalized.json" \
  "$CI_EVIDENCE_DIR/local-image.normalized.json"
```

An empty diff demonstrates the same Docker image ID, operating system,
architecture, runtime configuration and uncompressed root-filesystem layer
identities. It does not demonstrate that compressed registry layer bytes or an
eventual ECR manifest digest are identical. That registry digest exists only
after publication and must later be compared between `docker push` output and
ECR `DescribeImages`.

Any mismatch is a hard stop. Do not call two images equivalent merely because
they were built from the same commit or produce the same application behavior.

## Alternatives when the local image does not match

Neither alternative is implemented or authorized by this release gate.

### A. Controlled CI image export

A separately approved manual workflow could run only after all test/scan steps,
export the tested image with `docker image save`, create a deterministic
compressed archive, record its SHA-256, image ID and full source commit, and
upload it as a short-retention, access-controlled private artifact. The
operator would verify the archive checksum and loaded image ID before ECR push.
The storage impact and private-code exposure must be approved first.

### B. Manual CI-to-ECR publication with OIDC

After Terraform creates ECR, a separately approved `workflow_dispatch` job
could obtain short-lived AWS credentials through GitHub OIDC and publish the
already-tested image. Trust must be restricted to the exact repository,
workflow/ref and protected GitHub environment. The AWS role should permit only
ECR authorization, layer upload, `PutImage` and read-back verification on the
single repository. No static AWS secret, automatic push trigger, infrastructure
permission or mutable tag is acceptable.

## Release SHA and local operator input

After the release commit exists, update only ignored `terraform.tfvars`:

```bash
export RELEASE_SHA="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
test "${#RELEASE_SHA}" -eq 40
# Edit infra/aws/terraform.tfvars and set:
# image_tag = "<the complete RELEASE_SHA>"
chmod 0600 infra/aws/terraform.tfvars
test "$(sed -n 's/^[[:space:]]*image_tag[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
  infra/aws/terraform.tfvars)" = "$RELEASE_SHA"
git check-ignore --quiet infra/aws/terraform.tfvars
```

Never add `terraform.tfvars`, `backend.hcl`, state, lock objects or plan files
to Git.

## Read-only release plan

Do not reuse a plan containing the old short tag. During this no-AWS-mutation
gate, generate a read-only review plan with backend locking explicitly disabled.
Only do this while no other Terraform operator is active:

```bash
cd infra/aws
export AWS_PROFILE=coreview-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
umask 077
export RELEASE_PLAN_DIR="$(mktemp -d)"
export RELEASE_PLAN="$RELEASE_PLAN_DIR/coreview-release-readonly.tfplan"

terraform init -reconfigure -backend-config=backend.hcl
terraform validate
terraform plan -lock=false -out="$RELEASE_PLAN"
terraform show -no-color "$RELEASE_PLAN" \
  > "$RELEASE_PLAN_DIR/coreview-release-readonly.txt"
sha256sum "$RELEASE_PLAN" > "$RELEASE_PLAN.sha256"
test "$(terraform show -json "$RELEASE_PLAN" \
  | jq -r '.variables.image_tag.value')" = "$RELEASE_SHA"
```

This plan is evidence for read-only review, not the final apply artifact. After
deployment approval, regenerate the definitive plan with normal S3 locking,
review `16 to add, 0 to change, 0 to destroy`, record its hash, and apply only
that approved plan.

## Budget choices

The fixed baseline is approximately USD 20.61/month before variable transfer,
state versions, logs, taxes and residual resources. Practical planning choices:

- a one-day experiment is roughly USD 0.67 of hourly EC2/IPv4/EBS baseline;
- a continuously running week is roughly USD 4.75;
- a continuously running month needs at least the USD 20.61 baseline, so a
  USD 25–30 alerting budget leaves a small operational margin.

These are estimates, not caps. Stopping EC2 removes compute usage and releases
its auto-assigned public IPv4, but EBS, ECR, S3 state versions and logs persist.
Use actual-cost and forecast alerts appropriate to the approved duration.

## Final release checklist

- [ ] Git tree clean; no sensitive/operator files tracked.
- [ ] Full 40-character release SHA recorded and pushed.
- [ ] Remote CI run `headSha` equals the release SHA and succeeds.
- [ ] CI artifact and workflow logs retained for the approval window.
- [ ] Image ID, OS, architecture, Config and RootFS comparison is exact.
- [ ] Any image mismatch is resolved through an explicitly approved alternative.
- [ ] Ignored `terraform.tfvars` contains the full release SHA.
- [ ] Old short-tag plan is retired; new read-only plan reports 16/0/0.
- [ ] Budget/duration and alert recipients are approved.
- [ ] Creation, ECR publisher and SSM operator permissions are verified.
- [ ] No Terraform apply, ECR push or secret initialization has occurred.
