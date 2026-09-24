from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_APP_SECRET_KEY = "dev-insecure-change-me"

APP_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on"})
MONITOR_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
APP_DISABLED_VALUES = frozenset(
    {"0", "false", "no", "n", "off", "inactive", "inactivo", "disabled"}
)
MONITOR_DISABLED_VALUES = frozenset(
    {"0", "false", "no", "off", "disabled", "inactivo"}
)


class PRTGServer(TypedDict, total=False):
    id: str
    type: str
    enabled: bool | int | str
    name: str
    url: str
    username: str
    password: str
    passhash: str


@dataclass(frozen=True)
class HTTPSettings:
    retries: int
    backoff: float
    pool_connections: int
    pool_maxsize: int


@dataclass(frozen=True)
class SharedSettings:
    base_dir: Path
    db_path: Path
    output_path: Path
    servers_file: Path
    verify_ssl: bool
    event_retention_days: int
    http: HTTPSettings


@dataclass(frozen=True)
class AppSettings:
    shared: SharedSettings
    port: int
    request_timeout: int
    history_cache_max_entries: int
    history_cache_ttl: int
    alert_sound_dir: Path
    alert_sound_max_bytes: int
    audit_log_max_limit: int
    secret_key: str
    session_hours: int
    session_cookie_secure: bool
    seed_default_users: bool
    default_users_json: str
    log_level: str
    flask_debug: bool
    metrics_provider: Literal["disabled", "synthetic"]


@dataclass(frozen=True)
class MonitorSettings:
    shared: SharedSettings
    log_history_size: int
    update_interval: int
    request_timeout: int
    max_workers: int
    metrics_window_minutes: int
    device_cache_ttl_seconds: int
    previous_state_max_age_minutes: int
    mf_sla_hours: int
    mf_alert_states: frozenset[str]


def load_environment(env_file: str | os.PathLike[str] | None = None) -> None:
    """Load the project .env when python-dotenv is available.

    Environment variables already present keep precedence, matching the previous
    behavior of app.py and monitor.py.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    path = Path(env_file) if env_file is not None else BASE_DIR / ".env"
    load_dotenv(path)


def _environment(environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def env_value(
    name: str,
    default: str | None = None,
    *,
    required: bool = False,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    value = _environment(environ).get(name, default)

    if required and not value:
        raise RuntimeError(f"Missing env var: {name}")

    return value


def env_int(
    name: str,
    default: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    return int(_environment(environ).get(name, str(default)))


def env_float(
    name: str,
    default: float,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    return float(_environment(environ).get(name, str(default)))


def parse_bool(
    value: Any,
    default: bool = False,
    *,
    true_values: frozenset[str] = APP_TRUE_VALUES,
) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    return str(value).strip().lower() in true_values


def env_bool(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
    true_values: frozenset[str] = APP_TRUE_VALUES,
) -> bool:
    return parse_bool(
        _environment(environ).get(name),
        default,
        true_values=true_values,
    )


def parse_metrics_provider(value: Any) -> Literal["disabled", "synthetic"]:
    selected = str(value or "disabled").strip().casefold()
    if selected in {"", "disabled"}:
        return "disabled"
    if selected == "synthetic":
        return "synthetic"
    raise ValueError(
        "COREVIEW_METRICS_PROVIDER debe ser 'disabled' o 'synthetic'"
    )


def parse_config_bool(value: Any, default: bool = True) -> bool:
    """Preserve app.py's permissive enabled/config parsing."""
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    if isinstance(value, str):
        return value.strip().lower() not in APP_DISABLED_VALUES

    return default


