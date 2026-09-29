# Phase 1.3 final — OCI offline blueprint freeze

Freeze date: **2026-09-29**

## Verifiable status

```text
Architecture: Implemented.
Terraform: Static validation passed.
Real OCI plan: Not executed.
Workload deployment: Not executed.
OCI resources created: 0.
```

No OCI authentication, tenancy query, plan, apply, destroy, registry publication, Bastion session or workload execution was performed in Phase 1.3. Static checks, fixtures and provider-schema validation are not evidence from OCI.

## Closure findings and corrections

| Severity | Finding | Correction | Residual status |
|---|---|---|---|
| Critical | Workload DNS egress targeted the VCN CIDR, although OCI's default VCN resolver listens on `169.254.169.254`; bootstrap name resolution could fail. | TCP/UDP 53 now targets only `169.254.169.254/32`; regression test added. | Effective traffic remains pending a real deployment. |
| High | SSH ingress allowed the whole dedicated Bastion subnet. | The workload NSG now derives the actual `private_endpoint_ip_address` from `oci_bastion_bastion.lab` and permits only that `/32` on TCP 22. | The provider exposes this value as computed; a real plan/data-path test is still pending. |
| High | Oracle's tutorial demonstrates an Instance Principal helper, but the referenced helper implementation is third-party demonstration code and its source does not itself enforce the OCIR hostname. | The pinned/checksummed binary is installed outside `PATH`; a CoreView wrapper admits only the configured `iad.ocir.io` endpoint, rejects static credential storage and exposes no token file. | The upstream HTTP/SDK behavior and dependency graph still require approval or replacement by a controlled fork before deployment. |
| Medium | IAM policy/Dynamic Group propagation is asynchronous. A first digest pull could fail even with correct configuration. | The runtime performs up to 60 pulls at ten-second intervals and then fails closed; systemd allows 900 seconds. A later operator restart obtains fresh credentials. | A rule update can take substantially longer; do not lengthen retries indefinitely. Verify IAM before runtime initialization. |
| Medium | OCID, private address and namespaced repository outputs were printed as ordinary outputs. | Identity-bearing outputs are now `sensitive`; operators retrieve them deliberately with `terraform output -raw`. | Values still exist in protected state, as required by Terraform. |
| Medium | Git was installed only to compile a source archive downloaded with `curl`; it was unused. | The Git package input/install/removal was deleted. Go remains temporary and is removed after the verified build. | Exact Go and transitive module evidence remains a preflight input. |
| Low | A runbook incorrectly described `/opt/coreview/.env` as residing on the runtime volume. | Documentation now states that it is a root-only guest boot-filesystem file, outside Terraform metadata. | VM replacement requires deliberate secret reinitialization; the secret is not claimed as persistent runtime data. |

No dependency cycle was found. The graph is deliberately `Compute instance → exact-instance Dynamic Group → repository-scoped IAM policy`. Bootstrap never pulls an image, so Compute can be created before its IAM grant. The first pull occurs only after publication, interactive secret initialization and propagation review.

## Final architecture

```text
Zorin operator
  |
  | short-lived OCI Bastion port-forwarding session
  | local 22022 -> private Compute TCP 22
  v
OCI VCN — us-ashburn-1
  +-- private Bastion endpoint subnet
  |     +-- OCI Bastion Standard
  |
  +-- private workload subnet
        +-- NSG ingress: Bastion private endpoint /32 -> TCP 22
        +-- NSG egress:
        |     HTTPS/443 -> 0.0.0.0/0
        |     DNS TCP+UDP/53 -> 169.254.169.254/32
        |     NTP/123 -> 169.254.169.254/32
        |     IMDSv2/80 -> 169.254.169.254/32
        +-- Service Gateway -> regional OCI services
        +-- NAT Gateway -> outbound Internet dependencies
        +-- private VM.Standard.E4.Flex (1 OCPU, 8 GiB)
              +-- exact Oracle Linux 9 x86_64 image OCID (future preflight)
              +-- encrypted 50 GiB boot volume
              +-- protected encrypted 50 GiB runtime volume
              +-- exact-instance Dynamic Group -> read one OCIR repository
              +-- Docker Compose synthetic lab by manifest digest

Nested guest SSH forwards:
  laptop 5067 -> guest 127.0.0.1:5067
  laptop 9090 -> guest 127.0.0.1:9090

External to stack:
  private versioned Object Storage Terraform backend + native lock object
```

OCI Bastion reaches guest SSH. It is not claimed to connect directly to services bound to guest loopback.

## Static resource inventory

The module defines **23 Terraform resources** and one datasource:

- one VCN;
- two private subnets;
- two route tables;
- one NAT Gateway and one Service Gateway;
- two explicit empty/minimal security lists;
- one workload NSG and six rules;
- one OCI Bastion;
- one private Compute instance;
- one runtime Block Volume and one attachment;
- one private immutable OCIR repository;
- one exact-instance Dynamic Group;
- one repository-scoped IAM policy;
- datasource for the regional OCI services network.

