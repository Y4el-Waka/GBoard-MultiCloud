from __future__ import annotations
import csv
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import math
import tempfile
from collections import OrderedDict
from datetime import UTC,datetime, timedelta
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import click
import requests
from flask import (
    Flask,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from requests.adapters import HTTPAdapter, Retry
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


# -------------------- Base config --------------------
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "usuarios.db"))
DATA_FILE = Path(os.getenv("OUTPUT_PATH", BASE_DIR / "estado_sensores_prtg.json"))
PORT = int(os.getenv("PORT", "5067"))

ADMIN_ROLES = {"admin", "admon"}
DELETE_USER_ROLES = {"admon"}

HISTORY_CACHE_MAX_ENTRIES = int(os.getenv("HISTORY_CACHE_MAX_ENTRIES", "500"))
ALERT_SOUND_DIR = BASE_DIR / "static" / "uploads" / "alert_sounds"
ALERT_SOUND_MAX_BYTES = int(os.getenv("ALERT_SOUND_MAX_BYTES", str(5 * 1024 * 1024)))

SENSOR_EVENT_RETENTION_DAYS = int(os.getenv("SENSOR_EVENT_RETENTION_DAYS", "90"))
AUDIT_LOG_MAX_LIMIT = int(os.getenv("AUDIT_LOG_MAX_LIMIT", "500"))
EXPORT_LOG_WINDOWS = {12, 24, 72}


# -------------------- Config helpers --------------------
def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)

    if required and not value:
        raise RuntimeError(f"Missing env var: {name}")

    return value


def resolve_path(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def get_servers_file_path() -> Path:
    return resolve_path(os.getenv("PRTG_SERVERS_FILE", "servers.json"))


def create_http_session() -> requests.Session:
    session_http = requests.Session()

    retries = Retry(
        total=int(os.getenv("HTTP_RETRIES", "3")),
        backoff_factor=float(os.getenv("HTTP_BACKOFF", "0.5")),
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=int(os.getenv("HTTP_POOL_CONNECTIONS", "20")),
        pool_maxsize=int(os.getenv("HTTP_POOL_MAXSIZE", "50")),
    )

    session_http.mount("http://", adapter)
    session_http.mount("https://", adapter)

    return session_http


# -------------------- Roles --------------------
def normalize_role(rol: str | None) -> str:
    value = (rol or "lector").strip().lower()

    if value in {"admon", "superadmin", "super_admin"}:
        return "admon"

    if value in {"admin", "administrador"}:
        return "admin"

    return "lector"


def bool_from_payload(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "activo", "active"}

    return False


def bool_from_config(value: Any, default: bool = True) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "n", "off", "inactive", "inactivo", "disabled"}

    return default


def new_server_id() -> str:
    return f"srv_{secrets.token_hex(8)}"


# -------------------- PRTG server config --------------------
def read_servers_file() -> list[dict[str, Any]]:
    path = get_servers_file_path()

    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Archivo de servidores invalido: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("El archivo de servidores debe ser una lista JSON")

    clean_servers: list[dict[str, Any]] = []
    changed = False
    used_ids: set[str] = set()

    for item in payload:
        if not isinstance(item, dict):
            changed = True
            continue

        server = dict(item)

        if not server.get("id"):
            server["id"] = new_server_id()
            changed = True

        server_id = str(server["id"]).strip()

        if not server_id or server_id in used_ids:
            server["id"] = new_server_id()
            changed = True

        used_ids.add(str(server["id"]))

        if not server.get("type"):
            server["type"] = "prtg"
            changed = True

        if "enabled" not in server:
            server["enabled"] = True
            changed = True

        clean_servers.append(server)

    if changed:
        write_servers_file(clean_servers)

    return clean_servers

