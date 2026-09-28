# CoreView AWS laboratory Terraform

This directory prepares the first AWS deployment of the synthetic CoreView
laboratory. It is not production infrastructure and does not contact real PRTG.
Phase 1.0A must not run `apply` or create resources.

## Prerequisites

- Terraform 1.16.4;
- AWS provider 6.61.0 (downloaded by `terraform init`);
- AWS CLI v2 and Session Manager plugin for later Phase 1.0B access;
- an explicitly authorized AWS role and account;
- a separately created, encrypted and versioned S3 state bucket;
- a reviewed RFC1918 VPC/subnet pair with no overlap;
- a reviewed immutable image tag.

Copy the examples; both resulting files are ignored:

```bash
cd infra/aws
cp backend.example.hcl backend.hcl
cp terraform.tfvars.example terraform.tfvars
```

Replace all placeholders locally. Do not put access keys, passwords, passhashes,
application secrets or personal account information in either file.

## Offline structural validation

This initializes only provider plugins and does not configure or contact the S3
backend:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

`terraform init` contacts the HashiCorp provider registry unless its exact
provider is already cached. It does not contact AWS in this mode.

## Authorized read-only plan

Only after selecting an authorized AWS role:

```bash
aws sts get-caller-identity
terraform init -reconfigure -backend-config=backend.hcl
terraform plan -out=coreview-lab.tfplan
terraform show coreview-lab.tfplan
```

Confirm the caller account is in `allowed_account_ids`; review every resource,
the no-ingress security group, IAM JSON, AMI, public-IP choice and monthly
estimate. Record the planned `resolved_ami_id`, set it as `ami_id_override` in
ignored tfvars, create and review a second plan. Plan files are ignored and must
not be shared casually because Terraform artifacts can contain sensitive data.

No command in Phase 1.0A invokes `terraform apply` or `terraform destroy`.

## Inputs requiring operator choice

| Input | Requirement |
| --- | --- |
| `allowed_account_ids` | At least one explicitly authorized 12-digit account ID, kept out of source |
| `vpc_cidr` | Approved non-overlapping RFC1918 `/16`; the stack derives its first `/24` subnet |
| `image_tag` | Immutable reviewed artifact tag, preferably the Git commit SHA |
| `ami_id_override` | Pin after the first plan resolves the official AL2023 parameter |

See `RELEASE_GATE.md`, `DEPLOYMENT_RUNBOOK.md`, `DESTROY_RUNBOOK.md` and
`COST_ESTIMATE.md` before any later deployment. `STATE_BACKEND.md` defines the
separate, explicitly
approved S3 backend bootstrap, least-privilege access and recovery procedure.