The tenancy, compartment, availability domain, Object Storage state bucket, Bastion sessions and publisher credentials are external inputs/operations and are not owned by this stack.

## IAM matrix

| Actor | Intended capability | Explicit exclusion |
|---|---|---|
| Terraform operator | manage only the reviewed lab infrastructure and IAM resources | no application secrets or publisher token in Terraform |
| Backend operator | read/write/delete state and lock objects in the dedicated bucket | no workload or OCIR publication rights |
| Recovery administrator | bucket versions, lifecycle and final backend removal | separate from routine state writing |
| Image publisher | publish the reviewed image to the single CoreView repository | no VM, state or runtime administration rights |
| Compute Instance Principal | `read repos` for the `coreview` repository condition only | no push, IAM mutation or broad compartment read |
| Bastion operator | create/inspect/terminate short-lived sessions and read target metadata | no public SSH or application ingress |
| Guest administrator | SSH through Bastion, initialize synthetic secrets, select an approved digest | no credential in Terraform or cloud-init |

The Dynamic Group rule names the exact instance OCID, not a compartment or tag collection. The policy references the Dynamic Group by OCID and restricts `target.repo.name`.

## Networking matrix

| Direction | Source | Destination | Protocol/port | Purpose |
|---|---|---|---|---|
| Ingress | computed Bastion private endpoint `/32` | workload NSG | TCP 22 | guest SSH transport only |
| Egress | workload | Internet via NAT | TCP 443 | pinned package/source/binary downloads |
| Egress | workload | `169.254.169.254/32` | TCP+UDP 53 | OCI default VCN resolver |
| Egress | workload | `169.254.169.254/32` | UDP 123 | OCI time synchronization |
| Egress | workload | `169.254.169.254/32` | TCP 80 | IMDSv2 and Instance Principal exchange |
| Route | workload subnet | regional service CIDR | Service Gateway | OCI service path |

There is no Internet Gateway, public VM IP, public SSH, or inbound rule for 5067/9090. HTTPS egress is intentionally broad because bootstrap retrieves pinned artifacts from multiple official hosts; checksums are mandatory. Egress domain allow-listing would require an additional controlled proxy/firewall and remains outside this lab blueprint.

## OCIR credential chain audit

The implemented sequence is:

1. an independently reviewed source commit is downloaded over TLS from a fixed host;
2. the archive SHA-256 is verified;
3. exact Go tooling builds `linux/amd64` with `-mod=readonly`, `-trimpath` and `-buildvcs=false`;
4. the resulting binary SHA-256 is verified and installed as a non-direct upstream helper;
5. Go is removed;
6. Docker invokes the CoreView wrapper only for `iad.ocir.io`;
7. the wrapper rejects other registry endpoints and any credential `store` operation;
8. the upstream helper obtains ephemeral credentials with the Instance Principal SDK flow;
9. IAM permits read only on the named repository;
10. runtime accepts only `repository@sha256:<64-hex>`.

Repository immutability prevents replacement of an existing image version, but it is not treated as a substitute for digest selection. The runtime digest binds the content actually executed.

**Accepted limitation:** Oracle's tutorial validates the architectural pattern, not ownership/hardening of the third-party helper implementation. This freeze does not claim the helper was exercised against OCIR. Before a real apply, either complete an independent source/dependency review of the pinned revision and reproduce its binary checksum, or replace it with a controlled, CI-built fork that retains host allow-listing, redirect/timeout controls and no credential persistence. A static OCIR auth token in cloud-init, Terraform, metadata or the image remains rejected.

## Bootstrap and persistence

Bootstrap is idempotent by a versioned marker and fails closed:

- Docker is masked before package installation;
- Docker repository metadata, package NEVRAs, Compose, helper source and helper binary are pinned/checksummed;
- the attached device must appear and be block storage;
- only a signature-free device is formatted;
- only ext4 is accepted;
- the filesystem is mounted by UUID with `nodev,nosuid` and without `nofail`;
- Docker has `RequiresMountsFor=/var/lib/docker`;
- Docker state, Compose volumes, SQLite, snapshots and uploads therefore remain on the attached runtime volume;
- absent image or secrets cannot start CoreView;
- `.env` is root-owned mode 0600 on the guest boot filesystem and never enters Terraform state or metadata;
- release updates change only the guest digest and restore the prior configuration on failure;
- an unknown disk signature, failed mount, checksum mismatch, IAM failure or pull failure stops startup.

The fixed `/dev/oracleoci/oraclevdb` path is a future preflight/deployment assertion, not a fact verified against OCI. The first deployment must confirm attachment naming and reboot behavior before any operational data is accepted.

## Backend and recovery

The native Terraform `oci` backend is declared without coordinates. A separately approved private Object Storage bucket must provide:

- versioning;
- server-side encryption;
- no public access;
- a dedicated state key and sibling native lock object;
- routine `OBJECT_INSPECT`, `OBJECT_CREATE`, `OBJECT_READ` and `OBJECT_DELETE` permissions scoped to the bucket;
- independent recovery administration;
- reviewed retention of non-current versions and multipart cleanup;
- no automatic expiry of current state.