def write_servers_file(servers: list[dict[str, Any]]) -> None:
    path = get_servers_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix="servers_",
        suffix=".json",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(servers, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        os.replace(tmp_name, path)

    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def normalize_prtg_url(raw_url: str) -> str:
    url = raw_url.strip()

    if not url:
        raise ValueError("Servidor/puerto es obligatorio")

    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"

    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Servidor/puerto invalido. Usa algo como 10.0.0.5:5055")

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Solo se permite http o https")

    return url.rstrip("/")


def safe_server(server: dict[str, Any]) -> dict[str, Any]:
    auth_mode = "passhash" if server.get("passhash") else "password"

    return {
        "id": server.get("id", ""),
        "type": server.get("type", "prtg"),
        "name": server.get("name", ""),
        "url": server.get("url", ""),
        "username": server.get("username", ""),
        "enabled": bool_from_config(server.get("enabled", True), default=True),
        "auth_mode": auth_mode,
        "has_secret": bool(server.get("password") or server.get("passhash")),
    }

def load_prtg_servers(app: Flask | None = None) -> dict[str, dict[str, Any]]:
    raw = (os.getenv("PRTG_SERVERS_JSON") or "").strip()
    source = "PRTG_SERVERS_JSON"

    if not raw:
        path = get_servers_file_path()
        source = str(path)

        if path.is_file():
            raw = path.read_text(encoding="utf-8")

    if not raw:
        if app:
            app.logger.warning("No se configuraron servidores PRTG.")
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if app:
            app.logger.error("Configuracion de PRTG invalida en %s: %s", source, exc)
        return {}

    if not isinstance(data, list):
        if app:
            app.logger.error("Configuracion de PRTG invalida en %s: debe ser lista JSON", source)
        return {}

    servers: dict[str, dict[str, Any]] = {}

    for idx, server in enumerate(data, start=1):
        if not isinstance(server, dict):
            if app:
                app.logger.warning("Servidor PRTG #%s ignorado: no es objeto JSON.", idx)
            continue

        name = str(server.get("name", "")).strip()
        url = str(server.get("url", "")).strip().rstrip("/")
        username = str(server.get("username", "")).strip()
        has_secret = bool(server.get("password") or server.get("passhash"))

        if not (name and url and username and has_secret):
            if app:
                app.logger.warning("Servidor PRTG #%s ignorado por configuracion incompleta.", idx)
            continue

        servers[name] = {
            **server,
            "id": server.get("id", ""),
            "type": "prtg",
            "enabled": True,
            "name": name,
            "url": url,
            "username": username,
        }
        if not bool_from_config(server.get("enabled", True), default=True):
            continue

        server_type = str(server.get("type", "prtg")).strip().lower()
        if server_type != "prtg":
            continue

    return servers


def add_prtg_server(name: str, url: str, username: str, password: str) -> dict[str, Any]:
    if os.getenv("PRTG_SERVERS_JSON"):
        raise ValueError("Para agregar servidores desde la UI usa PRTG_SERVERS_FILE, no PRTG_SERVERS_JSON")

    normalized_url = normalize_prtg_url(url)
    parsed = urlparse(normalized_url)

    clean_name = name.strip() or parsed.netloc
    clean_username = username.strip()

    if not clean_name:
        raise ValueError("Nombre del servidor obligatorio")

    if not clean_username:
        raise ValueError("Usuario PRTG obligatorio")

    if not password:
        raise ValueError("Password PRTG obligatorio")

    servers = read_servers_file()

    for server in servers:
        if str(server.get("name", "")).strip().lower() == clean_name.lower():
            raise ValueError("Ya existe un servidor con ese nombre")

        if str(server.get("url", "")).strip().rstrip("/").lower() == normalized_url.lower():
            raise ValueError("Ya existe un servidor con esa URL/puerto")

    new_server = {
        "id": new_server_id(),
        "type": "prtg",
        "enabled": True,
        "name": clean_name,
        "url": normalized_url,
        "username": clean_username,
        "password": password,
    }

    servers.append(new_server)
    write_servers_file(servers)

    return new_server

def find_server_index_by_id(servers: list[dict[str, Any]], server_id: str) -> int | None:
    clean_id = str(server_id or "").strip()

    for index, server in enumerate(servers):
        if str(server.get("id", "")).strip() == clean_id:
            return index

    return None

def prtg_auth_payload(server: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "username": server["username"],
    }

    if server.get("passhash"):
        payload["passhash"] = server["passhash"]
    else:
        payload["password"] = server["password"]

    return payload


def get_prtg_server_or_reload(app: Flask, group: str) -> dict[str, Any] | None:
    server = app.prtg_servers.get(group)  # type: ignore[attr-defined]

    if server:
        return server

    app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
    return app.prtg_servers.get(group)  # type: ignore[attr-defined]


def prtg_pause_sensor(
    app: Flask,
    group: str,
    sensor_id: int,
    duration_minutes: int,
    pause_message: str,
) -> None:
    server = get_prtg_server_or_reload(app, group)

    if not server:
        raise LookupError(f"Grupo '{group}' no encontrado")

    duration_minutes = max(
        1,
        min(int(duration_minutes), 365 * 24 * 60),
    )

    params = {
        **prtg_auth_payload(server),
        "id": sensor_id,
        "duration": duration_minutes,
        "pausemsg": pause_message or "Pausado desde dashboard",
    }

    response = app.http.get(  # type: ignore[attr-defined]
        f"{server['url']}/api/pauseobjectfor.htm",
        params=params,
        timeout=app.config["REQUEST_TIMEOUT"],
        verify=app.config["PRTG_VERIFY_SSL"],
    )

    response.raise_for_status()


def prtg_pause_sensor_indefinitely(
    app: Flask,
    group: str,
    sensor_id: int,
    pause_message: str,
) -> None:
    server = get_prtg_server_or_reload(app, group)

    if not server:
        raise LookupError(f"Grupo '{group}' no encontrado")

    params = {
        **prtg_auth_payload(server),
        "id": sensor_id,
        "action": 0,
        "pausemsg": pause_message or "Pausado indefinidamente desde dashboard",
    }

    response = app.http.get(  # type: ignore[attr-defined]
        f"{server['url']}/api/pause.htm",
        params=params,
        timeout=app.config["REQUEST_TIMEOUT"],
        verify=app.config["PRTG_VERIFY_SSL"],
    )

    response.raise_for_status()

def parse_pause_until_minutes(value: str) -> tuple[int, str]:
    raw = str(value or "").strip()

    if not raw:
        raise ValueError("Selecciona fecha/hora para pausar hasta")

    try:
        # datetime-local del navegador llega como: 2026-05-09T18:30
        target = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Formato de fecha/hora invalido") from exc

    now = datetime.now()

    if target <= now:
        raise ValueError("La fecha/hora debe ser futura")

    seconds = (target - now).total_seconds()
    minutes = max(1, math.ceil(seconds / 60))

    return minutes, target.strftime("%Y-%m-%d %H:%M:%S")

def prtg_resume_sensor(
    app: Flask,
    group: str,
    sensor_id: int,
) -> None:
    server = get_prtg_server_or_reload(app, group)

    if not server:
        raise LookupError(f"Grupo '{group}' no encontrado")

    params = {
        **prtg_auth_payload(server),
        "id": sensor_id,
        "action": 1,
    }

    response = app.http.get(  # type: ignore[attr-defined]
        f"{server['url']}/api/pause.htm",
        params=params,
        timeout=app.config["REQUEST_TIMEOUT"],
        verify=app.config["PRTG_VERIFY_SSL"],
    )

    response.raise_for_status()

def update_prtg_server(
    server_id: str,
    name: str | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    if os.getenv("PRTG_SERVERS_JSON"):
        raise ValueError("Para editar servidores desde la UI usa PRTG_SERVERS_FILE, no PRTG_SERVERS_JSON")

    servers = read_servers_file()
    index = find_server_index_by_id(servers, server_id)

    if index is None:
        raise LookupError("Servidor no encontrado")

    current = dict(servers[index])

    next_name = str(name).strip() if name is not None else str(current.get("name", "")).strip()
    next_url = normalize_prtg_url(str(url)) if url is not None else str(current.get("url", "")).strip().rstrip("/")
    next_username = str(username).strip() if username is not None else str(current.get("username", "")).strip()

    if not next_name:
        raise ValueError("Nombre del servidor obligatorio")

    if not next_username:
        raise ValueError("Usuario PRTG obligatorio")

    for other in servers:
        if str(other.get("id", "")) == str(server_id):
            continue

        if str(other.get("name", "")).strip().lower() == next_name.lower():
            raise ValueError("Ya existe otro servidor con ese nombre")

        if str(other.get("url", "")).strip().rstrip("/").lower() == next_url.lower():
            raise ValueError("Ya existe otro servidor con esa URL/puerto")

    current["type"] = "prtg"
    current["name"] = next_name
    current["url"] = next_url
    current["username"] = next_username

    if enabled is not None:
        current["enabled"] = bool(enabled)

    if password is not None and str(password):
        current.pop("passhash", None)
        current["password"] = str(password)

    servers[index] = current
    write_servers_file(servers)

    return current


def delete_prtg_server(server_id: str) -> dict[str, Any]:
    if os.getenv("PRTG_SERVERS_JSON"):
        raise ValueError("Para borrar servidores desde la UI usa PRTG_SERVERS_FILE, no PRTG_SERVERS_JSON")

    servers = read_servers_file()
    index = find_server_index_by_id(servers, server_id)

    if index is None:
        raise LookupError("Servidor no encontrado")

    deleted = servers.pop(index)
    write_servers_file(servers)

    return deleted

# -------------------- Cache de historicos con limite --------------------
_HISTORY_CACHE: OrderedDict[str, tuple[datetime, dict[str, Any]]] = OrderedDict()
_HISTORY_CACHE_LOCK = RLock()


def cache_cleanup() -> None:
    now = datetime.utcnow()

    with _HISTORY_CACHE_LOCK:
        expired_keys = [
            key
            for key, (expires_at, _) in _HISTORY_CACHE.items()
            if expires_at < now
        ]

        for key in expired_keys:
            _HISTORY_CACHE.pop(key, None)


def cache_get(key: str) -> dict[str, Any] | None:
    now = datetime.utcnow()

    with _HISTORY_CACHE_LOCK:
        item = _HISTORY_CACHE.get(key)

        if not item:
            return None

        expires_at, payload = item

        if expires_at < now:
            _HISTORY_CACHE.pop(key, None)
            return None

        _HISTORY_CACHE.move_to_end(key)
        return payload


def cache_set(key: str, value: dict[str, Any], ttl_sec: int = 60) -> None:
    expires_at = datetime.utcnow() + timedelta(seconds=ttl_sec)

    with _HISTORY_CACHE_LOCK:
        _HISTORY_CACHE[key] = (expires_at, value)
        _HISTORY_CACHE.move_to_end(key)

        while len(_HISTORY_CACHE) > HISTORY_CACHE_MAX_ENTRIES:
            _HISTORY_CACHE.popitem(last=False)


def cache_clear() -> None:
    with _HISTORY_CACHE_LOCK:
        _HISTORY_CACHE.clear()


def cache_size() -> int:
    with _HISTORY_CACHE_LOCK:
        return len(_HISTORY_CACHE)


# -------------------- DB helpers --------------------
def conn_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with conn_db() as conn:
        # -------------------- Tabla usuarios --------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'lector',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cols = table_columns(conn, "usuarios")

        if "email" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN email TEXT")

        if "auth_code" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN auth_code TEXT")

        if "email_verified" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")

        if "is_active" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        if "updated_at" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN updated_at TEXT")

        if "last_login_at" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN last_login_at TEXT")

        if "password_changed_at" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN password_changed_at TEXT")

        if "alert_sound_enabled" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN alert_sound_enabled INTEGER NOT NULL DEFAULT 1")

        if "alert_repeat_down" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN alert_repeat_down INTEGER NOT NULL DEFAULT 0")

        if "alert_volume" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN alert_volume INTEGER NOT NULL DEFAULT 75")

        if "alert_sound_file" not in cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN alert_sound_file TEXT")

        # -------------------- Normalización de datos viejos --------------------
        conn.execute("UPDATE usuarios SET rol = 'lector' WHERE LOWER(rol) = 'lectura'")
        conn.execute("UPDATE usuarios SET rol = 'admin' WHERE LOWER(rol) = 'administrador'")
        conn.execute("UPDATE usuarios SET rol = 'admon' WHERE LOWER(rol) IN ('superadmin', 'super_admin')")

        conn.execute("UPDATE usuarios SET is_active = 1 WHERE is_active IS NULL")
        conn.execute("UPDATE usuarios SET email_verified = 0 WHERE email_verified IS NULL")
        conn.execute("UPDATE usuarios SET alert_sound_enabled = 1 WHERE alert_sound_enabled IS NULL")
        conn.execute("UPDATE usuarios SET alert_repeat_down = 0 WHERE alert_repeat_down IS NULL")
        conn.execute("UPDATE usuarios SET alert_volume = 75 WHERE alert_volume IS NULL")

        # -------------------- Índices usuarios --------------------
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON usuarios(rol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_is_active ON usuarios(is_active)")

        # -------------------- Tabla auditoría --------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                target_name TEXT,
                detail TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # -------------------- Índices auditoría --------------------
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_target_type ON audit_log(target_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_target_id ON audit_log(target_id)")

        audit_cols = table_columns(conn, "audit_log")

        if "user_agent" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN user_agent TEXT")

        if "request_method" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN request_method TEXT")

        if "request_path" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN request_path TEXT")

        # -------------------- Eventos persistentes de sensores --------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                device_name TEXT,
                sensor_name TEXT,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_events_created_at
            ON sensor_events(created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_events_sensor
            ON sensor_events(group_name, sensor_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_events_type
            ON sensor_events(event_type)
            """
        )

        # Estado más reciente, compartido con monitor.py
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_state(
                group_name TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                device_name TEXT,
                sensor_name TEXT,
                status TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_name, sensor_id)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_state_last_seen
            ON sensor_state(last_seen_at)
            """
        )

        # Incidentes activos de sensores MF y vencimiento de seis horas
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mf_alert_state(
                group_name TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                device_name TEXT,
                sensor_name TEXT,
                alert_started_at TEXT NOT NULL,
                deadline_at TEXT NOT NULL,
                last_status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_name, sensor_id)
            )
            """
        )

        # Conserva una ventana razonable sin dejar crecer la base eternamente.
        retention_cutoff = (
            datetime.now() - timedelta(days=max(1, SENSOR_EVENT_RETENTION_DAYS))
        ).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "DELETE FROM sensor_events WHERE created_at < ?",
            (retention_cutoff,),
        )

        # -------------------- Sensores fijados por usuario --------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_pinned_sensors(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, group_name, sensor_id),
                FOREIGN KEY(user_id) REFERENCES usuarios(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_pinned_sensors_user
            ON user_pinned_sensors(user_id)
            """
        )


def seed_default_users(app: Flask) -> None:
    if not env_bool("SEED_DEFAULT_USERS", False):
        return

    raw_users = os.getenv("DEFAULT_USERS_JSON", "[]")

    try:
        users = json.loads(raw_users)

        if not isinstance(users, list):
            raise ValueError("DEFAULT_USERS_JSON debe ser una lista")

        for user in users:
            add_user(
                username=user["username"],
                password=user["password"],
                rol=user.get("rol", "lector"),
                email=user.get("email"),
            )

    except Exception as exc:
        app.logger.error("Seed de usuarios invalido: %s", exc)


def active_admin_count(conn: sqlite3.Connection, exclude_user_id: int | None = None) -> int:
    params: list[Any] = []

    query = """
        SELECT COUNT(*) AS total
        FROM usuarios
        WHERE is_active = 1
          AND LOWER(rol) IN ('admin', 'admon', 'administrador', 'superadmin', 'super_admin')
    """

    if exclude_user_id is not None:
        query += " AND id <> ?"
        params.append(exclude_user_id)

    row = conn.execute(query, params).fetchone()
    return int(row["total"] if row else 0)


def add_user(
    username: str,
    password: str,
    rol: str = "lector",
    email: str | None = None,
    auth_code: str | None = None,
) -> bool:
    clean_username = username.strip()
    clean_email = (email or "").strip() or None

    if not clean_username or not password:
        raise ValueError("usuario/correo y password son obligatorios")

    with conn_db() as conn:
        if clean_email:
            duplicate_email = conn.execute(
                "SELECT 1 FROM usuarios WHERE email = ?",
                (clean_email,),
            ).fetchone()

            if duplicate_email:
                return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO usuarios(
                username,
                email,
                password,
                rol,
                auth_code,
                email_verified,
                is_active,
                updated_at,
                password_changed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_username,
                clean_email,
                generate_password_hash(password),
                normalize_role(rol),
                auth_code,
                0,
                1,
                now,
                now,
            ),
        )

        return cur.rowcount > 0


def list_users() -> list[dict[str, Any]]:
    with conn_db() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                username,
                email,
                rol,
                email_verified,
                is_active,
                created_at,
                updated_at,
                last_login_at,
                password_changed_at
            FROM usuarios
            ORDER BY is_active DESC, created_at DESC, username ASC
            """
        ).fetchall()

    users = []

    for row in rows:
        item = dict(row)
        item["rol"] = normalize_role(item.get("rol"))
        item["is_active"] = int(item.get("is_active") or 0)
        users.append(item)

    return users


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, username, email, rol, is_active
        FROM usuarios
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()


def get_user(username: str) -> sqlite3.Row | None:
    with conn_db() as conn:
        return conn.execute(
            """
            SELECT id, username, email, password, rol, is_active
            FROM usuarios
            WHERE username = ? OR email = ?
            """,
            (username, username),
        ).fetchone()


def mark_user_login(user_id: int) -> None:
    with conn_db() as conn:
        conn.execute(
            """
            UPDATE usuarios
            SET last_login_at = ?
            WHERE id = ?
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id),
        )


