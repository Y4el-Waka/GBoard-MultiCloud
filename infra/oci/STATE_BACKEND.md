# OCI Object Storage Terraform state backend

The backend is external to this stack and must not be created while applying the workload configuration. Phase 1.3B created no bucket.

Terraform 1.16's native `oci` backend stores the state object in OCI Object Storage and creates a sibling `.lock` object using conditional object creation. HashiCorp strongly recommends bucket versioning for recovery.

## Proposed object layout

```text
bucket:    globally unique approved name
namespace: tenancy Object Storage namespace
state:     coreview-cloud-lab/oci/lab/terraform.tfstate
lock:      coreview-cloud-lab/oci/lab/terraform.tfstate.lock
region:    us-ashburn-1
```

`backend.hcl` and all local Terraform state/plans are ignored by Git. Copy `backend.example.hcl` only after the bucket exists. Use short-lived `SecurityToken` authentication; do not add user OCIDs, fingerprints or private-key paths to the repository.

## Separate bootstrap checklist

An authorized backend administrator—not the routine workload stack—must:

1. verify the active tenancy and region against an approved allow-list;
2. choose a globally unique bucket name and confirm no same-named bucket is being adopted accidentally;
3. create a private bucket in `us-ashburn-1` with default server-side encryption;
4. enable Object Storage versioning;
5. apply lifecycle rules that abort incomplete multipart uploads and retain non-current state versions for a reviewed period (recommended starting point: 90 days); never expire the current state automatically;
6. grant the routine backend group only object inspect/create/read/delete operations on the named bucket;
7. deny or avoid public access and verify the bucket's public-access type;
8. record ownership, recovery and final-retention approvals outside Git.

The exact OCI CLI mutation commands are intentionally deferred until an authorized account preflight validates CLI schema, compartment policy and the approved bucket name. This prevents a copy/paste procedure from creating a bucket in an unverified tenancy.

## Minimum routine permissions

HashiCorp lists these Object Storage permissions for state and locking:

- `OBJECT_INSPECT`;
- `OBJECT_CREATE`;
- `OBJECT_READ`;
- `OBJECT_DELETE`.

Scope the policy to the dedicated bucket wherever OCI policy conditions permit. Bucket creation, versioning/lifecycle administration, policy administration and final deletion belong to the separate backend administrator/recovery role.

## Initialization after approval

```bash
cp infra/oci/backend.example.hcl infra/oci/backend.hcl
chmod 600 infra/oci/backend.hcl

terraform -chdir=infra/oci init \
  -reconfigure \
  -backend-config=backend.hcl

terraform -chdir=infra/oci state pull > /tmp/coreview-oci-state-review.json
chmod 600 /tmp/coreview-oci-state-review.json
```

Do not use `-migrate-state` until an explicit review confirms which local state, if any, is authoritative. Do not commit the pulled state.

## Recovery

1. stop Terraform writers and verify no live `.lock` operation;
2. list state object versions without printing their content;
3. copy/download the selected prior version into a root-only temporary file;
4. inspect it for the expected lineage and serial;
5. back up the current version;
6. restore using the documented Object Storage version operation or `terraform state push` only after two-person review;
7. run a refresh-only plan and inspect every difference;
8. delete temporary state copies securely.

Never delete a lock object merely because it exists; first establish that its Terraform process is gone and retain incident evidence.

## Independent destruction

`terraform destroy` for `infra/oci` does not remove the backend bucket. Final backend cleanup is a distinct retention decision:

1. archive approved final state/evidence;
2. confirm no future recovery/audit requirement;
3. remove state and lock object versions deliberately;
4. remove lifecycle/versioning configuration if required by OCI deletion rules;
5. delete the bucket;
6. remove its IAM policy;
7. verify Object Storage and cost inventory.

Reference: <https://developer.hashicorp.com/terraform/language/backend/oci>
