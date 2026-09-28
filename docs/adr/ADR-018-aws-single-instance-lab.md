# ADR-018: AWS single-instance synthetic laboratory

## Context

Phase 0.5 produced a reproducible, constrained Docker Compose laboratory. The
first AWS deployment must preserve that behavior without introducing a load
balancer, container orchestrator, managed database, public application port or
production PRTG connectivity. The synthetic metrics provider requires app and
collector to share the simulator network namespace so that its loopback-only
destination policy remains effective.

## Decision

Phase 1.0 uses one x86_64 Amazon Linux 2023 EC2 instance in a dedicated VPC and
single public subnet in `us-east-1`. The instance receives an ephemeral public
IPv4 address only for outbound Internet Gateway access. Its security group has
no ingress rules. Dashboard access uses Session Manager port forwarding to the
host-loopback binding on port 5067; SSH, 5067 and 9090 are never opened.

The initial reviewed size is `t3.small` with a 20 GiB encrypted gp3 root
volume. T3 credit mode is `standard` to avoid unplanned surplus-credit charges.
The instance role contains `AmazonSSMManagedInstanceCore` plus repository-scoped
ECR pull permissions. ECR authorization-token retrieval is the only wildcard
permission because AWS does not support resource-level scoping for it.

One private, encrypted ECR repository uses immutable tags, scan-on-push and
`force_delete = false`. Image publication is a separate, explicit operator
step. The EC2 bootstrap installs Docker and a checksum-pinned Compose plugin,
writes only synthetic configuration and registers a systemd unit. It neither
contains nor creates a hard-coded password. Runtime secrets are collected or
generated interactively after an authorized SSM session and stored as a
root-only host file on the encrypted EBS volume.

The existing Compose security controls remain: non-root application user,
read-only container roots, all Linux capabilities dropped,
`no-new-privileges`, loopback-only published ports and shared simulator network
namespace. The real PRTG provider stays disabled.

Terraform is a single root configuration split by responsibility, not a module
hierarchy. Provider versions are exact. Account IDs, approved RFC1918 CIDRs,
the immutable image tag and optionally the reviewed AMI ID are supplied through
ignored operator files. Remote state uses a separately bootstrapped, versioned,
encrypted S3 bucket and native S3 lockfile; no access credentials are placed in
backend configuration.

## Alternatives considered

- ECS/Fargate would change the proven shared-network and persistence model and
  is outside the first-deployment scope.
- ALB or direct public ports would broaden attack surface without a laboratory
  requirement.
- A private subnet with NAT Gateway would add a significant fixed cost. VPC
  endpoints could remove public IPv4 later, but several endpoints plus package
  and image bootstrap routing would add cost and complexity in this phase.
- SSH through a bastion or public key pair was rejected; Session Manager gives
  auditable, IAM-controlled access without inbound rules.
- Secrets Manager or Parameter Store SecureString is preferable for a future
  production design, but adds delivery policy and cost. A root-only runtime
  file on encrypted EBS is acceptable for this synthetic single-host lab and
  avoids placing secrets in Terraform state.
- Creating the state bucket in the same stack was rejected because the backend
  must exist before initialization and should survive workload destruction.
- ECR `force_delete = true` was rejected because destruction must not silently
  remove reviewed image evidence.

## Consequences

The architecture is inexpensive and closely matches the locally validated
runtime, but it is single-AZ and has no high availability. EC2 egress still
reaches arbitrary HTTPS destinations; the no-ingress security group is not an
egress firewall. The public IPv4 has an hourly charge. The SSM control plane,
ECR, GitHub Compose artifact and AL2023 package repositories must be reachable
during bootstrap.

The dynamic AL2023 public parameter supports current patch levels but is not a
permanent AMI pin. The reviewed plan exposes its resolved AMI; the operator must
copy that ID into ignored `ami_id_override` and review a second plan before a
future apply.

ECR containing images intentionally prevents a complete Terraform destroy
until the operator reviews and deletes those images. The external state bucket,
its versions and any account-level Session Manager logs are also outside this
stack and require separate retention review.
