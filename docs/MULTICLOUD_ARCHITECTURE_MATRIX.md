# CoreView multi-cloud architecture matrix

Fecha de auditoría: **2026-09-30**. Esta matriz compara las implementaciones versionadas, no recursos ejecutándose. AWS tiene evidencia adicional de un plan real revisado; GCP, Azure y OCI sólo tienen validación estática offline.

## Paridad funcional

| Contrato CoreView | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| Procesos | `simulator`, `collector`, `app` | igual | igual | igual |
| Namespace | `collector` y `app` usan `network_mode: service:simulator` | igual | igual | igual |
| Puertos host | `127.0.0.1:5067` y `127.0.0.1:9090` | igual | igual | igual |
| Datos | 13 PRTG lógicos y 1,000 sensores sintéticos | igual | igual | igual |
| Metrics provider | `synthetic`; PRTG real desactivado | igual | igual | igual |
| Runtime hardening | non-root, root FS read-only, capabilities eliminadas, `no-new-privileges` | igual | igual | igual |
| Inicio | bloqueado hasta que exista `/opt/coreview/.env` | igual | igual | igual |
| Secretos | inicialización interactiva posterior; no están en Terraform, metadata o imagen | igual | igual | igual |
| Runtime data | named volumes de Compose sobre EBS raíz | named volumes sobre Persistent Disk dedicado | named volumes sobre Managed Disk dedicado | named volumes sobre Block Volume dedicado |
| Imagen | tag de commit inmutable en ECR | tag inmutable en Artifact Registry | digest validado en guest | digest validado en guest |

Los puertos 5067/9090 son listeners del host invitado, no reglas cloud de ingreso. La administración crea un túnel hasta el loopback del invitado.

## Inventario estático

| Cloud | Recursos administrados por el stack | Recursos externos |
| --- | --- | --- |
| AWS | 16: VPC, IGW, subnet, route table/ruta/asociación, SG + 3 reglas egress, EC2, ECR, IAM role/policy/attachment/profile | cuenta, backend S3, presupuesto/logging y publicador |
| GCP | VPC/subnet, Router/NAT, 5 firewalls, VM, runtime disk, Service Account, Artifact Registry + IAM, 5 project services | proyecto, backend GCS, presupuesto y bindings humanos IAP/OS Login |
| Azure | RG opcional, VNet, 2 subnets, NSG, NAT + IP/asociaciones, Bastion + IP, NIC, VM, runtime disk/attachment, ACR + AcrPull | suscripción, RG opcional existente, backend Blob, publicador y operadores |
| OCI | VCN, 2 subnets/route tables/security lists, NAT/Service Gateway, NSG + 6 reglas, Bastion, Compute, volume/attachment, OCIR, Dynamic Group + policy | tenancy/compartment, backend Object Storage, sesiones Bastion, publicador y operadores |

## Networking y administración

| Aspecto | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| Topología | subnet pública; EC2 recibe IPv4 pública explícita | subnet privada | subnet privada + `AzureBastionSubnet` | subnets privadas workload + Bastion |
| Salida | IGW; HTTPS global y DNS al resolver VPC | Cloud NAT; HTTPS/DNS/NTP explícitos y deny final | NAT Gateway; HTTPS/DNS/NTP e IMDS explícitos y deny final | NAT para Internet y Service Gateway regional; HTTPS/DNS/NTP/IMDS explícitos |
| Ingress workload | ninguno en SG | TCP 22 sólo desde proxy IAP | TCP 22 sólo desde subnet Bastion | TCP 22 sólo desde IP privada exacta de Bastion |
| IP pública de VM | sí, efímera, sólo para salida | no | no | no |
| Administración | Session Manager, sin SSH | IAP TCP + SSH + OS Login | Bastion Standard native client + SSH | sesión OCI Bastion + SSH |
| Forwarding | SSM directo a 5067; comando análogo para 9090 bajo diagnóstico | SSH-over-IAP con `-L` | túnel Bastion a 22 y segundo SSH `-L` a loopback | sesión Bastion a 22 y segundo SSH `-L` a loopback |
| DNS/NTP/metadata | servicios link-local/VPC nativos; SG no abre ingress | DNS/NTP egress; metadata usada para token SA | service tags DNS/IMDS y NTP; Managed Identity usa IMDS | resolver/NTP/IMDS `169.254.169.254`; Instance Principal depende de IMDS |
| Exposición 5067/9090 | ninguna regla ingress | ninguna regla firewall | ninguna regla NSG | ninguna regla NSG/security list |

