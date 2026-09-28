# GCP cost estimate

Estimate date: **2026-09-28**. Currency: USD, before taxes. Region: `us-east1` (South Carolina). This is arithmetic from public list prices, not a quote, invoice or real plan.

## Monthly continuous-lab scenario

Assumptions: 730 hours, one on-demand `e2-small`, two 20 GiB zonal `pd-balanced` disks, one Public NAT assignment and ephemeral NAT address, 10 GiB processed through NAT, one small regional Artifact Registry image, and low state/log activity.

| Item | Public price used | Assumption | Estimate |
|---|---:|---:|---:|
| Compute Engine `e2-small` | USD 0.016752855/hour | 730 hours | USD 12.23 |
| Balanced Persistent Disk | USD 0.000136986/GiB-hour | 40 GiB × 730 hours | USD 4.00 |
| Public NAT gateway | USD 0.0014/assigned-VM-hour | 1 VM × 730 hours | USD 1.02 |
| Public NAT external address | USD 0.005/hour | 1 × 730 hours | USD 3.65 |
| Public NAT processing | USD 0.045/GiB | 10 GiB | USD 0.45 |
| Artifact Registry | First 0.5 GiB-month free per billing account; then USD 0.000136986/GiB-hour | 0.1 GiB and free tier still available | USD 0.00 |
| **Subtotal** | | | **USD 21.35/month** |

Official sources:

- Compute Engine E2 on-demand pricing: <https://cloud.google.com/products/compute/pricing/general-purpose>
- Persistent Disk pricing: <https://cloud.google.com/compute/disks-image-pricing>
- Cloud NAT pricing: <https://cloud.google.com/nat/pricing>
- Artifact Registry pricing: <https://cloud.google.com/artifact-registry/pricing>
- IAP pricing: <https://cloud.google.com/iap/pricing>

## Short-lived 24-hour scenario

The compute, disk, NAT-assignment and NAT-address subtotal is approximately **USD 0.69** for 24 hours, before NAT processing, external network egress, logging, state storage, taxes and any minimums. Disks and the registry can continue billing while the VM is stopped.

## Excluded or variable costs

- Internet data transfer and Cloud NAT processed GiB beyond the example;
- Cloud Logging charges from NAT error logs or other enabled logs;
- Artifact vulnerability scanning, which this stack does not enable;
- Artifact storage after the billing-account free tier;
- GCS backend storage, object versions and operations;
- snapshots or retained runtime disks;
- support, taxes, exchange rates and organization-specific pricing;
- committed-use discounts or promotional/free-tier credits.

VPC networks and Cloud Router do not add a line item in this estimate, but associated network services do. Basic IAP protection has no separate charge under the published IAP pricing model; Compute and network usage still cost money.

## Budget procedure

Before a real deployment, create a Cloud Billing budget outside this stack with notifications at several forecasted and actual thresholds, and route alerts to a monitored channel. A budget is an alerting mechanism, **not** a hard spending cap. Confirm that the budget covers the selected billing account/project and retain a separate teardown reminder.

Recalculate with the Google Cloud Pricing Calculator and current Cloud Billing SKUs immediately before any apply. Do not treat this dated estimate as authorization to spend.
