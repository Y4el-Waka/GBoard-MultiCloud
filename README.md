# PRTG Network Dashboard

Dashboard web en Flask para monitoreo centralizado de sensores PRTG, con enfoque NOC, administración de usuarios, administración de servidores, histórico por sensor, pausas desde la plataforma y alertas sonoras personalizadas.

El proyecto está pensado para correr en una VM Linux/Ubuntu Server como servicio, con `monitor.py` recolectando datos de PRTG y `app.py` sirviendo la interfaz web.

---

## Características principales

- Dashboard web en Flask.
- Interfaz estilo NOC / full black.
- Login con usuarios locales.
- Roles:
  - `lector`
  - `admin`
  - `admon`
- Gestión de usuarios desde UI.
- Gestión de servidores PRTG desde UI.
- Monitor multi-servidor.
- Recarga automática de `servers.json` sin reiniciar el monitor.
- Dashboard principal con auto-refresh cada 30 segundos.
- Histórico por sensor con gráfica multi-serie:
  - Tiempo de Ping
  - Mínimo
  - Máximo
  - Pérdida de paquetes
  - Tiempo de inactividad
  - Cobertura
- Pausar sensores desde la plataforma:
  - 1 hora
  - 3 horas
  - 24 horas
  - hasta fecha/hora definida
  - indefinidamente
- Reanudar escaneo desde la plataforma.
- Botones rápidos por sensor:
  - Pausa / Reanudar
  - Histórico
- Alertas sonoras por usuario:
  - sonido default generado por navegador
  - carga de archivo `.mp3`
  - volumen por usuario
  - repetir mientras existan sensores Down
- Auditoría backend para acciones administrativas y acciones sobre sensores.
- Sanitización de texto en frontend para reducir riesgo de XSS con nombres/mensajes provenientes de PRTG.
- Escritura atómica de archivos generados por el monitor.
- Retries y timeouts para consultas a PRTG.
- CLI para inicializar base de datos y crear usuarios.

---

## Estado actual

Este proyecto ya es funcional para monitoreo PRTG en producción o staging.

Pendientes planeados:

- Alertas por correo SMTP.
- Panel visual de auditoría.
- Integración futura con Zabbix.
- Integración futura con Issabel / troncales.
- Despliegue final con Gunicorn + Nginx + systemd.
- Backup automático de base de datos, configuración y sonidos.

---

## Estructura del proyecto

```text
.
├── app.py
├── monitor.py
├── conexion.py
├── requirements.txt
├── .env.example
├── servers.example.json
├── README.md
├── templates/
│   ├── login.html
│   └── dashboard_prtg.html
└── static/
    ├── dashboard.css
    ├── dashboard.js
    └── img/
---

## CoreView Cloud Lab: modo sintético

La versión pública/laboratorio no requiere acceso a ningún PRTG real. `datos_simulados.py` expone un subconjunto compatible de la API de PRTG para que `monitor.py` trabaje contra 13 servidores y ~1000 sensores completamente sintéticos.

### 1. Crear entorno

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp servers.example.json servers.json
```

### 2. Iniciar los PRTG sintéticos

```bash
python datos_simulados.py
```

El simulador escucha por defecto en `127.0.0.1:9090` y sirve 13 instancias lógicas (`/prtg-01` ... `/prtg-13`).

### 3. Iniciar el collector

En otra terminal:

```bash
source .venv/bin/activate
python monitor.py
```

`monitor.py` generará `estado_sensores_prtg.json`. Este archivo es runtime y está excluido de Git.

### 4. Crear usuario local

```bash
source .venv/bin/activate
flask --app app create-user admin 'cambia-esta-password' --rol admon
```

### 5. Iniciar CoreView

```bash
source .venv/bin/activate
python app.py
```

Abrir `http://127.0.0.1:5067`.

### Datos y privacidad

El laboratorio usa exclusivamente nombres, direcciones de documentación y métricas sintéticas. No debe incluirse información de producción, clientes, topología, credenciales, históricos o capturas de ambientes reales.
