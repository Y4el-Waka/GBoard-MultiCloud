# Proposed GCS Terraform state backend

The backend bucket is intentionally outside the workload stack and was **not created** in Phase 1.1A. The Terraform GCS backend requires a pre-existing bucket, supports state locking, and HashiCorp recommends Object Versioning for recovery.

## Required bucket controls

Under a separate approval, an authorized bootstrap administrator should create one globally unique regional bucket and verify:

- Public Access Prevention enforced;
- uniform bucket-level access enabled;
- Google-managed encryption or an independently approved CMEK design;
- Object Versioning enabled;
- no automatic expiration of the current state;
- a reviewed retention/lifecycle policy for non-current versions;
- incomplete multipart upload cleanup where supported by the chosen tooling;
- labels identifying project, environment and synthetic-only data;
- no public or all-authenticated-users IAM members.

The state prefix is `coreview-cloud-lab/gcp/lab`. `backend.hcl`, local state, plans and `terraform.tfvars` are ignored by Git.

## Separation of duties

The bootstrap identity owns bucket creation, security controls, lifecycle and final deletion. Routine Terraform operators receive only the object access needed by the GCS backend, normally `roles/storage.objectAdmin` scoped to this bucket, after reviewing whether organization policy requires a narrower custom role. They do not need bucket administration.

Use Application Default Credentials or workload identity. Never create or commit a service-account JSON key.

## Future initialization

After bucket ownership and controls have been independently verified:

```bash
cp infra/gcp/backend.example.hcl infra/gcp/backend.hcl
terraform -chdir=infra/gcp init \
  -reconfigure \
  -backend-config=backend.hcl
```

No backend operation was performed in this phase.

## Recovery

1. Stop all Terraform writers and confirm no legitimate operation owns the lock.
2. List object generations for the state path without changing them.
3. Download the current and candidate previous generation to an access-controlled temporary directory.
4. Verify JSON structure and compare resources/serial/lineage without publishing contents.
5. Restore only through the documented GCS generation-recovery workflow under dual review.
6. Run `terraform plan -refresh-only` and then a normal plan; do not apply until drift is understood.
7. Record the restored generation and preserve evidence without retaining state in Git.

Never use `force-unlock` until the lock owner is proven stale.

## Independent destruction

Workload `terraform destroy` does not remove this bucket. After the workload is gone and retention requirements are satisfied, a bucket administrator must review every state version, lock object and residual object, export any required evidence, remove versions deliberately and delete the bucket. Confirm billing and Asset Inventory afterward. This is a distinct approval from workload destruction.
