#!/usr/bin/env bash
set -euo pipefail

readonly aws_region="us-east-1"
readonly state_key="coreview-cloud-lab/aws/lab/terraform.tfstate"
readonly noncurrent_retention_days="90"

usage() {
  cat <<'USAGE'
Usage:
  AWS_PROFILE=coreview-lab bootstrap_state_backend.sh --check <bucket-name>
  AWS_PROFILE=coreview-lab COREVIEW_STATE_BACKEND_APPROVED=YES \
    bootstrap_state_backend.sh --execute <bucket-name>

Bucket names must match:
  coreview-cloud-lab-tfstate-<16 lowercase hexadecimal characters>

--check performs read-only inspection. --execute creates and configures only a
bucket that is conclusively absent. It refuses to adopt or modify an existing
bucket, including one owned by the current account.
USAGE
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 ]] || {
  usage >&2
  exit 2
}

readonly mode="$1"
readonly bucket_name="$2"

[[ "$mode" == "--check" || "$mode" == "--execute" ]] || fail "mode must be --check or --execute"
[[ "$bucket_name" =~ ^coreview-cloud-lab-tfstate-[0-9a-f]{16}$ ]] || fail "bucket name does not match the approved pattern"
: "${AWS_PROFILE:?Set AWS_PROFILE to the explicitly authorized profile}"

if [[ -n "${AWS_REGION:-}" && "$AWS_REGION" != "$aws_region" ]]; then
  fail "AWS_REGION must be $aws_region"
fi
if [[ -n "${AWS_DEFAULT_REGION:-}" && "$AWS_DEFAULT_REGION" != "$aws_region" ]]; then
  fail "AWS_DEFAULT_REGION must be $aws_region"
fi

command -v aws >/dev/null || fail "AWS CLI is required"
command -v python3 >/dev/null || fail "python3 is required for verification"

readonly aws_cli=(aws --profile "$AWS_PROFILE" --region "$aws_region" --no-cli-pager)
account_id="$("${aws_cli[@]}" sts get-caller-identity --query Account --output text)"
[[ "$account_id" =~ ^[0-9]{12}$ ]] || fail "AWS did not return a valid account identifier"
readonly account_id

tmp_dir="$(mktemp -d /tmp/coreview-state-backend.XXXXXX)"
chmod 0700 "$tmp_dir"
trap 'rm -rf -- "$tmp_dir"' EXIT

cat >"$tmp_dir/encryption.json" <<'JSON'
{
  "Rules": [
    {
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }
  ]
}
JSON

cat >"$tmp_dir/tags.json" <<'JSON'
{
  "TagSet": [
    {"Key": "Project", "Value": "coreview-cloud-lab"},
    {"Key": "Environment", "Value": "lab"},
    {"Key": "Owner", "Value": "coreview-lab"},
    {"Key": "ManagedBy", "Value": "manual-bootstrap"},
    {"Key": "DataClass", "Value": "terraform-state"}
  ]
}
JSON

cat >"$tmp_dir/lifecycle.json" <<JSON
{
  "Rules": [
    {
      "ID": "RetainNoncurrentStateVersions${noncurrent_retention_days}Days",
      "Status": "Enabled",
      "Filter": {"Prefix": "coreview-cloud-lab/aws/lab/"},
      "NoncurrentVersionExpiration": {
        "NoncurrentDays": ${noncurrent_retention_days}
      }
    },
    {
      "ID": "AbortIncompleteMultipartUploads7Days",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "AbortIncompleteMultipartUpload": {
        "DaysAfterInitiation": 7
      }
    }
  ]
}
JSON

cat >"$tmp_dir/bucket-policy.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${bucket_name}",
        "arn:aws:s3:::${bucket_name}/*"
      ],
      "Condition": {
        "Bool": {"aws:SecureTransport": "false"}
      }
    }
  ]
}
JSON

bucket_presence() {
  local status
  set +e
  "${aws_cli[@]}" s3api head-bucket \
    --bucket "$bucket_name" \
    --expected-bucket-owner "$account_id" \
    >"$tmp_dir/head.stdout" 2>"$tmp_dir/head.stderr"
  status=$?
  set -e

  if [[ $status -eq 0 ]]; then
    printf 'owned\n'
  elif grep -Eqi '404|NoSuchBucket|Not Found' "$tmp_dir/head.stderr"; then
    printf 'absent\n'
  else
    printf 'ambiguous\n'
  fi
}