def update_user_role(user_id: int, new_role: str, current_user_id: int | None = None) -> bool:
    normalized_role = normalize_role(new_role)

    with conn_db() as conn:
        user = get_user_by_id(conn, user_id)

        if not user:
            return False

        if current_user_id and int(user["id"]) == int(current_user_id):
            raise ValueError("No te cambies tu propio rol, papito. Usa otro admon para eso.")

        current_role = normalize_role(user["rol"])
        is_active = int(user["is_active"] or 0) == 1

        if is_active and current_role in ADMIN_ROLES and normalized_role not in ADMIN_ROLES:
            remaining = active_admin_count(conn, exclude_user_id=user_id)

            if remaining < 1:
                raise ValueError("No puedes dejar el sistema sin admin/admon activo")

        conn.execute(
            """
            UPDATE usuarios
            SET rol = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                normalized_role,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
            ),
        )

        return True


def set_user_active(user_id: int, active: bool, current_user_id: int | None = None) -> bool:
    with conn_db() as conn:
        user = get_user_by_id(conn, user_id)

        if not user:
            return False

        if current_user_id and int(user["id"]) == int(current_user_id):
            raise ValueError("No te puedes desactivar a ti mismo, mi rey")

        target_role = normalize_role(user["rol"])
        currently_active = int(user["is_active"] or 0) == 1

        if currently_active and not active and target_role in ADMIN_ROLES:
            remaining = active_admin_count(conn, exclude_user_id=user_id)

            if remaining < 1:
                raise ValueError("No puedes desactivar el ultimo admin/admon activo")

        conn.execute(
            """
            UPDATE usuarios
            SET is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                1 if active else 0,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
            ),
        )

        return True


def reset_user_password(user_id: int, new_password: str, current_user_id: int | None = None) -> bool:
    if len(new_password) < 8:
        raise ValueError("Password temporal minimo de 8 caracteres")

    with conn_db() as conn:
        user = get_user_by_id(conn, user_id)

        if not user:
            return False

        conn.execute(
            """
            UPDATE usuarios
            SET password = ?, password_changed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                generate_password_hash(new_password),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
            ),
        )

        return True


def clamp_volume(value: Any) -> int:
    try:
        volume = int(value)
    except (TypeError, ValueError):
        volume = 75

    return max(0, min(100, volume))


def public_alert_settings(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "enabled": True,
            "repeatWhileDown": False,
            "volume": 75,
            "soundFile": None,
            "soundUrl": None,
        }

    sound_file = row["alert_sound_file"] if "alert_sound_file" in row.keys() else None

    return {
        "enabled": bool(row["alert_sound_enabled"]),
        "repeatWhileDown": bool(row["alert_repeat_down"]),
        "volume": clamp_volume(row["alert_volume"]),
        "soundFile": sound_file,
        "soundUrl": url_for("static", filename=sound_file) if sound_file else None,
    }


def get_user_alert_settings(user_id: int) -> dict[str, Any]:
    with conn_db() as conn:
        row = conn.execute(
            """
            SELECT alert_sound_enabled, alert_repeat_down, alert_volume, alert_sound_file
            FROM usuarios
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    return public_alert_settings(row)


def update_user_alert_settings(
    user_id: int,
    enabled: bool,
    repeat_down: bool,
    volume: int,
) -> dict[str, Any]:
    with conn_db() as conn:
        conn.execute(
            """
            UPDATE usuarios
            SET alert_sound_enabled = ?,
                alert_repeat_down = ?,
                alert_volume = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                1 if enabled else 0,
                1 if repeat_down else 0,
                clamp_volume(volume),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
            ),
        )

    return get_user_alert_settings(user_id)


def update_user_alert_sound_file(user_id: int, sound_file: str | None) -> dict[str, Any]:
    with conn_db() as conn:
        conn.execute(
            """
            UPDATE usuarios
            SET alert_sound_file = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                sound_file,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
            ),
        )

    return get_user_alert_settings(user_id)


def get_current_alert_sound_file(user_id: int) -> str | None:
    with conn_db() as conn:
        row = conn.execute(
            """
            SELECT alert_sound_file
            FROM usuarios
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    if not row:
        return None

    return row["alert_sound_file"]


def remove_alert_sound_file(sound_file: str | None) -> None:
    if not sound_file:
        return

    try:
        target = (BASE_DIR / "static" / sound_file).resolve()
        allowed_root = ALERT_SOUND_DIR.resolve()

        if allowed_root in target.parents and target.exists():
            target.unlink()
    except Exception:
        pass


def validate_mp3_upload(file_storage) -> None:
    filename = file_storage.filename or ""

    if not filename:
        raise ValueError("Selecciona un archivo MP3")

    if not filename.lower().endswith(".mp3"):
        raise ValueError("Solo se permiten archivos .mp3")

    if request.content_length and request.content_length > ALERT_SOUND_MAX_BYTES:
        max_mb = ALERT_SOUND_MAX_BYTES / 1024 / 1024
        raise ValueError(f"El MP3 no debe pesar mas de {max_mb:.1f} MB")


def delete_user_by_id(user_id: int, current_user_id: int | None = None) -> bool:
    with conn_db() as conn:
        user = get_user_by_id(conn, user_id)

        if not user:
            return False

        if current_user_id and int(user["id"]) == int(current_user_id):
            raise ValueError("No te puedes borrar a ti mismo, mi rey")

        target_role = normalize_role(user["rol"])
        is_active = int(user["is_active"] or 0) == 1

        if is_active and target_role in ADMIN_ROLES:
            remaining = active_admin_count(conn, exclude_user_id=user_id)

            if remaining < 1:
                raise ValueError("No puedes borrar el ultimo admin/admon activo")

        conn.execute(
            "DELETE FROM user_pinned_sensors WHERE user_id = ?",
            (user_id,),
        )
        conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))

        return True

def list_user_pins(user_id: int) -> list[dict[str, str]]:
    with conn_db() as conn:
        rows = conn.execute(
            """
            SELECT group_name, sensor_id
            FROM user_pinned_sensors
            WHERE user_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (user_id,),
        ).fetchall()

    return [
        {
            "group": str(row["group_name"]),
            "id": str(row["sensor_id"]),
        }
        for row in rows
    ]


