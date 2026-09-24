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
from datetime import UTC,datetime, timedelta
from functools import wraps
from typing import Any
from urllib.parse import urlparse

import click
from config import (
    BASE_DIR,
    DEFAULT_APP_SECRET_KEY,
    load_app_prtg_servers,
    load_app_settings,
    load_environment,
    new_server_id,
    parse_config_bool as bool_from_config,
    read_servers_file,
    servers_json_is_configured,
    write_servers_file,
)
from database import ensure_monitoring_schema
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
from history_service import (
    HistoricalMetricsSource,
    HistorySensorNotFoundError,
    HistoryServerNotFoundError,
    HistoryService,
    HistoryUpstreamError,
    HistoryValidationError,
)
from metrics.serialization import serialize_normalized_history
from prtg_client import PRTGClient
from sensor_control_service import (
    SensorControlService,
    SensorControlUpstreamError,
    SensorControlValidationError,
    SensorServerNotFoundError,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_environment()
APP_SETTINGS = load_app_settings()
SHARED_SETTINGS = APP_SETTINGS.shared


# -------------------- Base config --------------------
DB_PATH = SHARED_SETTINGS.db_path
DATA_FILE = SHARED_SETTINGS.output_path
PORT = APP_SETTINGS.port

ADMIN_ROLES = {"admin", "admon"}
DELETE_USER_ROLES = {"admon"}

ALERT_SOUND_DIR = APP_SETTINGS.alert_sound_dir
ALERT_SOUND_MAX_BYTES = APP_SETTINGS.alert_sound_max_bytes

SENSOR_EVENT_RETENTION_DAYS = SHARED_SETTINGS.event_retention_days
AUDIT_LOG_MAX_LIMIT = APP_SETTINGS.audit_log_max_limit
EXPORT_LOG_WINDOWS = {12, 24, 72}
METRICS_API_V2_MAX_POINTS = 1000



def new_prtg_client(app: Flask) -> PRTGClient:
    return PRTGClient(
        timeout=app.config["REQUEST_TIMEOUT"],
        verify_ssl=app.config["PRTG_VERIFY_SSL"],
        http_settings=SHARED_SETTINGS.http,
        logger=app.logger,
    )


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


def _parse_aware_iso8601(value: str | None, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} debe ser ISO-8601")
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field_name} debe ser ISO-8601 valido") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} debe incluir timezone")
    return parsed.astimezone(UTC)


# -------------------- PRTG server config --------------------
def normalize_prtg_url(raw_url: str) -> str:
    url = raw_url.strip()

    if not url:
        raise ValueError("Servidor/puerto es obligatorio")

    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"

    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Servidor/puerto invalido. Usa algo como 192.0.2.5:5055")

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
    logger = app.logger if app else None
    return load_app_prtg_servers(logger)


def add_prtg_server(name: str, url: str, username: str, password: str) -> dict[str, Any]:
    if servers_json_is_configured():
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

