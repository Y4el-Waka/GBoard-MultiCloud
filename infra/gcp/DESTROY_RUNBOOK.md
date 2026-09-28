# GCP destruction runbook (future, independently approved)

Phase 1.1A did not execute any destruction. This procedure is destructive and must not be inferred from deployment approval.

## Inventory and backup gate

1. Freeze application use and new Terraform writers.
2. Record the exact project, backend prefix, Terraform workspace, commit and image digest without publishing identifiers.
3. Decide whether the synthetic SQLite database, snapshot, uploads or logs need a disk snapshot/export. Creating a snapshot is a separate billable mutation.
4. Inventory Artifact Registry images and decide which digests must be retained or exported. Repository deletion removes all of its contents; immutable tags prevent treating tagged images as disposable individual objects.
5. Review the runtime disk. Terraform owns it, so the destroy plan will delete it unless the configuration/lifecycle is deliberately changed first.
6. Confirm the GCS state bucket is outside this stack and will remain after workload destroy.

## Disable VM deletion protection through a reviewed change

`deletion_protection` defaults to true. In ignored `terraform.tfvars`, set it to false, produce a normal saved plan that changes only this attribute, review and apply that plan under its own approval. Do not edit state or use a console shortcut.

```bash
umask 077
export UNPROTECT_PLAN="$(mktemp)/coreview-gcp-unprotect.tfplan"
terraform -chdir=infra/gcp plan -out="$UNPROTECT_PLAN"
terraform -chdir=infra/gcp show "$UNPROTECT_PLAN"
terraform -chdir=infra/gcp apply "$UNPROTECT_PLAN"
```

## Decide the repository lifecycle through a reviewed change

The repository has Terraform `lifecycle.prevent_destroy = true`. A complete destroy plan must therefore stop until repository retention has been reviewed.

- To keep the repository, leave the guard in place and do not claim that a full-stack destroy is complete. It and its images may continue billing.
- To delete the repository, export every required digest, remove `prevent_destroy` in a separately reviewed source change, regenerate the plan, and accept that repository deletion removes all contained images.

Do not attempt to delete tagged images individually merely to bypass immutable-tag protection. Do not remove the resource from Terraform state as a teardown shortcut. Any lifecycle change invalidates an older saved plan.

## Destruction plan and approval

```bash
umask 077
export DESTROY_PLAN="$(mktemp)/coreview-gcp-destroy.tfplan"
terraform -chdir=infra/gcp plan -destroy -out="$DESTROY_PLAN"
terraform -chdir=infra/gcp show "$DESTROY_PLAN"
```

The review must identify the VM, boot disk, separate runtime disk, VPC, subnet, Cloud Router/NAT, firewall rules, runtime service account/binding and the selected Artifact Registry lifecycle. Project APIs use `disable_on_destroy=false`, so they remain enabled. The existing GCP project and external GCS backend are not resources in this stack.

After explicit destruction approval:

```bash
terraform -chdir=infra/gcp apply "$DESTROY_PLAN"
```

Do not use an unsaved `terraform destroy -auto-approve`.

## Residual-cost audit

Use Cloud Asset Inventory, Billing reports and read-only `gcloud` listings to verify no CoreView-tagged VM, Persistent Disk, snapshot, NAT address/gateway, Artifact Registry image/repository or unexpected logging sink remains. Review enabled project APIs but do not disable shared APIs automatically.

Separately inspect:

- GCS state objects, versions and lock objects;
- Artifact Registry artifacts intentionally retained;
- disk snapshots or exported evidence;
- Cloud Logging data;
- Billing Budgets and notification channels;
- any manually created IAM bindings for human IAP/OS Login operators.

Only after retention requirements are satisfied may a separately authorized bucket administrator follow `STATE_BACKEND.md` to remove the backend. Deleting workload infrastructure does not delete the existing GCP project or guarantee zero residual billing.
