# ADR-020: Azure private single-VM laboratory

- Status: Accepted for static implementation
- Date: 2026-09-28
- Scope: Phase 1.2A-1.2B

## Context

CoreView needs an Azure architecture that preserves the already measured synthetic Docker workload and its shared network namespace without presenting a dashboard or SSH listener to the public Internet. The design must remain usable from a Linux operator workstation, must not place runtime secrets in Terraform, and must make outbound connectivity explicit because Azure is removing implicit default outbound access for new virtual networks.

The workload needs one small x86_64 Linux host, private image distribution, persistent Docker/SQLite storage, and a secure way to tunnel `localhost:5067` to a laptop. It does not need a public load balancer, AKS, a database service or high availability.

## Decision

The primary blueprint uses:

- one private `Standard_B2s` Linux VM in `eastus2`;
- an exact Canonical Ubuntu 22.04 LTS Gen2 image version supplied at preflight;
- Trusted Launch controls (`secure_boot_enabled` and `vtpm_enabled`);
- one Standard SSD OS disk and one protected Standard SSD runtime disk;
- a private workload subnet with `default_outbound_access_enabled=false`;
- one Standard NAT Gateway and Standard public IP for explicit outbound HTTPS/DNS/NTP dependencies;
- Azure Bastion **Standard**, with tunneling enabled, in a dedicated `/26` or larger `AzureBastionSubnet`;
- no VM public IP and an NSG allowing inbound TCP 22 only from the Bastion subnet;
- Azure Container Registry Basic with admin and anonymous access disabled;
- a system-assigned VM identity with `AcrPull` scoped to that registry only;
- a digest-qualified container image selected at guest runtime, outside Terraform `custom_data`;
- an externally bootstrapped Azure Blob backend using Microsoft Entra data-plane authentication;
- an idempotent, secretless bootstrap that mounts the data disk at `/var/lib/docker`, installs a checksum-verified Compose plugin, and leaves CoreView stopped until secrets are initialized interactively.

The Azure VM's host ports 5067 and 9090 remain bound to loopback. `app` and `collector` share the simulator network namespace exactly as in the local, AWS and GCP lab contracts.

### Administrative access

Standard is selected because Microsoft documents native-client support only for Standard and Premium. The Zorin operator first starts a Bastion TCP tunnel to VM port 22 and then establishes an SSH local forward through that tunnel. Developer and Basic cannot support this CLI/native-client flow. Premium supports it but adds private-only Bastion and session recording that the synthetic lab does not require.

Official capability references:

- <https://learn.microsoft.com/azure/bastion/bastion-sku-comparison>
- <https://learn.microsoft.com/azure/bastion/native-client>
- <https://learn.microsoft.com/azure/networking/design-guide/developer-admin-access>

### Explicit outbound connectivity

Azure documents NAT Gateway as the recommended explicit outbound method and notes that new virtual networks become private by default after 2026-03-31. The main design therefore does not rely on default outbound access.

- <https://learn.microsoft.com/azure/nat-gateway/nat-gateway-design>
- <https://learn.microsoft.com/azure/nat-gateway/nat-overview>

## Alternatives considered

### A. Short-duration economical laboratory

A VM with an explicit Standard public IP, an NSG temporarily restricted to the operator's current public prefix, and direct SSH local forwarding is much cheaper because it removes NAT Gateway and dedicated Bastion. This alternative exposes a public SSH endpoint and requires careful source-prefix rotation and optional JIT controls, so it is not the frozen main design.

Bastion Developer is free and can provide a single browser session in supported regions. It does **not** support Azure CLI/native-client connections, so it cannot provide the required Linux laptop tunnel. Bastion Basic is dedicated and supports browser SSH, but it also lacks native-client support. Neither is presented as supporting the required port-forward workflow.

### B. Private laboratory with operator tunnel (selected)

Bastion Standard plus NAT Gateway keeps the VM private, provides the documented native-client tunnel, and makes egress deterministic. Its fixed hourly cost is materially higher than AWS Session Manager or GCP IAP for a one-VM lab.

### C. Bastion Premium

Premium adds private-only Bastion and session recording. It is the stronger production/compliance option, but its hourly charge and features are not justified for synthetic Phase 1.2. Native-client sessions also have recording limitations documented by Microsoft.

### D. Point-to-site VPN

An OpenVPN-based P2S gateway can provide broader private network access from Linux, but adds gateway configuration, certificates/Entra integration, operational scope and cost. It is excessive for one VM and one forwarded dashboard.

### E. VM Run Command or serial console only

These can help recover or administer a VM, but they do not provide the interactive SSH plus local dashboard forwarding contract and are not the primary access path.

## Consequences

Positive:

- VM, dashboard and simulator have no public listeners.
- Runtime image access is keyless and read-only.
- Explicit NAT avoids dependence on implicit outbound access.
- The runtime disk and registry have accidental-destruction guards.
- Bootstrap cannot launch an unreviewed or tag-substituted image because guest initialization and Compose accept only the expected ACR repository plus verified digest.
- Runtime image updates atomically replace only the guest configuration and restore the prior digest if restart fails; Terraform and VM `custom_data` stay unchanged.
- Azure-specific concerns stay under `infra/azure/`; the CoreView application is unchanged.

Trade-offs and accepted risks:

- Bastion Standard dominates cost and is billed continuously while deployed.
- NAT Gateway and two public IP resources also have fixed hourly cost.
- The Basic registry is private by authentication/RBAC, but its endpoint is public; network-private ACR would require Premium plus Private Link and DNS.
- The VM identity has registry-scoped `AcrPull`, not repository-scoped ABAC. The registry contains only the CoreView lab repository in this design.
- Platform-managed encryption protects disks at rest; customer-managed keys and their recovery lifecycle are deferred.
- No Log Analytics workspace is created. Managed boot diagnostics and local bounded Docker logs are the initial diagnostics; central retention is deferred.
- A real Azure plan, quota check, RBAC check, bootstrap execution and workload deployment remain untested.

The status is: **Architecture implemented; static validation is required; real Azure plan not executed; workload not deployed.**
