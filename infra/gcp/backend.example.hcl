# Copy to ignored backend.hcl only after a separately managed bucket has been
# created, ownership verified, public access prevented, versioning enabled and
# IAM restricted to the Terraform state operators.
bucket = "replace-with-approved-unique-state-bucket"
prefix = "coreview-cloud-lab/gcp/lab"