def add_user_pin(
    user_id: int,
    group: str,
    sensor_id: str,
) -> None:
    with conn_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_pinned_sensors(
                user_id,
                group_name,
                sensor_id
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                group,
                sensor_id,
            ),
        )


def remove_user_pin(
    user_id: int,
    group: str,
    sensor_id: str,
) -> None:
    with conn_db() as conn:
        conn.execute(
            """
            DELETE FROM user_pinned_sensors
            WHERE user_id = ?
              AND group_name = ?
              AND sensor_id = ?
            """,
            (
                user_id,
                group,
                sensor_id,
            ),
        )


# -------------------- Sensor event exports --------------------
def list_sensor_event_summary(hours: int) -> list[dict[str, Any]]:
    if hours not in EXPORT_LOG_WINDOWS:
        raise ValueError("Ventana de exportacion invalida")

    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    with conn_db() as conn:
        rows = conn.execute(
            """
            SELECT
                group_name,
                sensor_id,
                device_name,
                sensor_name,
                previous_status,
                new_status,
                event_type,
                created_at
            FROM sensor_events
            WHERE created_at >= ?
            ORDER BY created_at ASC, id ASC
            """,
            (cutoff,),
        ).fetchall()

        state_rows = conn.execute(
            """
            SELECT group_name, sensor_id, status
            FROM sensor_state
            """
        ).fetchall()

    current_status = {
        (str(row["group_name"]), str(row["sensor_id"])): str(row["status"])
        for row in state_rows
    }

    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        key = (str(row["group_name"]), str(row["sensor_id"]))

        item = grouped.setdefault(
            key,
            {
                "group": str(row["group_name"]),
                "sensor_id": str(row["sensor_id"]),
                "device": str(row["device_name"] or ""),
                "sensor": str(row["sensor_name"] or ""),
                "alert_count": 0,
                "recovery_count": 0,
                "status_change_count": 0,
                "first_alert_at": "",
                "last_alert_at": "",
                "first_event_at": str(row["created_at"]),
                "last_event_at": str(row["created_at"]),
                "current_status": current_status.get(key, str(row["new_status"])),
            },
        )

        item["device"] = str(row["device_name"] or item["device"])
        item["sensor"] = str(row["sensor_name"] or item["sensor"])
        item["last_event_at"] = str(row["created_at"])
        item["status_change_count"] += 1

        event_type = str(row["event_type"] or "status_change")

        if event_type == "alert":
            item["alert_count"] += 1

            if not item["first_alert_at"]:
                item["first_alert_at"] = str(row["created_at"])

            item["last_alert_at"] = str(row["created_at"])

        elif event_type == "recovery":
            item["recovery_count"] += 1

    result = []

    for item in grouped.values():
        service_name = item["sensor"] or item["device"] or item["sensor_id"]
        alert_count = int(item["alert_count"])

        item["summary"] = (
            f"{service_name} alertado x{alert_count}"
            if alert_count
            else f"{service_name} con {item['status_change_count']} cambios"
        )
        item["intermittent"] = alert_count > 1 or int(item["recovery_count"]) > 1
        result.append(item)

    result.sort(
        key=lambda item: (
            int(item["alert_count"]),
            str(item["last_alert_at"] or item["last_event_at"]),
        ),
        reverse=True,
    )

    return result


def build_sensor_events_csv(hours: int) -> io.BytesIO:
    rows = list_sensor_event_summary(hours)
    text_buffer = io.StringIO(newline="")

    writer = csv.writer(text_buffer)
    writer.writerow(
        [
            "Resumen",
            "Servicio",
            "Dispositivo",
            "Servidor PRTG",
            "Sensor ID",
            "Alertas",
            "Recuperaciones",
            "Intermitente",
            "Primera alerta",
            "Ultima alerta",
            "Primer evento",
            "Ultimo evento",
            "Estado actual",
            "Cambios totales",
        ]
    )

    for item in rows:
        writer.writerow(
            [
                item["summary"],
                item["sensor"],
                item["device"],
                item["group"],
                item["sensor_id"],
                item["alert_count"],
                item["recovery_count"],
                "Si" if item["intermittent"] else "No",
                item["first_alert_at"],
                item["last_alert_at"],
                item["first_event_at"],
                item["last_event_at"],
                item["current_status"],
                item["status_change_count"],
            ]
        )

    binary = io.BytesIO(text_buffer.getvalue().encode("utf-8-sig"))
    binary.seek(0)
    return binary


def parse_audit_date(value: str | None, end_of_day: bool = False) -> str | None:
    raw = str(value or "").strip()

    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Fecha de auditoria invalida") from exc

    if len(raw) <= 10:
        parsed = parsed.replace(
            hour=23 if end_of_day else 0,
            minute=59 if end_of_day else 0,
            second=59 if end_of_day else 0,
        )

    return parsed.strftime("%Y-%m-%d %H:%M:%S")


# -------------------- Audit helpers --------------------
def get_client_ip() -> str:
    if not has_request_context():
        return ""

    forwarded = request.headers.get("X-Forwarded-For", "")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.remote_addr or ""


def audit_detail(detail: Any) -> str:
    if detail is None:
        return ""

    if isinstance(detail, (dict, list, tuple)):
        try:
            return json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(detail)

    return str(detail)


