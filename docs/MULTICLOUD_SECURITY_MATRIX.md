# CoreView multi-cloud security matrix

Fecha de auditoría: **2026-09-30**. Los controles descritos son controles de código y diseño. Salvo el backend y plan AWS indicados, no son evidencia de controles efectivos en una cuenta cloud.

## Identidad e IAM

| Rol / riesgo | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| Runtime identity | EC2 IAM Role | Service Account sin key | System Assigned Managed Identity | Instance Principal mediante Dynamic Group exacto |
| Pull de imagen | repo exacto; `GetAuthorizationToken` global requerido por API | `roles/artifactregistry.reader` en repo | `AcrPull` en ACR completo | `read repos` condicionado por nombre de repo |
| Administración | SSM operator separado | bindings humanos IAP/OS Login fuera del stack | RBAC Bastion/VM fuera del stack + SSH key | operador Bastion + SSH key |
| Publisher | fuera del runtime; push ECR separado | fuera del runtime; writer separado | `AcrPush` separado | publisher OCIR separado |
| Terraform operator | permisos de creación del stack y backend cotidiano separados | proyecto autorizado y backend separado | subscription allow-list/RG y backend separado | tenancy/compartment allow-list y backend separado |
| Break-glass/recovery | state versions, EBS/ECR/logs bajo aprobación independiente | generations GCS y recursos retenidos | Blob versions/soft-delete y protected resources | Object versions, volumes y OCIR bajo doble revisión |
| Propagación | role/profile y SSM readiness | SA/IAM + OS Login/IAP | Managed Identity/AcrPull | Dynamic Group/policy; mayor latencia/variabilidad documentada |
| Limitación | managed policy SSM excede sólo ECR pull | reader role no es una acción única | scope mínimo disponible es registry | helper/SDK y policy language añaden complejidad |

No existen access keys, Service Account keys, client secrets, ACR admin credentials, OCI API private keys o auth tokens permanentes dentro de Terraform, templates o imágenes.

## Backends de estado

| Cloud | Estado | Encryption / public access | Versioning / locking | Operación cotidiana | Destrucción |
| --- | --- | --- | --- | --- | --- |
| AWS S3 | **creado y verificado** | SSE-S3, Block Public Access, BucketOwnerEnforced, deny non-TLS | versioning + `.tflock`; noncurrent 90 d | ruta exacta de state/lock con role autorizado | independiente; no lo elimina workload destroy |
| GCP GCS | propuesto, no creado | Public Access Prevention, uniform access, Google-managed encryption/CMEK opcional | Object Versioning + locking nativo | object admin o custom role en bucket dedicado | independiente y autorizada después de retención |
| Azure Blob | propuesto, no creado | HTTPS/TLS 1.2, shared key off, public off, firewall allow-list, encryption plataforma | versioning/soft-delete + blob lease | Entra `Storage Blob Data Contributor` en container | independiente; soft-delete/versiones revisadas |
| OCI Object Storage | propuesto, no creado | bucket privado, encryption por defecto | versioning + objeto `.lock` nativo | inspect/create/read/delete de objetos en bucket | independiente; versiones/locks/lifecycle revisados |

`backend.hcl`, `terraform.tfvars`, state, planes y copias de recuperación permanecen ignorados. Marcar outputs como `sensitive` evita su impresión rutinaria, pero **no** los elimina del state; proteger el backend sigue siendo obligatorio.

## Superficie de red

| Control | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| VM pública | IPv4 efímera, SG sin ingress | no | no | no |
| SSH público | no | no; IAP range only | no; Bastion subnet only | no; Bastion endpoint only |
| App/simulator ingress cloud | ninguno | ninguno | ninguno | ninguno |
| Egress genérico | TCP 443 | TCP 443 | TCP 443 | TCP 443 |
| DNS/NTP | resolver/link-local nativo | reglas explícitas 53/123 | service tag DNS + 123 | link-local 53/123 |
| Metadata token | IMDSv2 required, hop limit 1 | Metadata-Flavor | IMDS Managed Identity | IMDS Instance Principal |
| Riesgo SSRF | containers sin acceso deliberado a APIs cloud; metadata pertenece al host | igual | igual | igual |
| Egress scanner | Trivy AWS-0104: CRITICAL por TCP 443 a `0.0.0.0/0`; aceptado para endpoints y paquetes sin VPC endpoints | HTTPS también global por Cloud NAT; embedded rules no lo marcaron | HTTPS global por NAT; embedded rules no lo marcaron | HTTPS global por NAT; embedded rules no lo marcaron |

## Secretos y bootstrap

- El bootstrap instala host/runtime y termina sin arrancar CoreView si falta `/opt/coreview/.env`.
- El operador inicializa un password sintético y un secret Flask mediante prompt oculto; Terraform no los recibe.
- Los archivos `.env` son root-owned y modo 0600 en el guest.
- Los pulls Azure/GCP usan Docker configs temporales. OCI protege config/helper y usa credenciales efímeras. AWS usa el flujo ECR del host y requiere una revisión futura de almacenamiento/refresh del token Docker.
- Ninguna plantilla contiene datos de empresa, endpoints internos, claves privadas o credenciales.
- Los outputs con identificadores, IPs privadas, nombres de recursos o comandos derivados son sensibles. Sólo región/zona permanecen como outputs ordinarios.

## Evidencia y límites

| Evidencia | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| Código Terraform | implementado | implementado | implementado | implementado |
| `fmt/init -backend=false/validate` | validado | validado | validado | validado |
| Plan real | 16 add / 0 change / 0 destroy, revisado antes de Phase 1.4A | no ejecutado | no ejecutado | no ejecutado |
| Backend real | S3 creado/verificado | no creado | no creado | no creado |
| Runtime cloud | no validado | no validado | no validado | no validado |
| Workload desplegado | no | no | no | no |

El cambio Phase 1.4A que suprime outputs y endurece GCP cambia el código posterior al plan AWS guardado. Por tanto, el plan 16/0/0 se conserva como evidencia histórica válida de aquella revisión, pero **no debe aplicarse**; un futuro despliegue AWS requiere regenerar y revisar un nuevo plan.
