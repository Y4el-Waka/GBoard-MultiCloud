# Copy to ignored backend.hcl after an independently approved bootstrap.
bucket              = "replace-with-approved-state-bucket"
namespace           = "replace-with-tenancy-object-storage-namespace"
key                 = "coreview-cloud-lab/oci/lab/terraform.tfstate"
region              = "us-ashburn-1"
auth                = "SecurityToken"
config_file_profile = "COREVIEW_LAB"
