# CoreView Cloud Lab on OCI

This directory is the independent Phase 1.3B Oracle Cloud Infrastructure blueprint. It targets an existing OCI tenancy and compartment; it creates neither. It has been validated only offline.

Status:

- architecture implemented;
- Terraform statically validated with Terraform 1.16.4 and OCI provider 8.29.0;
- no OCI credentials used;
- no real plan, resource creation or workload deployment performed.

## Architecture

```text
Operator on Zorin
  |
  | OCI Security Token + OCI Bastion port-forward session
  | local :22022 -> private VM :22
  v
OCI VCN, us-ashburn-1
  +-- private Bastion endpoint subnet
  |     +-- OCI Bastion Standard (short-lived sessions)
  |
  +-- private workload subnet
        +-- NSG: SSH only from the exact Bastion private endpoint
        +-- Service Gateway -> regional OCI services
        +-- NAT Gateway -> explicit outbound HTTPS
        +-- VM.Standard.E4.Flex, 1 OCPU / 8 GiB, no public IP
              +-- exact-instance Dynamic Group -> read one OCIR repo
              +-- 50 GiB encrypted balanced boot volume
              +-- 50 GiB protected encrypted balanced runtime volume
              +-- Docker Compose synthetic stack by approved digest

Second SSH hop on laptop:
  localhost:5067 -> guest 127.0.0.1:5067
  localhost:9090 -> guest 127.0.0.1:9090

External to the workload stack:
  versioned Object Storage state backend with native lock object
```

OCI Bastion reaches guest SSH, not the loopback applications directly. The nested SSH command in `DEPLOYMENT_RUNBOOK.md` preserves the loopback-only security boundary.

## Static resource inventory

The stack declares:

1. one VCN;
2. private workload and Bastion endpoint subnets;
3. workload and Bastion route tables;
4. NAT and Service Gateways;
5. explicit security lists plus one workload NSG and six NSG rules;
6. one OCI Bastion;
7. one private x86_64 Compute instance;
8. one separate runtime Block Volume and attachment;
9. one private immutable OCIR repository;
10. one exact-instance Dynamic Group and repository-scoped read policy.

The existing compartment, tenancy, availability domain and future Object Storage backend are not created by this stack. Bastion sessions are deliberately ephemeral CLI operations, not Terraform resources or state.

## Required future inputs

Copy `terraform.tfvars.example` to the ignored `terraform.tfvars` only during an authorized preflight. Resolve:

- tenancy and compartment OCIDs plus identical explicit allow-lists;
- one availability domain;
- non-overlapping VCN/workload/Bastion CIDRs;
- current operator public CIDR(s) for Bastion;
- exact Oracle Linux 9 x86_64 platform-image OCID in Ashburn;
- reviewed Ed25519 or ECDSA public key;
- exact Oracle Linux Docker and Go package versions;
- Docker repository file checksum;
- full credential-helper source commit, source archive checksum and reproducible `linux/amd64` binary checksum; the upstream tutorial helper remains gated by the CoreView registry allow-list wrapper.

No private key, fingerprint, API key, auth token, registry password or application secret belongs in tfvars.

## Offline validation

```bash
terraform -chdir=infra/oci fmt -check -recursive
terraform -chdir=infra/oci init -backend=false -input=false
terraform -chdir=infra/oci validate
bash -n infra/oci/templates/bootstrap.sh.tftpl
.venv/bin/python -m unittest tests.test_oci_terraform -v
```

These checks validate syntax, provider schema and repository contracts. They do not prove tenancy policy, regional image/shape availability, service limits, network paths, Bastion sessions, Instance Principal token exchange, OCIR pull or workload execution.

## Runtime contract

The Compose template retains the measured CoreView laboratory topology:

- `COREVIEW_METRICS_PROVIDER=synthetic`;
- simulator, collector and app use the existing shared network namespace;
- ports 5067 and 9090 publish only to guest loopback;
- read-only container roots, all capabilities dropped and `no-new-privileges`;
- existing CPU/memory limits and health checks;
- SQLite, snapshot, uploads and Docker state live on the attached runtime volume.

The host mounts the runtime volume before starting Docker. The fstab entry omits `nofail`; missing storage fails closed instead of creating a second data set on the boot volume. `coreview-lab.service` is enabled but has `ConditionPathExists=/opt/coreview/.env`, so neither an empty OCIR repository nor absent secrets starts the workload.

## Key references

- [Deployment runbook](DEPLOYMENT_RUNBOOK.md)
- [Destroy runbook](DESTROY_RUNBOOK.md)
- [State backend](STATE_BACKEND.md)
- [Cost estimate](COST_ESTIMATE.md)
- [ADR-021](../../docs/adr/ADR-021-oci-private-single-vm-lab.md)
