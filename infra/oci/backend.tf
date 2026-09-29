terraform {
  # The Object Storage bucket is bootstrapped and protected independently.
  # Coordinates belong in ignored backend.hcl; authentication stays in the
  # operator environment or short-lived OCI CLI security-token profile.
  backend "oci" {}
}