`backend.hcl`, tfvars, state, plans and recovered copies remain ignored/private. Recovery requires stopping writers, verifying lock ownership, selecting a version without exposing content, preserving the current state, two-person review and a refresh-only plan. Stack teardown does not delete the backend.

## Cost review

The model remains arithmetically consistent with its dated assumptions:

- 24 hours: **~USD 1.31**;
- 168 hours: **~USD 9.14**;
- 730 hours: **~USD 39.70**.

It assumes E4 Flex at 1 OCPU/8 GiB, 100 GiB balanced Block Volume, about 2 GiB combined OCIR/state, no paid support and unknown/variable transfer, requests, logs and taxes. Stopping Compute removes modeled compute hours but leaves approximately **USD 4.30/month** of modeled volume/registry/state storage, plus retained versions/logs/transfer. Prices, billing granularity, capacity and limits must be refreshed before approval.

Ampere A1 remains an unvalidated economic alternative. The current release chain is `linux/amd64`; no ARM image, tests, SBOM, scanner evidence, smoke or performance evidence exists. Always Free eligibility, quota and capacity are not assumed.

## Validation record

The final validation is offline and intentionally credential-free:

| Check | Result |
|---|---|
| Terraform 1.16.4 format | passed |
| `init -backend=false` / OCI provider 8.29.0 | passed; lockfile reused |
| Terraform validate | passed |
| Provider schema / dependency graph | validated offline; no cycle observed |
| OCI structural/security tests | 12 passed |
| Bootstrap `bash -n` | passed |
| Rendered Compose config | passed with synthetic validation values |
| Python regression suite | 327 passed, 0 failed, 0 skipped |
| JavaScript regression suite | 18 passed, 0 failed, 0 skipped |
| Python compileall | passed |
| Secret/sanitization review | passed through regression suite and targeted review |
| `git diff --check` | passed |
| Offline IaC scan | Trivy 0.74.0: 0 findings with embedded checks |

The scan uses the scanner's embedded offline checks when network updating is disabled. It must not be represented as using the newest policy bundle.

## Protected teardown

Stage A stops CoreView/Compute and terminates sessions while retaining protected data/release assets. Stage B requires a reviewed source change that removes `prevent_destroy` only from approved resources, a new full destroy plan, separate approval, and an independent backend retention decision. Guards are not removed merely to make `destroy` succeed.

Residual-cost inventory must cover stopped instances, boot/runtime/backups, OCIR layers, Object Storage versions/locks/multipart uploads, sessions, gateways, VNICs/IPs, logs, Dynamic Groups and policies.

## Remaining deployment gates

1. authorize and verify the tenancy, compartment and operator roles;
2. resolve/fix an availability domain and Oracle Linux 9 x86_64 image OCID;
3. verify shape, volume, Bastion and OCIR availability/limits;
4. approve non-overlapping CIDRs and current operator source CIDR;
5. resolve all exact NEVRAs/checksums against the pinned image;
6. approve or replace the pinned third-party credential helper;
7. bootstrap/rehearse the separate backend and recovery procedure;
8. refresh costs and configure notification-only budget alerts;
9. run/review a real plan;
10. publish only the exact CI-validated `linux/amd64` image;
11. validate Bastion, Instance Principal, digest pull, DNS/NTP, mount/reboot, smoke, update/rollback and protected teardown.

Until those gates are completed, the only accurate final state is: **static validated / real plan not executed / not deployed / zero OCI resources created**.

## Authoritative references

- OCI Bastion networking and endpoint `/32`: <https://docs.oracle.com/en-us/iaas/Content/Bastion/Tasks/connectingtosessions.htm>
- OCI Bastion port forwarding: <https://docs.oracle.com/en-us/iaas/Content/Bastion/Tasks/connect-port-forwarding.htm>
- OCI VCN DNS resolver: <https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/dns.htm>
- OCI Instance Principals: <https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm>
- Dynamic Group propagation: <https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/managingdynamicgroups.htm>
- Oracle credential-helper tutorial: <https://docs.oracle.com/en/learn/cred-helper/index.html>
- OCIR repository policies: <https://docs.oracle.com/en-us/iaas/Content/Registry/Concepts/registrypolicyrepoaccess.htm>
- OCI container image digests: <https://docs.oracle.com/en-us/iaas/Content/Registry/Concepts/registryconcepts.htm>
- Terraform OCI backend: <https://developer.hashicorp.com/terraform/language/backend/oci>
- [ADR-021](adr/ADR-021-oci-private-single-vm-lab.md)
- [OCI README](../infra/oci/README.md)
- [Deployment runbook](../infra/oci/DEPLOYMENT_RUNBOOK.md)
- [Destroy runbook](../infra/oci/DESTROY_RUNBOOK.md)
- [State backend](../infra/oci/STATE_BACKEND.md)
- [Cost estimate](../infra/oci/COST_ESTIMATE.md)