def log_audit(
    action: str,
    target_type: str,
    target_id: str | int | None = None,
    target_name: str | None = None,
    detail: Any = None,
    user_id: int | None = None,
    username: str | None = None,
) -> None:
    """
    Guarda una acción administrativa.
    No guardar passwords, passhash, tokens ni secretos en detail.
    """
    try:
        user_agent = ""
        request_method = ""
        request_path = ""

        if has_request_context():
            user_id = user_id if user_id is not None else session.get("user_id")
            username = username if username is not None else session.get("username", "system")
            user_agent = request.headers.get("User-Agent", "")[:500]
            request_method = request.method
            request_path = request.path
        else:
            username = username or "system"

        with conn_db() as conn:
            conn.execute(
                """
                INSERT INTO audit_log(
                    user_id,
                    username,
                    action,
                    target_type,
                    target_id,
                    target_name,
                    detail,
                    ip_address,
                    user_agent,
                    request_method,
                    request_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    action,
                    target_type,
                    str(target_id or ""),
                    target_name or "",
                    audit_detail(detail),
                    get_client_ip(),
                    user_agent,
                    request_method,
                    request_path,
                ),
            )

    except Exception as exc:
        logging.getLogger(__name__).warning("No se pudo guardar auditoria: %s", exc)


def list_audit_logs(
    limit: int = 100,
    username: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    ip_address: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), AUDIT_LOG_MAX_LIMIT))

    where = ["1 = 1"]
    params: list[Any] = []

    if username:
        where.append("username = ?")
        params.append(username)

    if action:
        where.append("action = ?")
        params.append(action)

    if target_type:
        where.append("target_type = ?")
        params.append(target_type)

    if ip_address:
        where.append("ip_address LIKE ?")
        params.append(f"%{ip_address}%")

    parsed_from = parse_audit_date(date_from)
    parsed_to = parse_audit_date(date_to, end_of_day=True)

    if parsed_from:
        where.append("created_at >= ?")
        params.append(parsed_from)

    if parsed_to:
        where.append("created_at <= ?")
        params.append(parsed_to)

    if q:
        where.append(
            """
            (
                username LIKE ?
                OR action LIKE ?
                OR target_type LIKE ?
                OR target_name LIKE ?
                OR target_id LIKE ?
                OR detail LIKE ?
                OR ip_address LIKE ?
                OR user_agent LIKE ?
                OR request_path LIKE ?
            )
            """
        )
        like = f"%{q}%"
        params.extend([like] * 9)

    params.append(limit)

    with conn_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                id,
                user_id,
                username,
                action,
                target_type,
                target_id,
                target_name,
                detail,
                ip_address,
                user_agent,
                request_method,
                request_path,
                created_at
            FROM audit_log
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def audit_filter_options() -> dict[str, list[str]]:
    with conn_db() as conn:
        usernames = [
            str(row["value"])
            for row in conn.execute(
                """
                SELECT DISTINCT username AS value
                FROM audit_log
                WHERE COALESCE(username, '') <> ''
                ORDER BY username COLLATE NOCASE
                """
            ).fetchall()
        ]

        actions = [
            str(row["value"])
            for row in conn.execute(
                """
                SELECT DISTINCT action AS value
                FROM audit_log
                WHERE COALESCE(action, '') <> ''
                ORDER BY action COLLATE NOCASE
                """
            ).fetchall()
        ]

        target_types = [
            str(row["value"])
            for row in conn.execute(
                """
                SELECT DISTINCT target_type AS value
                FROM audit_log
                WHERE COALESCE(target_type, '') <> ''
                ORDER BY target_type COLLATE NOCASE
                """
            ).fetchall()
        ]

    return {
        "usernames": usernames,
        "actions": actions,
        "target_types": target_types,
    }


def audit_user_name(user_id: int) -> str:
    try:
        with conn_db() as conn:
            row = conn.execute(
                """
                SELECT username, email
                FROM usuarios
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()

        if not row:
            return str(user_id)

        return row["username"] or row["email"] or str(user_id)

    except Exception:
        return str(user_id)

# -------------------- Auth decorators --------------------
def _auth_error_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Sesion requerida"}), 401

    return redirect(url_for("login"))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return _auth_error_response()

        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return _auth_error_response()

        if normalize_role(session.get("rol")) not in ADMIN_ROLES:
            return jsonify({"error": "no eres admin papito", "code": "admin_required"}), 403

        return view(*args, **kwargs)

    return wrapper


def admon_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return _auth_error_response()

        if normalize_role(session.get("rol")) not in DELETE_USER_ROLES:
            return jsonify({"error": "solo admon puede hacer eso papito", "code": "admon_required"}), 403

        return view(*args, **kwargs)

    return wrapper


# -------------------- CSV historico PRTG --------------------
def parse_prtg_float(value: str) -> float | None:
    if not value:
        return None

    clean = value.strip().replace("\u00a0", " ")
    match = re.search(r"[-+]?\d[\d.,]*", clean)

    if not match:
        return None

    number = match.group(0)

    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")

    elif "," in number:
        left, right = number.rsplit(",", 1)

        if len(right) == 3 and len(left) >= 1:
            number = left + right
        else:
            number = left + "." + right

    try:
        return float(number)
    except ValueError:
        return None

def parse_history_csv(text: str) -> dict[str, Any]:
    """
    Parser robusto para /api/historicdata.csv de PRTG.

    Devuelve compatibilidad actual:
    - series: serie principal
    - unit
    - count
    - column

    Y además devuelve multi-serie:
    - datasets: ping, minimum, maximum, packet_loss, downtime, coverage si existen
    """
    if not text or not text.strip():
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "Respuesta historica vacia",
        }

    lower_text = text[:500].lower()

    if "<html" in lower_text or "<!doctype" in lower_text:
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "PRTG regreso HTML en lugar de CSV. Revisa credenciales o permisos.",
        }

    sample = text[:4096]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"

    rows = list(csv.reader(io.StringIO(text.replace("\r", "")), dialect))
    rows = [
        [str(cell).strip().strip('"') for cell in row]
        for row in rows
        if any(str(cell).strip() for cell in row)
    ]

    if len(rows) < 2:
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "CSV historico sin filas suficientes",
        }

    def normalize_header(value: str) -> str:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("\ufeff", "")
            .replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ñ", "n")
        )

    def strip_raw(value: str) -> str:
        value = normalize_header(value)
        return re.sub(r"\s*\(raw\)\s*$", "", value).strip()

    time_header_names = {
        "date time",
        "datetime",
        "date/time",
        "fecha hora",
        "fecha/hora",
        "fecha",
    }

    header_idx = None

    for idx, row in enumerate(rows):
        normalized = [strip_raw(cell) for cell in row]

        if any(cell in time_header_names for cell in normalized):
            header_idx = idx
            break

    if header_idx is None:
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "No se encontro columna Date Time / Fecha Hora en CSV historico",
            "preview": rows[:5],
        }

    header = rows[header_idx]
    data_rows = rows[header_idx + 1:]

    normalized_header = [normalize_header(cell) for cell in header]
    base_header = [strip_raw(cell) for cell in header]

    time_idx = None
    time_raw_idx = None

    for idx, col in enumerate(base_header):
        raw_col = normalized_header[idx]

        if col in time_header_names and "(raw)" not in raw_col:
            time_idx = idx
            break

    for idx, col in enumerate(base_header):
        raw_col = normalized_header[idx]

        if col in time_header_names and "(raw)" in raw_col:
            time_raw_idx = idx
            break

    if time_idx is None and time_raw_idx is not None:
        time_idx = time_raw_idx

    if time_idx is None:
        time_idx = 0

    def is_time_column(idx: int) -> bool:
        col = base_header[idx] if idx < len(base_header) else ""
        raw_col = normalized_header[idx] if idx < len(normalized_header) else ""

        if col in time_header_names:
            return True

        return "fecha hora" in raw_col or "date time" in raw_col

    def parse_excel_serial_date(value: str) -> datetime | None:
        try:
            serial = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

        return datetime(1899, 12, 30) + timedelta(days=serial)

    date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d-%H-%M-%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %I:%M:%S %p",
    ]

    def parse_prtg_time(row: list[str]) -> datetime | None:
        raw_time = ""

        if time_idx is not None and time_idx < len(row):
            raw_time = str(row[time_idx]).strip().strip('"')

        # Ejemplo: 06.05.2026 03:35:00 - 03:40:00
        if " - " in raw_time:
            raw_time = raw_time.split(" - ", 1)[0].strip()

        for fmt in date_formats:
            try:
                return datetime.strptime(raw_time, fmt)
            except ValueError:
                pass

        try:
            return datetime.fromisoformat(raw_time.replace("Z", ""))
        except ValueError:
            pass

        if time_raw_idx is not None and time_raw_idx < len(row):
            parsed_raw = parse_excel_serial_date(row[time_raw_idx])

            if parsed_raw:
                return parsed_raw

        return None

    def has_numeric_values(idx: int) -> bool:
        return any(
            idx < len(row) and parse_prtg_float(row[idx]) is not None
            for row in data_rows[:80]
        )

    def classify_column(base_name: str) -> tuple[str, str, str, int] | None:
        """
        Regresa:
        key, label, unit, priority
        """
        name = strip_raw(base_name)

        if name in {"coverage", "cobertura"}:
            return ("coverage", "Cobertura", "%", 60)

        if any(word in name for word in ["tiempo de ping", "ping time", "ping"]):
            return ("ping", "Tiempo de Ping", "ms", 10)

        if any(word in name for word in ["minimo", "minimum"]):
            return ("minimum", "Mínimo", "ms", 20)

        if any(word in name for word in ["maximo", "maximum"]):
            return ("maximum", "Máximo", "ms", 30)

        if any(word in name for word in ["perdida de paquetes", "packet loss", "packetloss"]):
            return ("packet_loss", "Pérdida de paquetes", "%", 40)

        if any(word in name for word in ["tiempo de inactividad", "downtime", "inactividad"]):
            return ("downtime", "Tiempo de inactividad", "%", 50)

        return None

    candidate_columns: list[dict[str, Any]] = []

    for idx, colname in enumerate(header):
        if idx == time_idx or idx == time_raw_idx:
            continue

        if is_time_column(idx):
            continue

        normalized = normalized_header[idx]
        classified = classify_column(colname)

        if not classified:
            continue

        if not has_numeric_values(idx):
            continue

        key, label, unit, priority = classified

        # Preferimos RAW para graficar, pero si solo existe la columna normal también sirve.
        raw_bonus = -1 if "(raw)" in normalized else 0

        candidate_columns.append(
            {
                "idx": idx,
                "key": key,
                "label": label,
                "unit": unit,
                "priority": priority + raw_bonus,
                "column": colname,
                "is_raw": "(raw)" in normalized,
            }
        )

    # Si una misma métrica aparece normal y RAW, nos quedamos con RAW.
    selected_by_key: dict[str, dict[str, Any]] = {}

    for candidate in sorted(candidate_columns, key=lambda item: item["priority"]):
        key = candidate["key"]

        if key not in selected_by_key:
            selected_by_key[key] = candidate
            continue

        current = selected_by_key[key]

        if candidate["is_raw"] and not current["is_raw"]:
            selected_by_key[key] = candidate

    selected_columns = sorted(
        selected_by_key.values(),
        key=lambda item: item["priority"],
    )

    if not selected_columns:
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "No se encontraron columnas historicas utiles para graficar.",
            "header": header,
            "preview": rows[:5],
        }

    datasets: list[dict[str, Any]] = []

    for column in selected_columns:
        points: list[dict[str, Any]] = []
        idx = column["idx"]

        for row in data_rows:
            if len(row) <= idx:
                continue

            parsed_time = parse_prtg_time(row)
            value = parse_prtg_float(str(row[idx]))

            if parsed_time is None or value is None:
                continue

            points.append(
                {
                    "t": parsed_time.isoformat(),
                    "value": value,
                }
            )

        if not points:
            continue

        axis = "y1" if column["unit"] == "%" else "y"

        datasets.append(
            {
                "key": column["key"],
                "label": column["label"],
                "unit": column["unit"],
                "axis": axis,
                "column": column["column"],
                "data": points,
            }
        )

    if not datasets:
        return {
            "series": [],
            "datasets": [],
            "unit": "",
            "count": 0,
            "error": "Las columnas historicas existen, pero no se pudieron convertir puntos.",
            "header": header,
            "preview": rows[:5],
        }

    primary_priority = ["ping", "minimum", "maximum", "packet_loss", "downtime", "coverage"]

    primary_dataset = datasets[0]

    for key in primary_priority:
        match = next((item for item in datasets if item["key"] == key), None)

        if match:
            primary_dataset = match
            break

    return {
        "series": primary_dataset["data"],
        "datasets": datasets,
        "unit": primary_dataset["unit"],
        "count": len(primary_dataset["data"]),
        "column": primary_dataset["column"],
        "header": header,
        "error": "",
    }

