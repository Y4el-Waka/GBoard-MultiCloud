# AWS laboratory destruction runbook

Destruction is destructive and must be explicitly authorized in Phase 1.0B or
later. Normal Terraform operations use the remote state configured in ignored
`backend.hcl`; never reconstruct targets from memory.

## Before destroy

1. Stop new Session Manager access and record the exact Terraform workspace,
   caller identity, Region and state key.
2. Decide whether synthetic SQLite/snapshot/uploads or runtime logs require an
   approved backup. The root volume has `delete_on_termination = true` and no
   Terraform snapshot resource.
3. Inspect the private ECR repository and record image digests/SBOM references.
   `force_delete = false` intentionally blocks repository removal while images
   remain.
4. Run and review `terraform plan -destroy`; verify it targets only the lab
   instance, root EBS, ephemeral public IPv4 association, ECR, instance profile,
   role/policies, SG, subnet, route/route table, IGW and VPC.

If deletion of reviewed ECR images is authorized, enumerate before deleting:

```bash
repository_url="$(terraform output -raw ecr_repository_url)"
repository_name="${repository_url#*/}"
aws ecr describe-images --region us-east-1 --repository-name "$repository_name"
```

Delete only explicitly reviewed image digests/tags. Do not set `force_delete`
merely to make destroy pass.

## Execute after approval

```bash
aws sts get-caller-identity
terraform plan -destroy -out=coreview-lab-destroy.tfplan
terraform show coreview-lab-destroy.tfplan
terraform apply coreview-lab-destroy.tfplan
```

The plan file is ignored but sensitive; delete it through the operator's normal
secure artifact process after the evidence retention period.

## Residual-cost audit

After completion, verify independently that no tagged EC2 instance, unattached
EBS volume, public IPv4, ENI, ECR image/repository, Internet Gateway, NAT
Gateway, load balancer or snapshot remains. Check Cost Explorer after billing
data catches up.

The separately bootstrapped S3 state bucket, object versions/lock objects,
account-level CloudTrail/Session Manager logs, AWS Budget and manually created
snapshots are not owned by this stack. Review their retention and cost
separately. Preserve the final state according to policy before removing the
backend; do not casually delete audit evidence.