def update_prtg_server(
    server_id: str,
    name: str | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    if servers_json_is_configured():
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
    if servers_json_is_configured():
        raise ValueError("Para borrar servidores desde la UI usa PRTG_SERVERS_FILE, no PRTG_SERVERS_JSON")

    servers = read_servers_file()
    index = find_server_index_by_id(servers, server_id)

    if index is None:
        raise LookupError("Servidor no encontrado")

    deleted = servers.pop(index)
    write_servers_file(servers)

    return deleted

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

        # Tablas compartidas con monitor.py; el esquema permanece sin cambios.
        ensure_monitoring_schema(conn)

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
    if not APP_SETTINGS.seed_default_users:
        return

    raw_users = APP_SETTINGS.default_users_json

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


# -------------------- App factory --------------------
def create_app(
    *,
    normalized_history_source: HistoricalMetricsSource | None = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    app.config.update(
        SECRET_KEY=APP_SETTINGS.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=APP_SETTINGS.session_cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(
            hours=APP_SETTINGS.session_hours
        ),
        PRTG_VERIFY_SSL=SHARED_SETTINGS.verify_ssl,
        HISTORY_CACHE_TTL=APP_SETTINGS.history_cache_ttl,
        REQUEST_TIMEOUT=APP_SETTINGS.request_timeout,
    )

    logging.basicConfig(
        level=APP_SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if APP_SETTINGS.secret_key == DEFAULT_APP_SECRET_KEY:
        app.logger.warning(
            "APP_SECRET_KEY no configurada; se usa un fallback inseguro "
            "permitido solo para desarrollo local."
        )

    init_db()
    seed_default_users(app)

    app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]

    def current_history_servers():
        return app.prtg_servers  # type: ignore[attr-defined]

    def reload_history_servers():
        app.prtg_servers = load_prtg_servers(app)  # type: ignore[attr-defined]
        return app.prtg_servers  # type: ignore[attr-defined]

    app.history_service = HistoryService(  # type: ignore[attr-defined]
        server_provider=current_history_servers,
        server_reloader=reload_history_servers,
        client_factory=lambda: new_prtg_client(app),
        cache_ttl_seconds=app.config["HISTORY_CACHE_TTL"],
        cache_max_entries=APP_SETTINGS.history_cache_max_entries,
        logger=app.logger,
        normalized_source=normalized_history_source,
    )

    app.sensor_control_service = SensorControlService(  # type: ignore[attr-defined]
        server_provider=current_history_servers,
        server_reloader=reload_history_servers,
        client_factory=lambda: new_prtg_client(app),
        history_service=app.history_service,  # type: ignore[attr-defined]
        logger=app.logger,
    )

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

        try:
            payload = app.history_service.get_history(  # type: ignore[attr-defined]
                group,
                sensor_id,
                minutes=minutes,
                average_seconds=avg,
            )
            return jsonify(payload)

        except HistoryServerNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        except HistoryValidationError as exc:
            return jsonify({"error": str(exc)}), 400

        except HistoryUpstreamError:
            return jsonify({"error": "No se pudo consultar PRTG"}), 502

    @app.get("/api/v2/metrics/history")
    @login_required
    def api_metrics_history_v2():
        server = request.args.get("server", "").strip()
        sensor_id = request.args.get("sensor_id", "").strip()
        sensor_type = request.args.get("sensor_type", "").strip()
        points_raw = request.args.get("points")
        start_raw = request.args.get("start")
        end_raw = request.args.get("end")

        if not server or not sensor_id or not sensor_type:
            return jsonify(
                {
                    "error": "server, sensor_id y sensor_type son obligatorios",
                    "code": "invalid_request",
                }
            ), 400

        has_points = points_raw is not None
        has_start = start_raw is not None
        has_end = end_raw is not None

        if has_points and (has_start or has_end):
            return jsonify(
                {
                    "error": "points no puede combinarse con start/end",
                    "code": "invalid_request",
                }
            ), 400

        if not has_points and not has_start and not has_end:
            return jsonify(
                {
                    "error": "debes proporcionar points o start/end",
                    "code": "invalid_request",
                }
            ), 400

        if has_start != has_end:
            return jsonify(
                {
                    "error": "start y end deben proporcionarse juntos",
                    "code": "invalid_request",
                }
            ), 400

        points: int | None = None
        start: datetime | None = None
        end: datetime | None = None

        if has_points:
            try:
                points = int(points_raw)
            except (TypeError, ValueError):
                return jsonify(
                    {
                        "error": "points debe ser un entero positivo",
                        "code": "invalid_request",
                    }
                ), 400

            if not 1 <= points <= METRICS_API_V2_MAX_POINTS:
                return jsonify(
                    {
                        "error": (
                            "points debe estar entre 1 y "
                            f"{METRICS_API_V2_MAX_POINTS}"
                        ),
                        "code": "invalid_request",
                    }
                ), 400
        else:
            try:
                start = _parse_aware_iso8601(start_raw, "start")
                end = _parse_aware_iso8601(end_raw, "end")
            except ValueError as exc:
                return jsonify(
                    {
                        "error": str(exc),
                        "code": "invalid_request",
                    }
                ), 400

            if start >= end:
                return jsonify(
                    {
                        "error": "start debe ser anterior a end",
                        "code": "invalid_request",
                    }
                ), 400

        try:
            result = app.history_service.get_normalized_history(  # type: ignore[attr-defined]
                server,
                sensor_id,
                sensor_type=sensor_type,
                points=points,
                start=start,
                end=end,
            )
            return jsonify(serialize_normalized_history(result))

        except HistoryServerNotFoundError:
            return jsonify(
                {"error": "Servidor no encontrado", "code": "server_not_found"}
            ), 404

        except HistorySensorNotFoundError:
            return jsonify(
                {"error": "Sensor no encontrado", "code": "sensor_not_found"}
            ), 404

        except HistoryValidationError:
            return jsonify(
                {
                    "error": "Solicitud de metricas invalida",
                    "code": "invalid_request",
                }
            ), 400

        except HistoryUpstreamError:
            return jsonify(
                {
                    "error": "No se pudo consultar la fuente de metricas",
                    "code": "upstream_error",
                }
            ), 502

        except Exception as exc:
            app.logger.error(
                "Error inesperado en metrics API v2 error_type=%s",
                type(exc).__name__,
            )
            return jsonify(
                {
                    "error": "Error interno",
                    "code": "internal_error",
                }
            ), 500


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

                app.sensor_control_service.pause_indefinitely(  # type: ignore[attr-defined]
                    group,
                    sensor_id,
                    message,
                )

                response_message = "Sensor pausado indefinidamente"

            else:
                if pause_until:
                    duration_minutes, until_label = parse_pause_until_minutes(pause_until)
                    mode = "until"
                else:
                    duration_minutes = duration_minutes or 60

                duration_minutes = app.sensor_control_service.pause_for(  # type: ignore[attr-defined]
                    group,
                    sensor_id,
                    duration_minutes,
                    message,
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

            return jsonify(
                {
                    "ok": True,
                    "message": response_message,
                }
            )

        except SensorServerNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        except (SensorControlValidationError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

        except SensorControlUpstreamError:
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
            app.sensor_control_service.resume(  # type: ignore[attr-defined]
                group,
                sensor_id,
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

            return jsonify(
                {
                    "ok": True,
                    "message": "Sensor reanudado",
                }
            )

        except SensorServerNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        except SensorControlValidationError as exc:
            return jsonify({"error": str(exc)}), 400

        except SensorControlUpstreamError:
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
            if servers_json_is_configured():
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
            app.history_service.clear()
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
            app.history_service.clear()
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
            app.history_service.clear()
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
        app.history_service.cleanup()

        data_file_size = DATA_FILE.stat().st_size if DATA_FILE.exists() else 0
        data_file_mtime = datetime.fromtimestamp(DATA_FILE.stat().st_mtime).isoformat() if DATA_FILE.exists() else None

        return jsonify(
            {
                "ok": True,
                "data_file_exists": DATA_FILE.exists(),
                "data_file_size_bytes": data_file_size,
                "data_file_mtime": data_file_mtime,
                "servers": len(app.prtg_servers),  # type: ignore[attr-defined]
                "history_cache_entries": app.history_service.cache_size(),
                "history_cache_max_entries": app.history_service.cache_max_entries,
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
        debug=APP_SETTINGS.flask_debug,
    )