def resolve_path(
    path_value: str | os.PathLike[str],
    *,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (Path(base_dir) / path).resolve()


def load_shared_settings(
    *,
    profile: Literal["app", "monitor"],
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> SharedSettings:
    env = _environment(environ)
    root = Path(base_dir).resolve()
    true_values = APP_TRUE_VALUES if profile == "app" else MONITOR_TRUE_VALUES

    return SharedSettings(
        base_dir=root,
        db_path=resolve_path(
            env.get("DB_PATH", "usuarios.db"),
            base_dir=root,
        ),
        output_path=resolve_path(
            env.get("OUTPUT_PATH", "estado_sensores_prtg.json"),
            base_dir=root,
        ),
        servers_file=resolve_path(
            env.get("PRTG_SERVERS_FILE", "servers.json"),
            base_dir=root,
        ),
        verify_ssl=parse_bool(
            env.get("PRTG_VERIFY_SSL"),
            False,
            true_values=true_values,
        ),
        event_retention_days=env_int(
            "SENSOR_EVENT_RETENTION_DAYS",
            90,
            environ=env,
        ),
        http=HTTPSettings(
            retries=env_int("HTTP_RETRIES", 3, environ=env),
            backoff=env_float("HTTP_BACKOFF", 0.5, environ=env),
            pool_connections=env_int(
                "HTTP_POOL_CONNECTIONS",
                20,
                environ=env,
            ),
            pool_maxsize=env_int(
                "HTTP_POOL_MAXSIZE",
                50,
                environ=env,
            ),
        ),
    )


def load_app_settings(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> AppSettings:
    env = _environment(environ)
    shared = load_shared_settings(
        profile="app",
        environ=env,
        base_dir=base_dir,
    )
    secret_key = env.get(
        "APP_SECRET_KEY",
        env.get("SECRET_KEY", DEFAULT_APP_SECRET_KEY),
    )

    return AppSettings(
        shared=shared,
        port=env_int("PORT", 5067, environ=env),
        request_timeout=env_int("REQUEST_TIMEOUT", 20, environ=env),
        history_cache_max_entries=env_int(
            "HISTORY_CACHE_MAX_ENTRIES",
            500,
            environ=env,
        ),
        history_cache_ttl=env_int(
            "HISTORY_CACHE_TTL",
            60,
            environ=env,
        ),
        alert_sound_dir=shared.base_dir / "static" / "uploads" / "alert_sounds",
        alert_sound_max_bytes=env_int(
            "ALERT_SOUND_MAX_BYTES",
            5 * 1024 * 1024,
            environ=env,
        ),
        audit_log_max_limit=env_int(
            "AUDIT_LOG_MAX_LIMIT",
            500,
            environ=env,
        ),
        secret_key=str(secret_key),
        session_hours=env_int("SESSION_HOURS", 8, environ=env),
        session_cookie_secure=env_bool(
            "SESSION_COOKIE_SECURE",
            False,
            environ=env,
        ),
        seed_default_users=env_bool(
            "SEED_DEFAULT_USERS",
            False,
            environ=env,
        ),
        default_users_json=env.get("DEFAULT_USERS_JSON", "[]"),
        log_level=env.get("LOG_LEVEL", "INFO"),
        flask_debug=env_bool("FLASK_DEBUG", False, environ=env),
        metrics_provider=parse_metrics_provider(
            env.get("COREVIEW_METRICS_PROVIDER")
        ),
    )


def load_monitor_settings(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> MonitorSettings:
    env = _environment(environ)
    shared = load_shared_settings(
        profile="monitor",
        environ=env,
        base_dir=base_dir,
    )

    return MonitorSettings(
        shared=shared,
        log_history_size=env_int("LOG_HISTORY_SIZE", 50, environ=env),
        update_interval=env_int("UPDATE_INTERVAL", 30, environ=env),
        request_timeout=env_int("REQUEST_TIMEOUT", 10, environ=env),
        max_workers=env_int("MAX_WORKERS", 6, environ=env),
        metrics_window_minutes=env_int(
            "METRICS_WINDOW_MINUTES",
            120,
            environ=env,
        ),
        device_cache_ttl_seconds=env_int(
            "DEVICE_CACHE_TTL_SECONDS",
            600,
            environ=env,
        ),
        previous_state_max_age_minutes=env_int(
            "PREVIOUS_STATE_MAX_AGE_MINUTES",
            1440,
            environ=env,
        ),
        mf_sla_hours=env_int("MF_SLA_HOURS", 6, environ=env),
        mf_alert_states=frozenset(
            item.strip()
            for item in env.get(
                "MF_ALERT_STATES",
                "Down,Down Acknowledged,Down Partial",
            ).split(",")
            if item.strip()
        ),
    )


def get_servers_file_path(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> Path:
    env = _environment(environ)
    return resolve_path(
        env.get("PRTG_SERVERS_FILE", "servers.json"),
        base_dir=base_dir,
    )


def servers_json_is_configured(
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Match the existing UI read-only check, including whitespace values."""
    return bool(_environment(environ).get("PRTG_SERVERS_JSON"))


def _read_servers_source(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> tuple[str, str]:
    env = _environment(environ)
    raw = (env.get("PRTG_SERVERS_JSON") or "").strip()
    source = "PRTG_SERVERS_JSON"

    if not raw:
        path = get_servers_file_path(environ=env, base_dir=base_dir)
        source = str(path)

        if path.is_file():
            raw = path.read_text(encoding="utf-8")

    return raw, source


def _log(
    logger: Any | None,
    level: str,
    message: str,
    *args: Any,
) -> None:
    if logger is not None:
        getattr(logger, level)(message, *args)


def load_app_prtg_servers(
    logger: Any | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> dict[str, dict[str, Any]]:
    """Load servers with the exact compatibility behavior of app.py.

    The application historically inserts valid records before checking enabled
    and type. That discrepancy with monitor.py is preserved intentionally until
    a separately approved behavior change.
    """
    raw, source = _read_servers_source(
        environ=environ,
        base_dir=base_dir,
    )

    if not raw:
        _log(logger, "warning", "No se configuraron servidores PRTG.")
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log(
            logger,
            "error",
            "Configuracion de PRTG invalida en %s: %s",
            source,
            exc,
        )
        return {}

    if not isinstance(data, list):
        _log(
            logger,
            "error",
            "Configuracion de PRTG invalida en %s: debe ser lista JSON",
            source,
        )
        return {}

    servers: dict[str, dict[str, Any]] = {}

    for index, server in enumerate(data, start=1):
        if not isinstance(server, dict):
            _log(
                logger,
                "warning",
                "Servidor PRTG #%s ignorado: no es objeto JSON.",
                index,
            )
            continue

        name = str(server.get("name", "")).strip()
        url = str(server.get("url", "")).strip().rstrip("/")
        username = str(server.get("username", "")).strip()
        has_secret = bool(server.get("password") or server.get("passhash"))

        if not (name and url and username and has_secret):
            _log(
                logger,
                "warning",
                "Servidor PRTG #%s ignorado por configuracion incompleta.",
                index,
            )
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

        if not parse_config_bool(server.get("enabled", True), default=True):
            continue

        server_type = str(server.get("type", "prtg")).strip().lower()
        if server_type != "prtg":
            continue

    return servers


def load_monitor_prtg_servers(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
    emit: Callable[[str], None] | None = print,
) -> list[dict[str, Any]]:
    """Load servers with the exact filtering behavior of monitor.py."""
    raw, _source = _read_servers_source(
        environ=environ,
        base_dir=base_dir,
    )

    if not raw:
        raise ValueError(
            "Define PRTG_SERVERS_JSON o PRTG_SERVERS_FILE con credenciales."
        )

    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Configuracion PRTG invalida: {exc}") from exc

    if not isinstance(servers, list) or not servers:
        raise ValueError(
            "La configuracion de servidores PRTG debe ser una lista no vacia."
        )

    clean: list[dict[str, Any]] = []

    for index, server in enumerate(servers, start=1):
        if not isinstance(server, dict):
            if emit:
                emit(f"[WARN] Servidor #{index} ignorado: no es objeto JSON")
            continue

        enabled_raw = server.get("enabled", True)
        enabled = True

        if isinstance(enabled_raw, bool):
            enabled = enabled_raw
        elif isinstance(enabled_raw, int):
            enabled = enabled_raw == 1
        elif isinstance(enabled_raw, str):
            enabled = (
                enabled_raw.strip().lower() not in MONITOR_DISABLED_VALUES
            )

        if not enabled:
            if emit:
                emit(
                    f"[INFO] Servidor #{index} desactivado, "
                    "se omite del monitoreo"
                )
            continue

        server_type = str(server.get("type", "prtg")).strip().lower()
        if server_type != "prtg":
            if emit:
                emit(
                    f"[INFO] Servidor #{index} tipo {server_type} "
                    "ignorado por este collector"
                )
            continue

        name = str(server.get("name", "")).strip()
        url = str(server.get("url", "")).strip().rstrip("/")
        username = str(server.get("username", "")).strip()
        has_secret = bool(server.get("password") or server.get("passhash"))

        if not (name and url and username and has_secret):
            if emit:
                emit(
                    f"[WARN] Servidor #{index} ignorado "
                    "por configuracion incompleta"
                )
            continue

        clean.append(
            {
                **server,
                "name": name,
                "url": url,
                "username": username,
            }
        )

    if not clean:
        raise ValueError("No hay servidores PRTG validos.")

    return clean


def new_server_id() -> str:
    return f"srv_{secrets.token_hex(8)}"


def read_servers_file(
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> list[dict[str, Any]]:
    path = get_servers_file_path(environ=environ, base_dir=base_dir)

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
        write_servers_file(
            clean_servers,
            environ=environ,
            base_dir=base_dir,
        )

    return clean_servers


def write_servers_file(
    servers: list[dict[str, Any]],
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] = BASE_DIR,
) -> None:
    path = get_servers_file_path(environ=environ, base_dir=base_dir)
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
