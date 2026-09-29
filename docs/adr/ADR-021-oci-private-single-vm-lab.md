# ADR-021: OCI private single-VM synthetic lab

## Status

Accepted for offline implementation on 2026-09-29 and amended by the Phase 1.3B freeze review. No OCI plan or deployment has been executed.

## Context

CoreView already has one validated `linux/amd64` container image and a three-service Compose topology whose app and collector share the simulator network namespace. The OCI blueprint must preserve that contract, keep 5067 and 9090 on guest loopback, and provide private administration without a public VM address. It must not claim that Always Free capacity or an ARM image exists.

The principal options were:

1. one x86_64 `VM.Standard.E4.Flex` VM in a private subnet, reached through OCI Bastion and using OCIR;
2. an Ampere A1 ARM VM, which can be economical but requires an independently built and tested `linux/arm64` supply chain;
3. a public VM with narrow SSH ingress, which is cheaper but creates a public management endpoint;
4. OKE or a public load balancer, both unnecessary for this single-host synthetic lab.

OCI Bastion port-forwarding sessions terminate at a target private IP and port. They cannot by themselves reach a service that listens only on `127.0.0.1` inside the guest. Therefore the operator first forwards a local port to guest SSH through Bastion, then starts a second SSH connection that forwards 5067 and 9090 to guest loopback.

OCIR does not automatically translate an Instance Principal into Docker authentication. Oracle documents a credential-helper pattern. The host therefore builds a pinned `docker-credential-ocir`, verifies both source and binary checksums, places it behind a registry allow-list wrapper, and configures Docker to request ephemeral Instance Principal credentials. Oracle documents this pattern, while the pinned helper implementation itself is third-party demonstration code and remains subject to an independent preflight review. Static auth tokens are not placed in Terraform, metadata or the image.

## Decision

- Use the existing compartment and tenancy as mandatory, explicitly allow-listed inputs.
- Implement in `us-ashburn-1`, with `iad.ocir.io`, while requiring a future preflight to resolve the availability domain and exact Oracle Linux 9 x86_64 image OCID.
- Use `VM.Standard.E4.Flex`, one OCPU and 8 GiB memory. This matches the CI architecture; A1 remains a documented alternative only.
- Create a VCN with distinct private workload and Bastion endpoint subnets. Use a NAT Gateway for controlled Internet egress and a Service Gateway for regional OCI services. Create no Internet Gateway and assign no public VM IP.
- Permit workload ingress only on TCP 22 from the computed Bastion private endpoint IP (/32). Compose continues to bind 5067 and 9090 to loopback.
- Use a 50 GiB balanced boot volume and a protected 50 GiB balanced runtime volume mounted at `/var/lib/docker`. Docker refuses to start without that mount.
- Create a private immutable OCIR repository. An exact-instance Dynamic Group receives repository-scoped read access only.
- Keep the approved image digest outside Terraform and custom metadata. Initialize secrets and the first digest interactively after provisioning. Rotate a digest inside the guest with rollback rather than replacing the VM.
- Use an external, versioned Object Storage bucket and Terraform's native `oci` backend locking. The bucket is not owned by this stack.
- Protect the VM, runtime volume and OCIR repository with `prevent_destroy`; complete teardown requires a separate reviewed stage.

## Alternatives considered

### Ampere A1

Rejected as the primary path. The current CI evidence is only for `linux/amd64`; selecting A1 would make the artifact unusable until multi-architecture build, SBOM, scanner, smoke and performance evidence exists. Always Free eligibility, quota and host capacity are not guaranteed.

### Public VM with source-restricted SSH

Rejected as the primary path because it exposes a management address and requires ongoing source-CIDR maintenance. It remains a cost-reduction option for a separately approved short-lived experiment.

### Static OCIR auth token in cloud-init

Rejected. It would put a long-lived secret in metadata, Terraform state or logs. A publisher may use a separately managed temporary auth token, but the VM uses Instance Principal and a Docker credential helper.

### OKE, Load Balancer or public dashboard

Rejected as disproportionate. The workload is a single synthetic lab and has no public availability requirement.

## Consequences

- The application topology remains portable and unchanged.
- Administration requires two local SSH processes because CoreView remains loopback-only.
- NAT, volumes, OCIR content, Object Storage state and optional logs can retain cost after Compute stops.
- IAM, Bastion connectivity, image availability, quota, package versions, OCIR pull and effective routing remain unverified until an authorized OCI preflight/plan and deployment.
- The bootstrap intentionally cannot complete until exact package versions and credential-helper checksums are resolved against the pinned image.
- A future ARM option must be treated as a separate release target, not a Terraform-only shape substitution.

## Authoritative references

- OCI Bastion port forwarding: <https://docs.oracle.com/en-us/iaas/Content/Bastion/Tasks/connect-port-forwarding.htm>
- OCI Bastion session TTL: <https://docs.oracle.com/en-us/iaas/Content/Bastion/Tasks/create-session-port-forwarding.htm>
- OCI credential helper with Instance Principals: <https://docs.oracle.com/en/learn/cred-helper/index.html>
- Repository-level OCIR policies: <https://docs.oracle.com/en-us/iaas/Content/Registry/Concepts/registrypolicyrepoaccess.htm>
- Compute shapes: <https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm>
- Boot volume limits: <https://docs.oracle.com/en-us/iaas/Content/Block/Concepts/bootvolumes.htm>
- Block volume creation and limits: <https://docs.oracle.com/en-us/iaas/Content/Block/Tasks/creatingavolume.htm>
- Terraform native OCI backend: <https://developer.hashicorp.com/terraform/language/backend/oci>