verify_bucket() {
  local value

  value="$("${aws_cli[@]}" s3api get-bucket-location \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query LocationConstraint --output text)"
  [[ "$value" == "None" || "$value" == "null" ]] || fail "bucket is not in us-east-1"

  value="$("${aws_cli[@]}" s3api get-public-access-block \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query 'PublicAccessBlockConfiguration.[BlockPublicAcls,IgnorePublicAcls,BlockPublicPolicy,RestrictPublicBuckets]' \
    --output text)"
  [[ "$value" == $'True\tTrue\tTrue\tTrue' ]] || fail "Block Public Access is incomplete"

  value="$("${aws_cli[@]}" s3api get-bucket-ownership-controls \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query 'OwnershipControls.Rules[0].ObjectOwnership' --output text)"
  [[ "$value" == "BucketOwnerEnforced" ]] || fail "Object Ownership is not BucketOwnerEnforced"

  value="$("${aws_cli[@]}" s3api get-bucket-encryption \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
    --output text)"
  [[ "$value" == "AES256" ]] || fail "default encryption is not SSE-S3"

  value="$("${aws_cli[@]}" s3api get-bucket-versioning \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query Status --output text)"
  [[ "$value" == "Enabled" ]] || fail "bucket versioning is not enabled"

  "${aws_cli[@]}" s3api get-bucket-policy \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --query Policy --output text >"$tmp_dir/actual-policy.json"
  "${aws_cli[@]}" s3api get-bucket-lifecycle-configuration \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --output json >"$tmp_dir/actual-lifecycle.json"
  "${aws_cli[@]}" s3api get-bucket-tagging \
    --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
    --output json >"$tmp_dir/actual-tags.json"

  python3 - "$tmp_dir/actual-policy.json" "$tmp_dir/actual-lifecycle.json" "$tmp_dir/actual-tags.json" <<'PY'
import json
import sys

policy = json.load(open(sys.argv[1], encoding="utf-8"))
lifecycle = json.load(open(sys.argv[2], encoding="utf-8"))
tags = json.load(open(sys.argv[3], encoding="utf-8"))

tls_denies = [
    statement
    for statement in policy.get("Statement", [])
    if statement.get("Effect") == "Deny"
    and statement.get("Action") == "s3:*"
    and statement.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
]
if not tls_denies:
    raise SystemExit("TLS-only bucket policy is missing")

rules = {rule.get("ID"): rule for rule in lifecycle.get("Rules", [])}
retention = rules.get("RetainNoncurrentStateVersions90Days", {})
if retention.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays") != 90:
    raise SystemExit("90-day noncurrent-version retention is missing")
if "Expiration" in retention:
    raise SystemExit("current state versions must not expire automatically")

multipart = rules.get("AbortIncompleteMultipartUploads7Days", {})
if multipart.get("AbortIncompleteMultipartUpload", {}).get("DaysAfterInitiation") != 7:
    raise SystemExit("incomplete multipart cleanup is missing")

actual_tags = {item["Key"]: item["Value"] for item in tags.get("TagSet", [])}
expected_tags = {
    "Project": "coreview-cloud-lab",
    "Environment": "lab",
    "Owner": "coreview-lab",
    "ManagedBy": "manual-bootstrap",
    "DataClass": "terraform-state",
}
if any(actual_tags.get(key) != value for key, value in expected_tags.items()):
    raise SystemExit("required CoreView state tags are missing")
PY

  printf 'Bucket security controls verified for the authorized account and %s.\n' "$aws_region"
  printf 'State key: %s\n' "$state_key"
}

presence="$(bucket_presence)"
readonly presence

if [[ "$mode" == "--check" ]]; then
  case "$presence" in
    absent)
      printf 'Bucket name is currently absent and available for an approved bootstrap attempt.\n'
      ;;
    owned)
      verify_bucket
      ;;
    ambiguous)
      fail "bucket existence or ownership is ambiguous; do not create or adopt it"
      ;;
  esac
  exit 0
fi

[[ "${COREVIEW_STATE_BACKEND_APPROVED:-}" == "YES" ]] || fail "set COREVIEW_STATE_BACKEND_APPROVED=YES only after explicit approval"
[[ "$presence" == "absent" ]] || fail "refusing to create, adopt or modify a bucket that is not conclusively absent"

"${aws_cli[@]}" s3api create-bucket --bucket "$bucket_name"
"${aws_cli[@]}" s3api put-public-access-block \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
"${aws_cli[@]}" s3api put-bucket-ownership-controls \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
"${aws_cli[@]}" s3api put-bucket-encryption \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --server-side-encryption-configuration "file://$tmp_dir/encryption.json"
"${aws_cli[@]}" s3api put-bucket-versioning \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --versioning-configuration Status=Enabled
"${aws_cli[@]}" s3api put-bucket-policy \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --policy "file://$tmp_dir/bucket-policy.json"
"${aws_cli[@]}" s3api put-bucket-tagging \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --tagging "file://$tmp_dir/tags.json"
"${aws_cli[@]}" s3api put-bucket-lifecycle-configuration \
  --bucket "$bucket_name" --expected-bucket-owner "$account_id" \
  --lifecycle-configuration "file://$tmp_dir/lifecycle.json"

verify_bucket
printf 'Backend bucket bootstrap completed. Configure ignored backend.hcl only after review.\n'
