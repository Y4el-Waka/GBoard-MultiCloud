# Terraform state backend bootstrap

This runbook prepares the external S3 backend for the CoreView synthetic AWS
laboratory. The bucket is deliberately outside the workload Terraform stack so
that workload destruction cannot silently remove state or recovery history.

No bucket name, account ID, role ARN or credentials belong in version control.
Use an explicitly authorized short-lived AWS session. These commands must not
run in CI or application startup.

## Proposed bucket name

Generate a non-identifying, globally unique candidate locally:

```bash
export COREVIEW_STATE_BUCKET="coreview-cloud-lab-tfstate-$(openssl rand -hex 8)"
```

The accepted pattern is
`coreview-cloud-lab-tfstate-<16-lowercase-hex>`. Record the approved name in the
operational secret store, not Git. The random suffix avoids putting an AWS
account ID into the globally visible bucket name.

## Safe inspection and explicit creation

The bootstrap script defaults to read-only inspection and refuses to adopt or
modify any existing bucket. A `403` or otherwise ambiguous `HeadBucket` result
is a hard stop; only a conclusive `404` permits an approved creation attempt.
Even an existing bucket controlled by the current account is not modified.

```bash
cd /path/to/GBOARD-CLOUD-LAB-v0.1
export AWS_PROFILE=coreview-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export COREVIEW_STATE_BUCKET="coreview-cloud-lab-tfstate-<approved-random-suffix>"

# Read-only identity, existence/ownership and configuration verification.
infra/aws/scripts/bootstrap_state_backend.sh --check "$COREVIEW_STATE_BUCKET"
```

Only after a separate explicit approval:

```bash
export COREVIEW_STATE_BACKEND_APPROVED=YES
infra/aws/scripts/bootstrap_state_backend.sh --execute "$COREVIEW_STATE_BUCKET"
unset COREVIEW_STATE_BACKEND_APPROVED
```

`--execute` performs, in order:

1. `sts:GetCallerIdentity`;
2. `s3:HeadBucket` with the expected account owner;
3. `s3:CreateBucket` in `us-east-1`;
4. `s3:PutBucketPublicAccessBlock` with all four controls enabled;
5. `s3:PutBucketOwnershipControls` using `BucketOwnerEnforced`;
6. `s3:PutEncryptionConfiguration` using SSE-S3 (`AES256`);
7. `s3:PutBucketVersioning` with `Enabled`;
8. `s3:PutBucketPolicy` denying all non-TLS requests;
9. `s3:PutBucketTagging` with synthetic CoreView laboratory tags;
10. `s3:PutLifecycleConfiguration`;
11. read-only verification of every control.

The lifecycle expires only noncurrent versions below the CoreView state prefix
after 90 days and aborts incomplete multipart uploads after seven days. It has
no current-version expiration. Ninety days supplies multiple recovery windows
without retaining every superseded state indefinitely. A different retention
period requires separate review because expired versions cannot be recovered.

If a post-creation operation fails, the script stops and does not attempt an
automatic rollback. Re-run `--check`, inspect the partially configured bucket
and authorize only narrowly scoped remediation. Do not delete it reflexively.

## Separation of IAM permissions

The temporary bootstrap identity needs the create/configure operations above
plus corresponding `Get*`/`List*` verification operations. Stop assuming that
elevated role after verification.

