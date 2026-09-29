# Phase 1.3 — Oracle Cloud Infrastructure blueprint

## Status

- Architecture: **Implemented**
- Terraform: **Static validation passed**
- Real OCI plan: **Not executed**
- Workload deployment: **Not executed**
- OCI credentials used: **No**
- OCI resources created: **0**

Phase 1.3A adds an independent OCI implementation under `infra/oci/`. It does not alter AWS, GCP, Azure or the CoreView application/runtime. The result is an offline-validated blueprint, not evidence of OCI compatibility or deployment.

## Architecture

CoreView runs as its existing three-container synthetic stack on one private Oracle Linux 9 x86_64 Compute instance. `simulator`, `collector` and `app` preserve the shared simulator network namespace, measured resource limits and loopback-only publication.

```text
Zorin operator
  |
  | OCI Bastion short-lived port-forward session
  | localhost:22022 -> private Compute:22
  |
  | nested SSH local forwards
  | localhost:5067 -> guest loopback:5067
  | localhost:9090 -> guest loopback:9090
  v
OCI VCN (us-ashburn-1)
  +-- private Bastion endpoint subnet
  |     +-- OCI Bastion Standard
  |
  +-- private workload subnet
        +-- NSG: inbound TCP 22 from the exact Bastion endpoint IP only
        +-- Service Gateway -> OCI regional services
        +-- NAT Gateway -> outbound HTTPS/NTP
        +-- VM.Standard.E4.Flex (1 OCPU, 8 GiB; no public IP)
              +-- Oracle Linux 9 exact image OCID (future preflight)
              +-- IMDSv1 disabled; in-transit PV encryption
              +-- exact-instance Dynamic Group
              +-- credential helper -> ephemeral Instance Principal OCIR token
              +-- 50 GiB balanced encrypted boot volume
              +-- 50 GiB protected balanced runtime Block Volume
              +-- Docker Compose synthetic lab selected by manifest digest

External and independently managed:
  private versioned Object Storage backend + native lock object
```

There is no Internet Gateway, public VM address, public dashboard, Load Balancer, Kubernetes, database service, Redis or publicly reachable SSH rule.

## Static resource inventory

The root module contains these resource families:

- VCN, two private subnets, two route tables and explicit non-default security lists;
- NAT Gateway and Service Gateway;
- workload NSG with stateful SSH, HTTPS, DNS, NTP and IMDS rules;
- OCI Bastion Standard;
- one `VM.Standard.E4.Flex` Compute instance;
- one separate Block Volume and attachment;
- one private immutable OCIR repository;
- one exact-instance Dynamic Group;
- one compartment policy granting read on only the `coreview` repository.

The compartment, tenancy, availability domain, Object Storage bucket and ephemeral Bastion sessions are not Terraform-owned by this stack.

## Security matrix

| Area | Implemented decision | Not yet proven |
|---|---|---|
| Account scope | mandatory tenancy and compartment OCIDs plus hard allow-list validations | effective tenancy/compartment permissions |
| Provider auth | short-lived SecurityToken default; no API key variables | real operator login and policy evaluation |
| Inbound | private VM; TCP 22 only from the computed Bastion endpoint IP/32 | effective VCN/Bastion data path |
| Dashboard/simulator | guest-loopback bindings for 5067/9090 | browser smoke in OCI |
| Admin | short-lived Bastion session then nested SSH forwards | session creation, TTL and operator IAM |
| Egress | explicit Service Gateway plus NAT; NSG permits HTTPS, DNS, NTP and IMDS | regional service routing and DNS/NTP behavior |
| Compute | x86_64 E4 Flex; exact image OCID input; IMDSv1 disabled | image/shape availability and quota |
| Storage | balanced encrypted boot/runtime volumes; runtime protected and mandatory for Docker | real device path/attachment timing and backup policy |
| Runtime identity | Dynamic Group matches exact instance OCID | token exchange/propagation latency |
| Registry | private immutable repository; read-only repository policy | real OCIR namespace, push and digest pull |
| Docker auth | pinned/checksummed helper uses ephemeral Instance Principal | helper revision/build checksum and real OCIR behavior |
| Secrets | interactive guest-only initialization; root mode 0600 | operational recovery/rotation in OCI |
| State | external native OCI backend with locking/versioning design | bucket bootstrap, policy and recovery rehearsal |
| Destruction | guards on VM, runtime volume and repository; staged teardown | real destroy dependency behavior |

## Runtime and bootstrap

The secretless bootstrap:

1. masks Docker before package installation;
2. verifies Docker repository metadata and installs exact RPM versions;
3. waits for `/dev/oracleoci/oraclevdb`;
4. formats only a blank device, accepts only ext4, and mounts by UUID at `/var/lib/docker` without `nofail`;
5. makes the mount a systemd prerequisite for Docker;
6. verifies the pinned Compose binary;
7. downloads a full credential-helper source commit, verifies its archive, builds `linux/amd64`, verifies the binary, removes Go and exposes it only through an OCIR-host allow-list wrapper;
8. configures Docker `credHelpers` for only `iad.ocir.io`; static credential storage is rejected;
9. installs the Compose/runtime files and digest update/rollback helpers;
10. enables a service that cannot start until `/opt/coreview/.env` exists.

