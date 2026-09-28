# AWS laboratory cost estimate

Estimate date: 2026-09-28. Region: US East (N. Virginia). Currency: USD.
This is a planning estimate, not a quote or a hard spending limit. Taxes,
support, unexpected transfer, retained snapshots, state history and account
pricing agreements are excluded. Recalculate with the
[AWS Pricing Calculator](https://aws.amazon.com/aws-cost-management/aws-pricing-calculator/pricing/)
immediately before a deployment.

## Monthly baseline

The calculation uses 730 hours/month:

| Component | Assumption | Estimated monthly cost |
| --- | --- | ---: |
| EC2 `t3.small` Linux On-Demand | $0.0209/hour x 730 | $15.26 |
| EBS gp3 | 20 GiB x $0.08/GiB-month; baseline performance only | $1.60 |
| In-use public IPv4 | $0.005/hour x 730 | $3.65 |
| Private ECR | Conservative 1 GiB retained x $0.10/GiB-month | $0.10 |
| Same-Region ECR pull | ECR to EC2 in same Region | $0.00 |
| VPC, subnet, route table, IGW, SG | No hourly charge; transfer still applies | $0.00 |
| **Estimated fixed baseline** | Before tax/transfer/state/log retention | **$20.61/month** |

Official references:

- The [EC2 T3 page](https://aws.amazon.com/ec2/instance-types/t3/) lists the
  `t3.small` 2-vCPU/2-GiB shape and a displayed Linux On-Demand price of
  $0.0209/hour. Confirm the Region-specific figure in Pricing Calculator.
- [EBS pricing](https://aws.amazon.com/ebs/pricing/) prices gp3 by provisioned
  storage and includes 3,000 IOPS/125 MB/s baseline; the AWS gp3 example uses
  $0.08/GB-month.
- [VPC pricing](https://aws.amazon.com/vpc/pricing/) charges $0.005/hour for an
  in-use public IPv4 address.
- [ECR pricing](https://aws.amazon.com/ecr/pricing/) lists private repository
  storage at $0.10/GB-month and no same-Region ECR-to-EC2 transfer charge.

No NAT Gateway, Elastic IP, ALB, RDS, ECS, EKS, Redis or paid detailed EC2
monitoring is configured.

## Variable and residual charges

- Internet data transfer and SSM traffic depend on use; the table does not claim
  a free allowance or estimate unmeasured traffic.
- The external S3 state bucket incurs small storage/request/version-retention
  charges and survives workload destroy.
- Account-level Session Manager logging, CloudTrail or CloudWatch retention may
  add charges if enabled outside this stack.
- ECR remains after a failed destroy while it contains images. Images and old
  layers continue accruing storage.
- No snapshots are created by Terraform. Manually created EBS snapshots survive
  instance deletion and continue billing.
- T3 is configured in `standard` credit mode, so surplus credits are not billed;
  sustained CPU can instead throttle. If deliberately changed to Unlimited,
  [EC2 On-Demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/) lists
  Linux T3 surplus credits at $0.05 per vCPU-hour.

## Budget procedure

Create an AWS Cost Budget manually in the authorized laboratory account after
reviewing account-wide allocation. A useful starting point for this isolated
stack is a monthly cost budget above the $20.61 baseline, with actual-cost
notifications at 50% and 80% and a forecast notification at 100%. Recipients
must be maintained outside this repository. Tag-based filters may lag until
cost allocation tags are activated.

[AWS Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)
provides monitoring and notifications, and AWS documents the creation flow in
[Creating a cost budget](https://docs.aws.amazon.com/cost-management/latest/userguide/create-cost-budget.html).
Budget data updates periodically; an alert is not a transaction-level control
and does not stop or cap spending.
