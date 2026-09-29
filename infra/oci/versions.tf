terraform {
  required_version = "= 1.16.4"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 8.29.0"
    }
  }
}
