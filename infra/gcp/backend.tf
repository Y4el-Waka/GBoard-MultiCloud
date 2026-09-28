terraform {
  # The bucket is intentionally bootstrapped outside this stack. Use
  # backend.example.hcl only after its ownership and security are verified.
  backend "gcs" {}
}