# -------------------- App factory --------------------
def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    app.config.update(
        SECRET_KEY=get_env("APP_SECRET_KEY", os.getenv("SECRET_KEY", "dev-insecure-change-me")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", False),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.getenv("SESSION_HOURS", "8"))),
        PRTG_VERIFY_SSL=env_bool("PRTG_VERIFY_SSL", False),
        HISTORY_CACHE_TTL=int(os.getenv("HISTORY_CACHE_TTL", "60")),
        REQUEST_TIMEOUT=int(os.getenv("REQUEST_TIMEOUT", "20")),
    )

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    init_db()
    seed_default_users(app)

    app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
    app.http = create_http_session()  # type: ignore[attr-defined]

    @app.cli.command("audit-list")
    @click.option("--limit", default=20, show_default=True)
    @click.option("--q", default=None)
    def audit_list_command(limit: int, q: str | None):
        logs = list_audit_logs(limit=limit, q=q)

        if not logs:
            click.echo("Sin eventos de auditoria.")
            return

        for item in logs:
            click.echo(
                f"[{item['created_at']}] "
                f"{item['username'] or 'system'} "
                f"{item['action']} "
                f"{item['target_type']}:{item['target_name'] or item['target_id']} "
                f"ip={item['ip_address'] or '-'}"
            )

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.tailwindcss.com cdn.jsdelivr.net cdnjs.cloudflare.com code.jquery.com; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; "
            "font-src 'self' fonts.gstatic.com cdnjs.cloudflare.com data:; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'"
        )

        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"

        return resp

    @app.route("/", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            user = get_user(username)

            if user and check_password_hash(user["password"], password):
                if int(user["is_active"] or 0) != 1:
                    log_audit(
                        action="auth.login_failed",
                        target_type="session",
                        target_id=user["id"],
                        target_name=user["username"],
                        detail={"reason": "inactive_user"},
                        user_id=int(user["id"]),
                        username=str(user["username"]),
                    )

                    return render_template(
                        "login.html",
                        error="Tu usuario esta inactivo, habla con el patron papito",
                    ), 403

                session.clear()
                session.permanent = True

                session["logged_in"] = True
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["email"] = user["email"]
                session["rol"] = normalize_role(user["rol"])

                mark_user_login(int(user["id"]))

                log_audit(
                    action="auth.login_success",
                    target_type="session",
                    target_id=user["id"],
                    target_name=user["username"],
                    detail={
                        "role": normalize_role(user["rol"]),
                        "email": user["email"] or "",
                    },
                    user_id=int(user["id"]),
                    username=str(user["username"]),
                )

                return redirect(url_for("dashboard"))

            log_audit(
                action="auth.login_failed",
                target_type="session",
                target_name=username or "sin_usuario",
                detail={"reason": "invalid_credentials"},
                username=username or "anonymous",
            )

            return render_template("login.html", error="Credenciales incorrectas"), 401

        return render_template("login.html")


    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template(
            "dashboard_prtg.html",
            usuario=session.get("username", ""),
            rol=session.get("rol", "lector"),
        )

    @app.route("/api/me")
    @login_required
    def api_me():
        return jsonify(
            {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "rol": session.get("rol", "lector"),
            }
        )

    @app.route("/api/me/pins", methods=["GET", "POST", "DELETE"])
    @login_required
    def api_me_pins():
        user_id = session.get("user_id")

        if not user_id:
            return jsonify({"error": "Sesion sin usuario valido"}), 400

        user_id = int(user_id)

        if request.method == "GET":
            return jsonify(
                {
                    "pins": list_user_pins(user_id),
                }
            )

        payload = request.get_json(silent=True) or {}

        group = str(payload.get("group", "")).strip()
        sensor_id = str(payload.get("id", "")).strip()
        sensor_name = str(payload.get("sensor_name", "")).strip()
        device_name = str(payload.get("device_name", "")).strip()

        if not group or not sensor_id:
            return jsonify({"error": "group e id son obligatorios"}), 400

        if request.method == "POST":
            add_user_pin(
                user_id=user_id,
                group=group,
                sensor_id=sensor_id,
            )

            log_audit(
                action="sensor.pin",
                target_type="sensor",
                target_id=sensor_id,
                target_name=sensor_name or sensor_id,
                detail={
                    "group": group,
                    "device": device_name,
                    "sensor": sensor_name,
                },
            )

            return jsonify(
                {
                    "ok": True,
                    "pinned": True,
                    "message": "Sensor agregado a fijados",
                }
            )

        remove_user_pin(
            user_id=user_id,
            group=group,
            sensor_id=sensor_id,
        )

        log_audit(
            action="sensor.unpin",
            target_type="sensor",
            target_id=sensor_id,
            target_name=sensor_name or sensor_id,
            detail={
                "group": group,
                "device": device_name,
                "sensor": sensor_name,
            },
        )

        return jsonify(
            {
                "ok": True,
                "pinned": False,
                "message": "Sensor retirado de fijados",
            }
        )

    @app.route("/api/me/alert-settings", methods=["GET", "PUT"])
    @login_required
    def api_me_alert_settings():
        user_id = session.get("user_id")

        if not user_id:
            return jsonify({"error": "Sesion sin usuario valido"}), 400

        if request.method == "GET":
            return jsonify({"settings": get_user_alert_settings(int(user_id))})

        payload = request.get_json(silent=True) or {}

        settings = update_user_alert_settings(
            user_id=int(user_id),
            enabled=bool_from_payload(payload.get("enabled", True)),
            repeat_down=bool_from_payload(payload.get("repeatWhileDown", False)),
            volume=clamp_volume(payload.get("volume", 75)),
        )

        log_audit(
            action="alert.settings_update",
            target_type="alert",
            target_id=user_id,
            target_name=session.get("username", ""),
            detail={
                "enabled": settings.get("enabled"),
                "repeatWhileDown": settings.get("repeatWhileDown"),
                "volume": settings.get("volume"),
            },
        )

        return jsonify({"ok": True, "settings": settings})


    @app.route("/api/me/alert-sound", methods=["POST", "DELETE"])
    @login_required
    def api_me_alert_sound():
        user_id = session.get("user_id")

        if not user_id:
            return jsonify({"error": "Sesion sin usuario valido"}), 400

        user_id = int(user_id)

        if request.method == "DELETE":
            old_file = get_current_alert_sound_file(user_id)
            remove_alert_sound_file(old_file)
            settings = update_user_alert_sound_file(user_id, None)

            log_audit(
                action="alert.sound_reset",
                target_type="alert",
                target_id=user_id,
                target_name=session.get("username", ""),
                detail="Sonido personalizado eliminado",
            )

            return jsonify(
                {
                    "ok": True,
                    "message": "Sonido personalizado eliminado. Se usara el default.",
                    "settings": settings,
                }
            )

        uploaded = request.files.get("sound")

        if not uploaded:
            return jsonify({"error": "No mandaste archivo"}), 400

        try:
            validate_mp3_upload(uploaded)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        ALERT_SOUND_DIR.mkdir(parents=True, exist_ok=True)

        original_name = secure_filename(uploaded.filename or "alert.mp3")
        _, ext = os.path.splitext(original_name)

        filename = f"user_{user_id}_{secrets.token_hex(8)}{ext.lower()}"
        target = ALERT_SOUND_DIR / filename

        old_file = get_current_alert_sound_file(user_id)

        uploaded.save(target)

        sound_file = f"uploads/alert_sounds/{filename}"
        settings = update_user_alert_sound_file(user_id, sound_file)

        remove_alert_sound_file(old_file)
        log_audit(
            action="alert.sound_upload",
            target_type="alert",
            target_id=user_id,
            target_name=session.get("username", ""),
            detail={
                "sound_file": sound_file,
            },
        )

        return jsonify(
            {
                "ok": True,
                "message": "MP3 de alerta actualizado",
                "settings": settings,
            }
        ), 201

    @app.route("/api/sensors")
    @login_required
    def api_sensors():
        if not DATA_FILE.exists() or DATA_FILE.stat().st_size < 5:
            return jsonify(
                {
                    "sensores": [],
                    "logs": {
                        "last_up": [],
                        "last_down": [],
                    },
                    "ultima_actualizacion": None,
                }
            ), 503

        return send_file(
            DATA_FILE,
            mimetype="application/json",
            max_age=0,
            conditional=False,
        )

    @app.route("/api/logs/export")
    @login_required
    def api_export_sensor_logs():
        hours = request.args.get("hours", type=int)

        if hours not in EXPORT_LOG_WINDOWS:
            return jsonify({"error": "hours debe ser 12, 24 o 72"}), 400

        try:
            report = build_sensor_events_csv(hours)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        filename = (
            f"logs_sensores_{hours}h_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )

        log_audit(
            action="logs.export",
            target_type="sensor_logs",
            target_id=str(hours),
            target_name=f"Ultimas {hours} horas",
            detail={
                "hours": hours,
                "format": "csv",
            },
        )

        return send_file(
            report,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )

    @app.route("/api/history")
    @login_required
    def api_history():
        group = request.args.get("group", "").strip()
        sensor_id = request.args.get("id", type=int)
        minutes = min(max(request.args.get("minutes", default=120, type=int), 5), 24 * 60)
        avg = min(max(request.args.get("avg", default=300, type=int), 60), 3600)

        if not group or not sensor_id:
            return jsonify({"error": "group e id son obligatorios"}), 400

        server = app.prtg_servers.get(group)  # type: ignore[attr-defined]

        if not server:
            app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
            server = app.prtg_servers.get(group)  # type: ignore[attr-defined]

        if not server:
            return jsonify({"error": f"Grupo '{group}' no encontrado"}), 404

        key = f"{group}:{sensor_id}:{minutes}:{avg}"
        cached = cache_get(key)

        if cached:
            return jsonify(cached)

        end = datetime.now()
        start = end - timedelta(minutes=minutes)

        params = {
            "id": sensor_id,
            "sdate": start.strftime("%Y-%m-%d-%H-%M-%S"),
            "edate": end.strftime("%Y-%m-%d-%H-%M-%S"),
            "avg": avg,
            "username": server["username"],
            "usecaption": 1,
        }

        if server.get("passhash"):
            params["passhash"] = server["passhash"]
        else:
            params["password"] = server["password"]

        url = f"{server['url']}/api/historicdata.csv"

        try:
            response = app.http.get(  # type: ignore[attr-defined]
                url,
                params=params,
                timeout=app.config["REQUEST_TIMEOUT"],
                verify=app.config["PRTG_VERIFY_SSL"],
            )

            response.raise_for_status()

            payload = parse_history_csv(response.text)

            if not payload.get("series"):
                app.logger.warning(
                    "Historico sin puntos group=%s sensor=%s avg=%s status=%s error=%s preview=%s",
                    group,
                    sensor_id,
                    avg,
                    response.status_code,
                    payload.get("error"),
                    response.text[:300],
                )

            cache_set(key, payload, ttl_sec=app.config["HISTORY_CACHE_TTL"])

            return jsonify(payload)

        except requests.RequestException as exc:
            app.logger.warning(
                "Error consultando historico PRTG %s/%s: %s",
                group,
                sensor_id,
                exc,
            )

            return jsonify({"error": "No se pudo consultar PRTG"}), 502


    @app.route("/api/sensors/pause", methods=["POST"])
    @login_required
    def api_pause_sensor():
        payload = request.get_json(silent=True) or {}

        group = str(payload.get("group", "")).strip()
        sensor_id = payload.get("id")
        duration_minutes = payload.get("duration_minutes")
        pause_until = str(payload.get("pause_until", "")).strip()
        indefinite = bool_from_payload(payload.get("indefinite", False))

        sensor_name = str(payload.get("sensor_name", "")).strip()
        device_name = str(payload.get("device_name", "")).strip()
        message = str(payload.get("message", "")).strip()

        try:
            sensor_id = int(sensor_id)
        except (TypeError, ValueError):
            return jsonify({"error": "id debe ser numerico"}), 400

        if not group or not sensor_id:
            return jsonify({"error": "group e id son obligatorios"}), 400

        if not message:
            message = f"Pausado desde dashboard por {session.get('username', 'usuario')}"

        try:
            mode = "duration"
            until_label = ""

            if indefinite:
                mode = "indefinite"

                prtg_pause_sensor_indefinitely(
                    app=app,
                    group=group,
                    sensor_id=sensor_id,
                    pause_message=message,
                )

                response_message = "Sensor pausado indefinidamente"

            else:
                if pause_until:
                    duration_minutes, until_label = parse_pause_until_minutes(pause_until)
                    mode = "until"
                else:
                    try:
                        duration_minutes = int(duration_minutes or 60)
                    except (TypeError, ValueError):
                        return jsonify({"error": "duration_minutes debe ser numerico"}), 400

                duration_minutes = max(1, min(int(duration_minutes), 365 * 24 * 60))

                prtg_pause_sensor(
                    app=app,
                    group=group,
                    sensor_id=sensor_id,
                    duration_minutes=duration_minutes,
                    pause_message=message,
                )

                response_message = (
                    f"Sensor pausado hasta {until_label}"
                    if until_label
                    else f"Sensor pausado por {duration_minutes} minutos"
                )

            log_audit(
                action="sensor.pause",
                target_type="sensor",
                target_id=sensor_id,
                target_name=sensor_name or str(sensor_id),
                detail={
                    "group": group,
                    "device": device_name,
                    "sensor": sensor_name,
                    "mode": mode,
                    "duration_minutes": duration_minutes if not indefinite else None,
                    "pause_until": until_label,
                    "indefinite": indefinite,
                    "message": message,
                },
            )

            cache_clear()

            return jsonify(
                {
                    "ok": True,
                    "message": response_message,
                }
            )

        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        except requests.RequestException as exc:
            app.logger.warning("Error pausando sensor PRTG %s/%s: %s", group, sensor_id, exc)
            return jsonify({"error": "No se pudo pausar el sensor en PRTG"}), 502

    @app.route("/api/sensors/resume", methods=["POST"])
    @login_required
    def api_resume_sensor():
        payload = request.get_json(silent=True) or {}

        group = str(payload.get("group", "")).strip()
        sensor_id = payload.get("id")
        sensor_name = str(payload.get("sensor_name", "")).strip()
        device_name = str(payload.get("device_name", "")).strip()

        try:
            sensor_id = int(sensor_id)
        except (TypeError, ValueError):
            return jsonify({"error": "id debe ser numerico"}), 400

        if not group or not sensor_id:
            return jsonify({"error": "group e id son obligatorios"}), 400

        try:
            prtg_resume_sensor(
                app=app,
                group=group,
                sensor_id=sensor_id,
            )

            log_audit(
                action="sensor.resume",
                target_type="sensor",
                target_id=sensor_id,
                target_name=sensor_name or str(sensor_id),
                detail={
                    "group": group,
                    "device": device_name,
                    "sensor": sensor_name,
                },
            )

            cache_clear()

            return jsonify(
                {
                    "ok": True,
                    "message": "Sensor reanudado",
                }
            )

        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

        except requests.RequestException as exc:
            app.logger.warning("Error reanudando sensor PRTG %s/%s: %s", group, sensor_id, exc)
            return jsonify({"error": "No se pudo reanudar el sensor en PRTG"}), 502


    @app.route("/api/admin/audit", methods=["GET"])
    @admin_required
    def api_admin_audit():
        try:
            limit = request.args.get("limit", default=100, type=int)
            logs = list_audit_logs(
                limit=limit,
                username=request.args.get("username", "").strip() or None,
                action=request.args.get("action", "").strip() or None,
                target_type=request.args.get("target_type", "").strip() or None,
                ip_address=request.args.get("ip", "").strip() or None,
                date_from=request.args.get("date_from", "").strip() or None,
                date_to=request.args.get("date_to", "").strip() or None,
                q=request.args.get("q", "").strip() or None,
            )

            return jsonify(
                {
                    "logs": logs,
                    "filters": audit_filter_options(),
                    "count": len(logs),
                }
            )

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/admin/servers", methods=["GET", "POST"])
    @admin_required
    def api_admin_servers():
        if request.method == "GET":
            if os.getenv("PRTG_SERVERS_JSON"):
                servers = [safe_server(server) for server in app.prtg_servers.values()]  # type: ignore[attr-defined]

                return jsonify(
                    {
                        "servers": servers,
                        "readonly": True,
                        "message": "Config actual viene de PRTG_SERVERS_JSON",
                    }
                )

            servers = [safe_server(server) for server in read_servers_file()]
            return jsonify({"servers": servers, "readonly": False})

        payload = request.get_json(silent=True) or {}

        try:
            new_server = add_prtg_server(
                name=str(payload.get("name", "")),
                url=str(payload.get("url", "")),
                username=str(payload.get("username", "")),
                password=str(payload.get("password", "")),
            )

            app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
            cache_clear()
            log_audit(
                action="server.create",
                target_type="server",
                target_id=new_server.get("id"),
                target_name=new_server.get("name"),
                detail={
                    "type": new_server.get("type", "prtg"),
                    "url": new_server.get("url"),
                    "username": new_server.get("username"),
                    "enabled": new_server.get("enabled", True),
                },
            )

            return jsonify({"ok": True, "server": safe_server(new_server)}), 201

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


    @app.route("/api/admin/servers/<server_id>", methods=["PATCH"])
    @admin_required
    def api_admin_update_server(server_id: str):
        payload = request.get_json(silent=True) or {}

        try:
            updated = update_prtg_server(
                server_id=server_id,
                name=payload.get("name") if "name" in payload else None,
                url=payload.get("url") if "url" in payload else None,
                username=payload.get("username") if "username" in payload else None,
                password=payload.get("password") if "password" in payload else None,
                enabled=bool_from_payload(payload.get("enabled")) if "enabled" in payload else None,
            )

            app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
            cache_clear()
            log_audit(
                action="server.update",
                target_type="server",
                target_id=server_id,
                target_name=updated.get("name"),
                detail={
                    "changed_fields": list(payload.keys()),
                    "enabled": updated.get("enabled", True),
                    "url": updated.get("url"),
                    "username": updated.get("username"),
                },
            )

            return jsonify(
                {
                    "ok": True,
                    "message": "Servidor actualizado",
                    "server": safe_server(updated),
                }
            )

        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


    @app.route("/api/admin/servers/<server_id>", methods=["DELETE"])
    @admon_required
    def api_admin_delete_server(server_id: str):
        try:
            deleted = delete_prtg_server(server_id)

            app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
            cache_clear()
            log_audit(
                action="server.delete",
                target_type="server",
                target_id=server_id,
                target_name=deleted.get("name"),
                detail={
                    "type": deleted.get("type", "prtg"),
                    "url": deleted.get("url"),
                    "username": deleted.get("username"),
                },
            )

            return jsonify(
                {
                    "ok": True,
                    "message": "Servidor eliminado definitivamente",
                    "server": safe_server(deleted),
                }
            )

        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/admin/users", methods=["GET", "POST"])
    @admin_required
    def api_admin_users():
        if request.method == "GET":
            return jsonify(
                {
                    "users": list_users(),
                    "email_code_standby": True,
                }
            )

        payload = request.get_json(silent=True) or {}

        email = str(payload.get("email", "")).strip()
        username = str(payload.get("username", "")).strip() or email
        password = str(payload.get("password", ""))
        rol = normalize_role(str(payload.get("rol", "lector")))

        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return jsonify({"error": "Correo invalido"}), 400

        if not username:
            return jsonify({"error": "Usuario o correo obligatorio"}), 400

        if len(password) < 8:
            return jsonify({"error": "Password temporal minimo de 8 caracteres"}), 400

        auth_code = secrets.token_urlsafe(16)

        created = add_user(
            username=username,
            email=email,
            password=password,
            rol=rol,
            auth_code=auth_code,
        )

        if not created:
            return jsonify({"error": "El usuario/correo ya existe"}), 409

        log_audit(
            action="user.create",
            target_type="user",
            target_name=username,
            detail={
                "email": email,
                "rol": rol,
            },
        )

        return jsonify(
            {
                "ok": True,
                "email_code_standby": True,
                "message": "Usuario creado. Envio de codigo por correo pendiente.",
            }
        ), 201

    @app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
    @admon_required
    def api_admin_update_user(user_id: int):
        payload = request.get_json(silent=True) or {}
        current_user_id = session.get("user_id")

        changed: list[str] = []

        try:
            if "rol" in payload:
                updated = update_user_role(
                    user_id=user_id,
                    new_role=str(payload.get("rol", "lector")),
                    current_user_id=current_user_id,
                )

                if not updated:
                    return jsonify({"error": "Usuario no encontrado"}), 404

                changed.append("rol")

            if "is_active" in payload:
                updated = set_user_active(
                    user_id=user_id,
                    active=bool_from_payload(payload.get("is_active")),
                    current_user_id=current_user_id,
                )

                if not updated:
                    return jsonify({"error": "Usuario no encontrado"}), 404



                changed.append("estado")

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not changed:
            return jsonify({"error": "No mandaste cambios"}), 400

        log_audit(
            action="user.update",
            target_type="user",
            target_id=user_id,
            target_name=audit_user_name(user_id),
            detail={
                "changed": changed,
                "payload": {
                    key: value
                    for key, value in payload.items()
                    if key in {"rol", "is_active"}
                },
            },
        )

        return jsonify(
            {
                "ok": True,
                "message": f"Usuario actualizado: {', '.join(changed)}",
            }
        )

    @app.route("/api/admin/users/<int:user_id>/reset-password", methods=["POST"])
    @admon_required
    def api_admin_reset_user_password(user_id: int):
        payload = request.get_json(silent=True) or {}
        new_password = str(payload.get("password", ""))

        try:
            updated = reset_user_password(
                user_id=user_id,
                new_password=new_password,
                current_user_id=session.get("user_id"),
            )

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not updated:
            return jsonify({"error": "Usuario no encontrado"}), 404
        log_audit(
            action="user.reset_password",
            target_type="user",
            target_id=user_id,
            target_name=audit_user_name(user_id),
            detail="Password temporal actualizado",
        )

        return jsonify(
            {
                "ok": True,
                "message": "Password temporal actualizado",
            }
        )

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    @admon_required
    def api_admin_delete_user(user_id: int):
        try:
            deleted = delete_user_by_id(
                user_id=user_id,
                current_user_id=session.get("user_id"),
            )

        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not deleted:
            return jsonify({"error": "Usuario no encontrado"}), 404
        log_audit(
            action="user.delete",
            target_type="user",
            target_id=user_id,
            target_name=str(user_id),
            detail="Usuario eliminado definitivamente",
        )

        return jsonify(
            {
                "ok": True,
                "message": "Usuario eliminado",
            }
        )

    @app.route("/estado_sensores_prtg.json")
    @login_required
    def legacy_sensors():
        return api_sensors()

    @app.route("/healthz")
    def healthz():
        cache_cleanup()

        data_file_size = DATA_FILE.stat().st_size if DATA_FILE.exists() else 0
        data_file_mtime = datetime.fromtimestamp(DATA_FILE.stat().st_mtime).isoformat() if DATA_FILE.exists() else None

        return jsonify(
            {
                "ok": True,
                "data_file_exists": DATA_FILE.exists(),
                "data_file_size_bytes": data_file_size,
                "data_file_mtime": data_file_mtime,
                "servers": len(app.prtg_servers),  # type: ignore[attr-defined]
                "history_cache_entries": cache_size(),
                "history_cache_max_entries": HISTORY_CACHE_MAX_ENTRIES,
            }
        )

    @app.route("/logout")
    def logout():
        if session.get("logged_in"):
            log_audit(
                action="auth.logout",
                target_type="session",
                target_id=session.get("user_id"),
                target_name=session.get("username", ""),
                detail={
                    "role": session.get("rol", "lector"),
                },
            )

        session.clear()
        return redirect(url_for("login"))

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo(f"Base de datos lista: {DB_PATH}")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("password")
    @click.option("--rol", default="admin", show_default=True)
    @click.option("--email", default=None)
    def create_user_command(username: str, password: str, rol: str, email: str | None):
        created = add_user(
            username=username,
            password=password,
            rol=rol,
            email=email,
        )

        click.echo("Usuario creado." if created else "El usuario ya existia; no se modifico.")

    @app.cli.command("set-role")
    @click.argument("username")
    @click.argument("rol")
    def set_role_command(username: str, rol: str):
        normalized = normalize_role(rol)

        with conn_db() as conn:
            cur = conn.execute(
                """
                UPDATE usuarios
                SET rol = ?, updated_at = ?
                WHERE username = ? OR email = ?
                """,
                (
                    normalized,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    username,
                    username,
                ),
            )

        click.echo(
            f"Rol actualizado a {normalized}."
            if cur.rowcount
            else "Usuario no encontrado."
        )

    @app.cli.command("activate-user")
    @click.argument("username")
    def activate_user_command(username: str):
        with conn_db() as conn:
            cur = conn.execute(
                """
                UPDATE usuarios
                SET is_active = 1, updated_at = ?
                WHERE username = ? OR email = ?
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    username,
                    username,
                ),
            )

        click.echo("Usuario activado." if cur.rowcount else "Usuario no encontrado.")

    @app.cli.command("deactivate-user")
    @click.argument("username")
    def deactivate_user_command(username: str):
        with conn_db() as conn:
            user = conn.execute(
                """
                SELECT id, rol, is_active
                FROM usuarios
                WHERE username = ? OR email = ?
                """,
                (username, username),
            ).fetchone()

            if not user:
                click.echo("Usuario no encontrado.")
                return

            if int(user["is_active"] or 0) == 1 and normalize_role(user["rol"]) in ADMIN_ROLES:
                remaining = active_admin_count(conn, exclude_user_id=int(user["id"]))

                if remaining < 1:
                    click.echo("No puedes desactivar el ultimo admin/admon activo.")
                    return

            conn.execute(
                """
                UPDATE usuarios
                SET is_active = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    int(user["id"]),
                ),
            )

        click.echo("Usuario desactivado.")

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.argument("password")
    def reset_password_command(username: str, password: str):
        if len(password) < 8:
            click.echo("Password minimo de 8 caracteres.")
            return

        with conn_db() as conn:
            cur = conn.execute(
                """
                UPDATE usuarios
                SET password = ?, password_changed_at = ?, updated_at = ?
                WHERE username = ? OR email = ?
                """,
                (
                    generate_password_hash(password),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    username,
                    username,
                ),
            )

        click.echo("Password actualizado." if cur.rowcount else "Usuario no encontrado.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=env_bool("FLASK_DEBUG", False),
    )