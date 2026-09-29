# OCI teardown and residual-cost runbook

No destroy was executed in Phase 1.3B. Terraform lifecycle guards intentionally make an unreviewed full destroy fail.

## Stage A — ordinary stop and protected decommission

Use this stage when the laboratory should stop consuming Compute hours but protected data/release assets may be needed. It is an OCI mutation and requires approval.

1. stop CoreView through an authorized Bastion/SSH session;
2. record a sanitized health and resource inventory;
3. stop the Compute instance using the approved OCI operator flow;
4. terminate all Bastion sessions;
5. retain the VM definition, runtime volume and OCIR repository protections;
6. verify costs that continue while Compute is stopped.

Do not run `terraform destroy` expecting a partial success. `prevent_destroy` on the instance, runtime volume and OCIR repository causes Terraform to reject a destructive plan before it applies changes.

Stopped-state residuals include both Block Volumes, OCIR layers, Object Storage state/versions, and any logging. Network objects and Bastion should also be inventoried even when their modeled service charge is zero.

## Stage B — explicit full teardown

Full deletion requires a reviewed source change and new plan:

1. export any approved SQLite/snapshot evidence without secrets;
2. confirm retention obligations for runtime disk, boot disk and registry manifests;
3. separately preserve the final Terraform state version;
4. remove `prevent_destroy` only from the exact resources approved for deletion;
5. commit/review that change or keep a documented controlled patch, according to the release process;
6. regenerate a full destroy plan—never use `-target` to conceal dependencies;
7. review every object, including IAM, Bastion, Dynamic Group, policies, NSG, gateways, routes, subnets, VCN, instance, volumes and OCIR;
8. apply only the saved approved destroy plan;
9. perform the residual-cost audit below;
10. handle the external backend under `STATE_BACKEND.md`, not this stack.

Future command shape after the guard-removal review:

```bash
umask 077
terraform -chdir=infra/oci plan \
  -destroy \
  -input=false \
  -out=/tmp/coreview-oci-destroy.tfplan
terraform -chdir=infra/oci show -no-color /tmp/coreview-oci-destroy.tfplan \
  > /tmp/coreview-oci-destroy.txt
# Separate approval reviews the saved plan.
terraform -chdir=infra/oci apply -input=false /tmp/coreview-oci-destroy.tfplan
```

No automatic approval is permitted. Do not set OCIR deletion flags merely to make deletion convenient; explicitly remove images only after their retention decision.

## Residual-cost audit

After the authorized teardown, inspect the approved compartment for:

- running or stopped Compute instances;
- boot, block and backup volumes, including detached volumes;
- OCIR repositories, images and unreferenced layers;
- NAT/Service Gateways, VCNs, subnets, route tables, NSGs and VNICs;
- OCI Bastions and active sessions;
- public/reserved IPs and Load Balancers (none are expected);
- Object Storage buckets, state versions, lock objects and multipart uploads;
- log groups, streams, alarms and custom metrics;
- Dynamic Groups and policies;
- budgets/alerts and any cross-compartment artifacts.

Compare the inventory with the final Terraform state and OCI Cost Analysis after billing data settles. `terraform destroy` does not delete the independently managed backend bucket or its versions.

## Rollback of an accidental teardown request

If an unexpected delete or replacement appears, do not apply it. Preserve the plan, pull a protected copy of state, restore the reviewed code/inputs, reinitialize the same backend, and create a new normal plan. Never use state removal as a substitute for understanding an owned resource.