Las diferencias son deliberadas. Session Manager no es SSH; IAP termina un túnel TCP y requiere ingress desde su rango; Azure Bastion Standard mantiene infraestructura dedicada y cobro continuo; OCI Bastion crea sesiones efímeras y no llega directamente al loopback de CoreView.

## Registry y cadena de imagen

| Aspecto | ECR | Artifact Registry | ACR | OCIR |
| --- | --- | --- | --- | --- |
| Privacidad | repositorio privado | repositorio privado | admin/anonymous desactivados | `is_public=false` |
| Mutabilidad | tags inmutables | tags inmutables | selección runtime por digest | repo inmutable + selección por digest |
| Runtime auth | EC2 role + token ECR | SA metadata token + Docker config temporal | Managed Identity IMDS + intercambio ACR + config temporal | Instance Principal + helper fijado/verificado + config protegido |
| Scope pull | repo exacto; token auth requiere `*` | reader a repo exacto | `AcrPull` a registry completo | policy read con condición de repo |
| Registry vacío | bootstrap termina; servicio espera secretos pero primer start falla claramente si no existe tag | igual | bootstrap nunca hace pull; inicializador exige digest publicado | igual |
| Actualización | cambiar tag de Terraform/user-data; puede reemplazar EC2 | cambiar input/startup metadata; requiere plan revisado | script guest `update-runtime-image`, sin cambio Terraform | script guest por digest, sin cambio Terraform |
| Rollback | restaurar input/plan y tag existente | restaurar input/metadata y tag existente | digest anterior validado en guest | digest anterior validado en guest |

La inmutabilidad de tag de AWS/GCP evita reutilizar el mismo tag, pero un digest sigue siendo una evidencia más directa del manifiesto exacto. Antes de cualquier despliegue real debe comprobarse la correspondencia entre commit, evidencia CI y digest del registry.

## Bootstrap: requisitos mínimos comunes

1. Ejecutarse con `set -euo pipefail` y marcador de revisión cuando aplica.
2. No contener secretos ni iniciar CoreView antes de `/opt/coreview/.env`.
3. Verificar por SHA-256 todo binario descargado fuera del gestor de paquetes.
4. Impedir que Docker escriba en un destino incorrecto antes del montaje persistente.
5. Rechazar discos con firmas desconocidas; aceptar sólo el filesystem documentado.
6. Hacer que Docker dependa del montaje runtime cuando existe volumen separado.
7. Tolerar registry vacío sin inventar una imagen ni degradar a una fuente pública.
8. Hacer pull sólo de la referencia aprobada y conservar un rollback verificable.
9. Mantener logs útiles sin imprimir tokens, contraseñas o URLs autenticadas.
10. Ser idempotente o requerir explícitamente regenerar/reemplazar el host.

Azure y OCI cumplen el flujo guest por digest y rollback más completo. GCP queda fail-closed ante discos desconocidos tras Phase 1.4A. AWS sigue siendo el diseño mínimo: usa el root EBS para runtime y un tag inmutable incorporado al user-data.

## Persistencia

