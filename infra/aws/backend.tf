terraform {
  # Supply the bucket, key, Region, encryption and lockfile settings through
  # an ignored backend.hcl. The state bucket is intentionally bootstrapped
  # outside this stack so it can outlive and protect this laboratory state.
  backend "s3" {}
}
