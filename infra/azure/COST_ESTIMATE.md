# Azure cost estimate

Estimate date: **2026-09-28**. Currency: USD, before taxes. Region: `eastus2`. This is arithmetic from public list prices and documented assumptions, not a quote, invoice or real plan.

## Selected private architecture

Assumptions: one on-demand Linux `Standard_B2s`, two 32 GiB Standard SSD LRS managed disks (E4), one Standard NAT Gateway processing 10 GiB/month, two Standard static IPv4 addresses (NAT and Bastion), Bastion Standard at its two-instance base, one Basic ACR, tiny Azure Blob state, no Log Analytics ingestion, and low outbound transfer.

| Item | Public price used | 24 hours | 168 hours | 730 hours/month |
|---|---:|---:|---:|---:|
| Linux VM `Standard_B2s` | USD 0.0416/hour | 1.00 | 6.99 | 30.37 |
| Bastion Standard base | USD 0.29/hour | 6.96 | 48.72 | 211.70 |
| NAT Gateway | USD 0.045/hour | 1.08 | 7.56 | 32.85 |
| Two Standard static IPv4 addresses | USD 0.005/IP-hour | 0.24 | 1.68 | 7.30 |
| Two E4 Standard SSD disks | USD 2.40/disk-month, prorated | 0.16 | 1.10 | 4.80 |
| ACR Basic | USD 0.1666/day | 0.17 | 1.17 | 5.00 |
| NAT processed data | USD 0.045/GiB; scaled assumption | 0.02 | 0.10 | 0.45 |
| Blob state | tiny state/operations assumption | 0.01 | 0.01 | 0.01 |
| **Approximate total** | | **USD 9.64** | **USD 67.33** | **USD 292.48** |

The fixed Bastion charge dominates the selected design. Charges begin when Bastion and NAT exist, not when a session or workload is active.
Deallocating only the VM removes approximately USD 30.37/month of compute from this model, but leaves roughly USD 262.11/month of modeled fixed/persistent services and storage until Bastion, NAT, public IPs, disks, ACR and backend are separately removed. This is an estimate, not a billing guarantee.

## Economical short-lab alternative

The rejected economical option gives the VM an explicit Standard public IP, removes NAT and Bastion, and restricts TCP 22 to a reviewed operator source prefix for a short approved window. Using the same VM, disks, ACR and state assumptions, it is roughly USD 1.4 for 24 hours and USD 47-48 for 730 hours before transfer. It exposes a public management endpoint and requires operational source-prefix/JIT discipline, so it is not the primary Terraform implementation.

Bastion Developer is free in supported regions but is browser-only, shared and single-VM; it does not support the required Azure CLI/native-client tunnel. Basic is paid and dedicated but also lacks native-client support. Premium supplies private-only Bastion and recording at approximately USD 0.45/hour base, but those capabilities are outside this synthetic lab's requirements.

## Sources and reproducibility

Prices were checked against the official Azure Retail Prices API on 2026-09-28 for East US 2 where available:

- `Standard_B2s` Linux: USD 0.0416/hour;
- Bastion Standard Gateway: USD 0.29/hour;
- Standard static IPv4: USD 0.005/hour;
- E4 LRS Standard SSD: USD 2.40/month;
- ACR Basic unit: USD 0.1666/day.

Official references:

- Retail Prices API: <https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices>
- Azure Bastion pricing: <https://azure.microsoft.com/pricing/details/azure-bastion/>
- NAT Gateway pricing: <https://azure.microsoft.com/pricing/details/azure-nat-gateway/>
- ACR pricing: <https://azure.microsoft.com/pricing/details/container-registry/>
- Managed Disks pricing: <https://azure.microsoft.com/pricing/details/managed-disks/>
- Virtual Machines pricing: <https://azure.microsoft.com/pricing/details/virtual-machines/linux/>

## Excluded or variable

- Internet data transfer and NAT processing beyond the 10 GiB example;
- Bastion outbound above its included allowance;
- ACR storage above included capacity, build tasks and geo-replication;
- retained disks, snapshots, ACR images and Blob state versions after teardown;
- Azure Monitor/Log Analytics ingestion, which this stack does not create;
- support, taxes, exchange rates, reservations, savings plans and organization-specific agreements;
- Defender for Cloud/JIT charges if the economical alternative were adopted.

Recalculate with Azure Pricing Calculator and the Retail Prices API immediately before a real apply. Azure Cost Management budgets are alerts, not hard spending caps.