The image digest is not a Terraform variable and does not enter custom metadata. An empty OCIR repository is safe: no image pull happens during bootstrap. After publication, an operator initializes application secrets interactively and supplies an approved manifest digest. Later digest changes happen inside the guest without changing `user_data` or replacing the instance.

## Identity separation

- Terraform operator: reviewed infrastructure/IAM mutation and backend access.
- Backend administrator/recovery owner: bucket lifecycle, versions and final cleanup.
- Image publisher: push only to the CoreView OCIR repository using separately managed temporary credentials.
- Compute identity: exact-instance Dynamic Group and read only on one repository.
- Bastion operator: create/terminate short-lived sessions and read target details.
- Guest administrator: SSH public-key access through Bastion; initializes synthetic runtime secrets.
- Break-glass: separately governed; no credential is encoded in Terraform.

OCI Instance Principal is not treated as implicit Docker authentication. The credential helper is an explicit, audited exchange layer.

## x86_64 versus Ampere A1

The primary architecture uses AMD E4 because CI validated only a `linux/amd64` image. Ampere A1 can reduce theoretical cost and may be Always Free eligible, but no `linux/arm64` image, SBOM, scanner evidence, smoke, performance test or Compose release artifact exists. Always Free eligibility, quotas, home-region restrictions and capacity are not guaranteed. A1 remains `Not Tested`.

## Comparison with other CoreView blueprints

| Capability | AWS | GCP | Azure | OCI |
|---|---|---|---|---|
| Private administration | SSM Session Manager | IAP TCP + OS Login | Bastion Standard + SSH | Bastion port forwarding + nested SSH |
| VM ingress | none in SG | SSH from IAP proxy range | SSH from Bastion subnet | SSH from exact Bastion endpoint IP |
| Egress | public IPv4 route in initial lab | Cloud NAT | NAT Gateway | NAT + Service Gateway |
| Runtime identity | EC2 role | service account | managed identity | Instance Principal/Dynamic Group |
| Registry | ECR | Artifact Registry | ACR | OCIR |
| State | S3 | proposed GCS | proposed Blob | proposed Object Storage native backend |

These are capability comparisons, not claims of equivalent data paths or authorization semantics.

## Cost summary

Using public prices reviewed on 2026-09-29 and excluding tax/support/unknown transfer/logging:

- 24 hours: approximately **USD 1.31**;
- seven continuous days: approximately **USD 9.14**;
- 730-hour month: approximately **USD 39.70**.

The model includes 1 OCPU/8 GiB E4 compute, 100 GiB total balanced Block Volume and about 2 GiB registry/state storage. It does not assume Always Free. See `infra/oci/COST_ESTIMATE.md` for formulas, sources and residual costs.

## Validation evidence

Completed without OCI credentials:

- Terraform format check;
- `terraform init -backend=false` with OCI provider 8.29.0 and versioned lockfile;
- `terraform validate` against the installed provider schema;
- Bash syntax check of the bootstrap template;
- OCI structural/security unit tests;
- existing Python and JavaScript regression suites;
- Docker Compose contract/config validation;
- repository secret/IP checks and `git diff --check`;
- offline IaC scan when the pinned local scanner was available.

Observed Phase 1.3A baseline results (superseded by the Phase 1.3B final report):

- 326 Python tests passed, including 11 OCI-specific tests;
- 18 JavaScript tests passed;
- Terraform format and validate passed;
- rendered Docker Compose configuration passed;
- Trivy 0.74.0 reported zero findings using its embedded offline rules. Network was deliberately disabled, so its checks bundle could not be refreshed; this is not evidence against the latest policy set.

Fixture/static checks are not evidence from OCI.

## Known limitations and future requirements

Before a real plan or deployment:

1. authenticate to an explicitly authorized tenancy;
2. verify compartment/tenancy allow-lists and effective IAM;
3. resolve one availability domain and exact Oracle Linux 9 x86_64 image OCID;
4. verify E4, Block Volume, Bastion and OCIR limits/capacity;
5. approve non-overlapping CIDRs and current operator public CIDR;
6. pin/test exact Oracle Linux Docker packages and credential-helper source/binary checksums;
7. bootstrap and rehearse the external Object Storage backend;
8. refresh prices and configure budget alerts;
9. execute and review a real Terraform plan;
10. publish only the exact CI-validated private image by digest;
11. validate Bastion, Instance Principal, OCIR, reboot/persistence, smoke and teardown.

Current final truth:

```text
Architecture: Implemented.
Terraform: Static validation passed.
Real OCI plan: Not executed.
Workload deployment: Not executed.
OCI resources created: 0.
```

## References

- [ADR-021](adr/ADR-021-oci-private-single-vm-lab.md)
- [OCI blueprint README](../infra/oci/README.md)
- [Deployment runbook](../infra/oci/DEPLOYMENT_RUNBOOK.md)
- [Destroy runbook](../infra/oci/DESTROY_RUNBOOK.md)
- [State backend](../infra/oci/STATE_BACKEND.md)
- [Cost estimate](../infra/oci/COST_ESTIMATE.md)