The daily Terraform backend identity needs only the permissions HashiCorp
documents for the default workspace and native S3 locking. Replace
`<BUCKET_NAME>` locally and attach this policy only to the approved role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOnlyTheCoreViewStatePath",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<BUCKET_NAME>",
      "Condition": {
        "StringEquals": {
          "s3:prefix": [
            "coreview-cloud-lab/aws/lab/terraform.tfstate",
            "coreview-cloud-lab/aws/lab/terraform.tfstate.tflock"
          ]
        }
      }
    },
    {
      "Sid": "ReadWriteStateWithoutDelete",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/coreview-cloud-lab/aws/lab/terraform.tfstate"
    },
    {
      "Sid": "ManageNativeLockFile",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/coreview-cloud-lab/aws/lab/terraform.tfstate.tflock"
    }
  ]
}
```

Terraform does not need `s3:DeleteObject` on the state itself. Recovery access
such as `s3:ListBucketVersions` and `s3:GetObjectVersion` belongs to a separate
break-glass role. Bucket deletion, lifecycle changes, policy changes and version
purges are excluded from daily access.

The TLS-deny bucket policy grants no access. With Block Public Access and ACLs
disabled, an explicit identity or resource policy is still required. Do not add
wildcard `Allow` statements.

## Backend configuration

Copy the versioned example to the ignored operator file:

```bash
cd infra/aws
cp backend.example.hcl backend.hcl
chmod 0600 backend.hcl
```

Set only the approved bucket name:

```hcl
bucket       = "<APPROVED_BUCKET_NAME>"
key          = "coreview-cloud-lab/aws/lab/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
```

No access key, secret key, session token, Account ID or role credential belongs
in this file. Authentication comes from the authorized AWS profile/session.
`backend.hcl`, state files, common plan suffixes and per-operator tfvars are
ignored by Git.

## Read-only verification

After creation, `--check` verifies Region, expected owner, Block Public Access,
Object Ownership, encryption, versioning, TLS policy, tags and lifecycle. These
independent commands are also read-only:

```bash
aws --profile coreview-lab --region us-east-1 s3api head-bucket \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-public-access-block \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-bucket-ownership-controls \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-bucket-encryption \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-bucket-versioning \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-bucket-policy-status \
  --bucket "$COREVIEW_STATE_BUCKET"
aws --profile coreview-lab --region us-east-1 s3api get-bucket-lifecycle-configuration \
  --bucket "$COREVIEW_STATE_BUCKET"
```

Then initialize without automatic state migration:

```bash
terraform init -reconfigure -backend-config=backend.hcl
```

If Terraform proposes migrating unexpected local state, stop and inspect it.

## Operator variables before the definitive plan

Create ignored `terraform.tfvars` locally:

```hcl
allowed_account_ids = ["<AUTHORIZED_ACCOUNT_ID>"]
vpc_cidr            = "<APPROVED_NON_OVERLAPPING_RFC1918_/16>"
ami_id_override     = "<REVIEWED_AL2023_AMI_ID>"
image_tag           = "<IMMUTABLE_REVIEWED_COMMIT_OR_DIGEST_TAG>"
```

Retain the already reviewed account, independently approve the candidate CIDR,
pin the resolved AL2023 AMI and replace the temporary image tag with the exact
validated commit tag. Keep this file mode `0600` and outside Git.

## Version recovery

Recovery is a controlled write and must not run concurrently with Terraform:

1. Stop Terraform operations and confirm no active lock owner.
2. With a break-glass role, list versions for exactly the state key.
3. Download the current state and selected historical version into protected
   mode-0600 local files; never print them.
4. Validate JSON and compare Terraform lineage and serial values.
5. Upload the reviewed historical content as a new current version. Do not
   delete newer versions merely to roll back.
6. Run `terraform state pull`, `terraform plan -refresh-only` and a normal plan
   before any apply.

Read-only inventory:

```bash
aws s3api list-object-versions \
  --bucket "$COREVIEW_STATE_BUCKET" \
  --prefix coreview-cloud-lab/aws/lab/terraform.tfstate
```

Retrieve a selected version with `s3api get-object --version-id` into a protected
temporary file. Restoring it with `s3api put-object` is a separate, explicitly
approved mutation.

## Independent final cleanup

`terraform destroy` does not own this bucket and must not delete it. After the
workload is gone:

1. preserve final state and required audit evidence;
2. inventory all versions and delete markers for the exact state and lock keys;
3. review lifecycle history and retained evidence;
4. explicitly delete approved versions and markers by version ID;
5. verify the bucket is empty;
6. delete the bucket with a separately authorized break-glass role;
7. verify no bucket, versions, lock object or residual S3 charge remains.

Do not use an unreviewed recursive deletion command. Versioned buckets require
deleting every version and delete marker, not only current keys. Bucket cleanup
is an independent destructive operation and is never implied by workload
`terraform destroy`.

## Cost boundary

EC2, EBS, public IPv4 and ECR form the approximately USD 20.61/month workload
baseline. The state bucket persists after workload destroy. S3 Standard storage
in `us-east-1` is approximately USD 0.023 per retained GiB-month, plus request
charges. A small state normally costs substantially less than one cent monthly,
but versions, recovery copies and audit retention accumulate independently.
AWS Budgets provides notifications; it is not a hard spending limit.
