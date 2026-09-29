# Future Azure destroy runbook

**Not executed in Phase 1.2A or 1.2B.** Destruction requires separate approval. `prevent_destroy` intentionally blocks accidental removal of the managed Resource Group, VM, runtime disk and ACR.

## 1. Inventory and backup

```bash
terraform -chdir=infra/azure state list
terraform -chdir=infra/azure state pull > /tmp/coreview-azure-final-state.json
az resource list --resource-group APPROVED_RESOURCE_GROUP --output table
az disk list --resource-group APPROVED_RESOURCE_GROUP --output table
az acr repository list --name APPROVED_ACR_NAME --output table
```

Store the state export only in an approved encrypted recovery location, then securely remove the temporary copy. Determine whether the synthetic SQLite/runtime disk, ACR image and logs need retention. They contain synthetic lab data but can still contain application credentials at runtime.

## 2. Stage A — protected decommission

Through Bastion, stop CoreView and remove active sessions. Revoke image-publisher and administrative grants no longer needed. Do not delete the state backend yet.
Deallocate the VM if immediate compute savings are required, but record that Bastion, NAT Gateway, both public IPs, disks, ACR and Blob state continue billing. With the guards still present, `terraform plan -destroy` is expected to stop with a `prevent_destroy` error before any apply; it is a safety verification, not a partial cleanup mechanism. Do not use `-target` or state removal to work around it.

## 3. Stage B — explicitly authorize guard removal

To perform an approved full destroy, a reviewed change must temporarily remove `prevent_destroy` from:

- `azurerm_resource_group.lab` when this stack owns it;
- `azurerm_linux_virtual_machine.coreview`;
- `azurerm_managed_disk.runtime`;
- `azurerm_container_registry.coreview`.

If using a pre-existing Resource Group, it is a data source and must never be deleted by this stack. Re-run and review a normal plan after changing protection; never use `-target` or `-auto-approve` to bypass the inventory.

## 4. Full destruction approval

```bash
terraform -chdir=infra/azure plan -destroy -input=false -out=/tmp/coreview-azure-destroy.tfplan
terraform -chdir=infra/azure show -no-color /tmp/coreview-azure-destroy.tfplan
terraform -chdir=infra/azure apply /tmp/coreview-azure-destroy.tfplan
```

Confirm the plan includes the VM, NIC, managed disks, role assignment, ACR, Bastion, both public IPs, NAT, NSG, subnets and VNet. A managed RG is removed only after its children. An adopted RG remains.

## 5. Residual-cost audit

After destroy, explicitly query:

- managed disks and snapshots;
- public IP addresses and NAT Gateway;
- Bastion hosts;
- ACR registries and retained manifests;
- NICs, NSGs and VNets;
- boot diagnostics artifacts;
- role assignments;
- locks and diagnostic settings;
- the separately managed state Storage Account, versions and soft-deleted containers.

The backend survives workload destroy by design. Follow [STATE_BACKEND.md](STATE_BACKEND.md) only after the final state and recovery window are approved. Azure budgets are not deletion controls and do not cap residual spend.
