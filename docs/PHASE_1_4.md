# Phase 1.4A — Multi-Cloud Final Technical Audit

Fecha de auditoría: **2026-09-30**.

## Alcance y evidencia

La auditoría revisó el Terraform, lockfiles, templates Compose, bootstraps, IAM, networking, registries, backends, costos, runbooks de deployment/destroy y tests de `infra/aws`, `infra/gcp`, `infra/azure` e `infra/oci`. No se autenticó contra ninguna nube, no se ejecutó un plan real nuevo, no hubo `apply`/`destroy`, no se publicó una imagen y no se creó ni modificó ningún recurso cloud.

Vocabulario normalizado:

- **Implemented**: existe código Terraform, scripts/templates y runbooks versionados.
- **Static validation passed**: `fmt`, `init -backend=false`, `validate` y checks offline pasaron; no prueba APIs o recursos cloud.
- **Real plan executed**: Terraform consultó una cuenta autorizada y produjo un plan revisado.
- **Not deployed**: el workload CoreView nunca fue arrancado en ese proveedor.
- **Backend created**: existe y fue verificado fuera del stack.
- **Backend proposed**: hay diseño/runbook, pero no se creó.
- **Runtime cloud validated/not validated**: confirma o niega un smoke real de la carga en cloud.

## Estado central verificable

| Cloud | Architecture | Static validation | Real plan | Workload | State backend |
| --- | --- | --- | --- | --- | --- |
| AWS | Implemented | Passed | Executed: 16 add / 0 change / 0 destroy | Not deployed | Created and verified |
| GCP | Implemented | Passed | Not executed | Not deployed | Proposed, not created |
| Azure | Implemented | Passed | Not executed | Not deployed | Proposed, not created |
| OCI | Implemented | Passed | Not executed | Not deployed | Proposed, not created |

En las cuatro nubes, **Runtime cloud: not validated**. El plan AWS 16/0/0 es evidencia real histórica, pero el endurecimiento de outputs de Phase 1.4A cambia el código respecto de ese artefacto. No debe aplicarse el plan guardado: antes de un despliegue se debe regenerar y revisar un plan completo.

## Hallazgos

### Corregidos ahora

1. **GCP podía formatear un Persistent Disk con una firma que `blkid` no reconociera.** El bootstrap ahora enmascara Docker antes de instalarlo, usa `blkid -p`, rechaza firmas detectadas por `wipefs`, exige `ext4` y UUID no vacío, y sólo después habilita Docker. Esto elimina escritura accidental en boot disk y formateo ambiguo.
2. **AWS, GCP y Azure imprimían identificadores cloud como outputs ordinarios; OCI conservaba un nombre de repositorio ordinario.** Todos los outputs con IDs, IP privada, nombres de recursos, repositorios o comandos derivados son ahora `sensitive`. Sólo región/zona permanece rutinaria. Los valores siguen dentro del state y el backend debe protegerse.
3. **Faltaba un contrato transversal ejecutable.** `tests/test_multicloud_contract.py` congela topología Compose, loopback, provider sintético, versiones Terraform/provider, backends, archivos ignorados, outputs sensibles y estados de evidencia.

### Aceptados o diferidos

- **Persistencia AWS**: el runtime comparte el root EBS de 20 GiB con `delete_on_termination=true`. Es menos resistente que los discos runtime separados de GCP/Azure/OCI. Añadir EBS separado sería una mejora, pero cambia inventario/costo y requiere un nuevo plan real; se difiere deliberadamente.
- **Selección de imagen**: AWS/GCP usan tags inmutables de commit; Azure/OCI usan manifest digest suministrado al guest. Ambos mecanismos evitan `latest`, pero no son evidencia idéntica. Se recomienda digest en los cuatro stacks en una futura revisión autorizada.
- **Actualización sin reemplazo**: Azure/OCI actualizan digest dentro del guest; AWS puede reemplazar la instancia por cambios de user-data y GCP cambia startup metadata. Se conserva el diseño actual y se documenta la diferencia.
- **Pinning de paquetes OS**: Azure/OCI fijan más inputs que AWS/GCP; repositorios y paquetes del sistema todavía exigen un preflight de disponibilidad y evidencia antes de desplegar. No se fuerza una estrategia uniforme sin validar cada distribución.
- **Auth del registry**: GCP/Azure usan Docker config temporal; OCI usa un helper auditado con credenciales efímeras; AWS conserva el flujo `docker login` ECR del host. Antes de desplegar AWS debe revisarse refresh/almacenamiento del token.
- **Backups**: ningún stack crea snapshots o backup policy. `prevent_destroy` reduce borrado accidental, pero no sustituye backup o restore rehearsal.
- **Cloud effectiveness**: IAM efectivo, quotas, rutas, Bastion/IAP/SSM, imágenes regionales y pull real no están validados en GCP/Azure/OCI; AWS tampoco tiene runtime desplegado.

## Contrato funcional común

Los cuatro templates contienen `simulator`, `collector` y `app`; `collector`/`app` comparten el namespace del simulador; 5067 y 9090 se publican sólo en `127.0.0.1`; el provider es `synthetic`; los containers conservan root FS read-only, capabilities eliminadas y `no-new-privileges`; CoreView espera secretos interactivos; y los named volumes conservan SQLite, snapshot y uploads en el storage documentado.

La evidencia local del producto sigue siendo 13 servidores lógicos, 1,000 sensores sintéticos y cero errores del collector. La auditoría no presenta esos datos como telemetría cloud.

