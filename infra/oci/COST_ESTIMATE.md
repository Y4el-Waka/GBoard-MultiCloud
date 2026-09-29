# OCI cost estimate — CoreView synthetic lab

Estimate date: **2026-09-29**. Currency: USD, before tax/support. Region modeled: `us-ashburn-1`. This is a planning calculation, not an OCI invoice or Pricing Calculator quote. Prices and eligibility must be refreshed immediately before an authorized plan.

## Assumptions

- `VM.Standard.E4.Flex`: 1 OCPU and 8 GiB memory.
- Published list prices used: AMD E4 OCPU USD 0.032765/OCPU-hour and memory USD 0.0019659/GB-hour.
- Two 50 GiB balanced volumes (boot plus runtime). Planning rate: USD 0.0255/GB-month capacity plus USD 0.0017/VPU/GB-month at 10 VPUs/GB, or about USD 0.0425/GB-month.
- OCI Bastion: no service charge; normal networking/data-transfer behavior still applies.
- Service Gateway: no service charge.
- Approximately 2 GiB combined OCIR image and Object Storage state/version footprint at USD 0.0255/GB-month, without assuming the free allowance.
- NAT Gateway has no separate hourly line item identified in the cited OCI public price list; outbound transfer and any changed service pricing remain variable and must be checked in the OCI estimator.
- No public VM IP, Load Balancer, database, Kubernetes, monitoring ingestion or paid support.
- Data egress, taxes, logs, API requests and retained versions are excluded because usage is unknown.

## Calculation

Compute hourly:

```text
1 OCPU × 0.032765 + 8 GiB × 0.0019659 = USD 0.0484922/hour
```

Persistent storage:

```text
100 GiB balanced block storage × 0.0425 = USD 4.25/month
2 GiB OCIR/Object Storage × 0.0255 = USD 0.051/month
```

| Scenario | Compute | Prorated block storage | Prorated registry/state | Modeled total |
|---|---:|---:|---:|---:|
| 24 hours | 1.16 | 0.14 | <0.01 | **~1.31** |
| 168 hours | 8.15 | 0.98 | 0.01 | **~9.14** |
| 730 hours | 35.40 | 4.25 | 0.05 | **~39.70** |

The short scenarios prorate monthly storage for comparison, but OCI billing granularity and minimums must be confirmed. Network egress can increase every scenario.

## Always Free and A1

OCI documents Always Free A1 and storage allowances, but eligibility, home-region constraints, service limits and capacity are not guaranteed. A1 is ARM; CoreView's current CI artifact is only `linux/amd64`. No Always Free discount is included in the totals and A1 is not a deployable substitution until an arm64 build, tests, SBOM, vulnerability scan and smoke evidence exist.

## Residual costs

Stopping Compute removes compute-hour consumption but can leave approximately **USD 4.30/month** in modeled Block Volume plus OCIR/Object Storage storage, before retained versions, logs and transfer. NAT/Service Gateways, Bastion, network objects and IAM generally do not justify assuming all residual cost is zero. After teardown, inventory:

- boot and block volumes/backups;
- OCIR images/layers;
- Object Storage state versions and lock objects;
- log groups/streams;
- NAT/Service Gateways, reserved/public addresses and Bastion sessions;
- detached VNICs and other compartment resources.

## Official sources

- OCI public price list: <https://www.oracle.com/cloud/price-list/>
- OCI Compute pricing: <https://www.oracle.com/cloud/compute/pricing/>
- OCI Block Volume pricing: <https://www.oracle.com/cloud/storage/block-volumes/pricing/>
- VCN and Service Gateway FAQ: <https://www.oracle.com/cloud/networking/virtual-cloud-network/faq/>
- OCI Bastion overview: <https://docs.oracle.com/en-us/iaas/Content/Bastion/Concepts/bastionoverview.htm>
- Always Free limitations: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>

Budgets and alerts are recommended before deployment, but they are notifications rather than hard spending limits.