| Aspecto | AWS | GCP | Azure | OCI |
| --- | --- | --- | --- | --- |
| Boot | EBS gp3 20 GiB, cifrado, delete-on-termination | pd-balanced 20 GiB, auto-delete | OS Managed Disk 32 GiB | boot Block Volume 50 GiB |
| Runtime separado | no | pd-balanced 20 GiB | Managed Disk 32 GiB | Block Volume 50 GiB |
| Mount guard | no aplica: Docker en root | `RequiresMountsFor`, fstab sin `nofail`, firma/fs/UUID validados | igual | igual |
| Protección Terraform | ECR con contenido bloquea delete; EC2/root sin guard | VM deletion protection; Artifact Registry `prevent_destroy` | RG administrado, VM, runtime disk y ACR con `prevent_destroy` | VM, runtime volume y OCIR con `prevent_destroy` |
| Pérdida al destruir VM | runtime se elimina con root EBS salvo backup manual | boot se elimina; runtime disk Terraform también se eliminaría tras aprobar protecciones/plan | runtime protegido hasta retirar guard | runtime protegido hasta retirar guard |
| Recovery | backup/snapshot manual no modelado | snapshot/retención externa no modelada | backup/retención externa no modelada | backup/retención externa no modelada |

AWS es materialmente menos resistente: el runtime y Docker comparten el único root EBS con `delete_on_termination=true`. Es un trade-off aceptado del laboratorio inicial, no paridad con los otros tres diseños. Cambio futuro recomendado: EBS runtime separado, mount guard y decisión de retención; no se aplica aquí porque añadiría recursos y invalidaría el plan AWS 16/0/0.

## Costos consolidados

Estimaciones documentadas, no cotizaciones. Las fechas/regiones y fuentes están en cada `COST_ESTIMATE.md`.

| Cloud | Fecha / región | 24 h | 168 h | 730 h | Residual tras detener compute |
| --- | --- | ---: | ---: | ---: | ---: |
| AWS | 2026-09-28 / us-east-1 | ~USD 0.68 | ~USD 4.74 | ~USD 20.61 | ~USD 1.70/mes + S3/logs mientras root y ECR se retengan; la IPv4 efímera se libera al parar |
| GCP | 2026-09-28 / us-east1 | ~USD 0.69 + variables | ~USD 4.91 normalizado | ~USD 21.35 | ~USD 7.65–8.67/mes + registry/GCS/logs, según asignación NAT efectiva |
| Azure | 2026-09-28 / eastus2 | ~USD 9.64 | ~USD 67.33 | ~USD 292.48 | ~USD 262.11/mes |
| OCI | 2026-09-29 / us-ashburn-1 | ~USD 1.31 | ~USD 9.14 | ~USD 39.70 | ~USD 4.30/mes + variables |

No son precios directamente equivalentes: Azure incluye Bastion Standard y NAT dedicados; AWS usa IPv4 pública sin ingress y Session Manager; GCP modela NAT/IAP; OCI modela Bastion y gateways sin una línea horaria equivalente. Transferencia, logs, taxes, versiones de state y descuentos se excluyen o varían. El valor GCP de 168 h es prorrateo del modelo mensual y debe recalcularse antes de una aprobación.

## Teardown

| Cloud | Destroy ordinario | Protecciones / bloqueos | Persistencia externa | Riesgo residual |
| --- | --- | --- | --- | --- |
| AWS | puede destruir red/EC2/IAM; ECR bloquea si contiene imágenes | `force_delete=false`; decidir imágenes primero | backend S3 real, versiones/locks; Budgets/logs externos | snapshots manuales, ECR, state, logs |
| GCP | requiere desactivar `deletion_protection`; runtime disk se incluye | Artifact Registry `prevent_destroy` | GCS propuesto, proyecto y APIs preservadas | discos/snapshots, repo, state, NAT artifacts/logs |
| Azure | plan destroy falla con guards; teardown en dos etapas | RG administrado, VM, runtime disk y ACR | Blob propuesto; RG preexistente no se destruye | Bastion/NAT/PIPs/disks/ACR/state |
| OCI | plan destroy falla con guards; teardown en dos etapas | VM, runtime volume y OCIR | Object Storage propuesto | volumes/backups/OCIR/state/sessions/logs |

Ningún runbook debe describir `terraform destroy` como eliminación completa mientras existan `prevent_destroy`, deletion protection o repositorios con contenido. Los cuatro runbooks separan workload, artefactos persistentes y backend; la verificación de costo residual es obligatoria.