Detalle: [matriz de arquitectura](MULTICLOUD_ARCHITECTURE_MATRIX.md).

## Seguridad e IAM

Cada runtime usa identidad sin clave permanente: EC2 role, GCP Service Account, Azure Managed Identity y OCI Instance Principal/Dynamic Group. El publisher, operador Terraform, operador administrativo y dueño de recovery/backend permanecen separados en los runbooks. El alcance pull difiere: repo exacto en AWS/GCP/OCI cuando el servicio lo permite; ACR concede `AcrPull` a nivel registry.

Detalle: [matriz de seguridad](MULTICLOUD_SECURITY_MATRIX.md).

## Terraform y backends

- Terraform está fijado en `1.16.4` en los cuatro stacks.
- Providers fijados: AWS `6.61.0`, Google `7.17.0`, AzureRM `5.7.0`, OCI `8.29.0`.
- Los cuatro lockfiles están versionados.
- Existen guards de cuenta/proyecto/subscription/tenancy-compartment y variables obligatorias.
- `backend.hcl`, tfvars, state y planes permanecen ignorados.
- AWS tiene S3 real creado/verificado con encryption, versioning, Block Public Access y lockfile.
- GCS, Azure Blob y OCI Object Storage son propuestas externas al workload; no existen por evidencia de este repositorio.

## Administración privada

| Cloud | Ingress requerido | IP pública de VM | Dependencia SSH | Port forwarding | Costo/operación |
| --- | --- | --- | --- | --- | --- |
| AWS Session Manager | SG: ninguno | sí, sólo salida | no | documento SSM | baja infraestructura; depende de SSM/IAM/agent |
| GCP IAP + OS Login | TCP 22 desde rango IAP | no | sí | SSH sobre IAP | IAP sin gateway dedicado; bindings humanos externos |
| Azure Bastion Standard | TCP 22 desde subnet Bastion | no | sí | Bastion tunnel + SSH `-L` | host dedicado costoso; alta auditabilidad/RBAC |
| OCI Bastion | TCP 22 desde endpoint privado exacto | no | sí | sesión a SSH + SSH `-L` | sesión con TTL; requiere claves y flujo Bastion |

No se establece ranking: cada mecanismo tiene semántica, costo y superficie distinta.

## Costos y teardown

Los totales consolidados son aproximadamente AWS USD 20.61, GCP USD 21.35, Azure USD 292.48 y OCI USD 39.70 para 730 h, bajo fechas, regiones y supuestos distintos. La tabla de 24 h, 168 h, residual tras stop y exclusiones está en la matriz de arquitectura. Budgets son alertas, no límites duros.

Los runbooks no prometen un destroy completo cuando guards o registries lo impiden. AWS puede quedar bloqueado por ECR con imágenes; GCP requiere cambiar deletion protection y decidir Artifact Registry; Azure/OCI requieren teardown protegido en dos etapas. Todos conservan el backend fuera del workload y exigen inventario residual.

## Validación ejecutada

Ejecutada offline, sin credenciales cloud:

- suite Python completa: **332 tests, 332 passing, 0 failing, 0 skipped**;
- suite JavaScript: **18 tests, 18 passing**;
- `terraform fmt -check -recursive` en los cuatro stacks: **passed**;
- `terraform init -backend=false` y `terraform validate` en los cuatro stacks: **passed**; AWS se repitió con un `TF_DATA_DIR` temporal limpio porque el directorio de trabajo local conservaba metadata del backend S3;
- sintaxis Bash de templates/scripts: **passed**;
- `docker compose config`: **passed**;
- `python -m compileall .`: **passed**;
- sanitization/secret regression tests: **passed**;
- `git diff --check`: **passed**.

Trivy 0.74.0 se ejecutó offline con la imagen fijada y checks embebidos. Reportó **1 CRITICAL (AWS-0104)** por HTTPS egress `0.0.0.0/0` en AWS; no reportó HIGH/CRITICAL en los otros targets. Se acepta como requisito operativo documentado para SSM, ECR, paquetes y artefactos sin VPC endpoints, limitado a TCP 443. El resultado tiene alcance limitado: no cargó checks descargables y evaluó sin tfvars operativos. No equivale a políticas actuales ni a un assessment de cuenta. Ningún test local demuestra recursos cloud reales.

## Acciones opcionales para despliegues futuros

1. Regenerar el plan AWS completo por el cambio de outputs y revisar que siga sin destroy/replacement inesperado.
2. Decidir si AWS adopta EBS runtime separado y digest; si se acepta, tratarlo como cambio de arquitectura y costo.
3. Crear/verificar GCS, Blob u Object Storage sólo bajo aprobaciones independientes.
4. Resolver/pinear imágenes OS regionales y revisar quotas/precios actuales.
5. Publicar exclusivamente el artefacto exacto validado por CI y verificar digest.
6. Ejecutar un plan real completo por GCP/Azure/OCI y revisar IAM/routing/effective policy.
7. Inicializar secretos en guest, arrancar CoreView y ejecutar smoke/restart/persistence/rollback.
8. Probar restore desde backup y teardown con inventario de costo residual.
9. Mantener `validated_against_real_prtg: false` hasta una prueba autorizada independiente.

## Cierre

Phase 1.4A deja cuatro blueprints implementados y estáticamente coherentes, sin fingir equivalencia ni evidencia cloud inexistente. No se inició portafolio, landing page ni despliegue.
