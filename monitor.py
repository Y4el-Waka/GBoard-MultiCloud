from __future__ import annotations
import json
import os
import sqlite3
import tempfile
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC,datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import urllib3
from config import (
    BASE_DIR,
    load_environment,
    load_monitor_prtg_servers,
    load_monitor_settings,
)
from requests.adapters import HTTPAdapter, Retry

load_environment()
MONITOR_SETTINGS = load_monitor_settings()
SHARED_SETTINGS = MONITOR_SETTINGS.shared


# -------------------- Config --------------------
LOG_HISTORY_SIZE = MONITOR_SETTINGS.log_history_size
UPDATE_INTERVAL = MONITOR_SETTINGS.update_interval
REQUEST_TIMEOUT = MONITOR_SETTINGS.request_timeout
MAX_WORKERS = MONITOR_SETTINGS.max_workers

METRICS_WINDOW_MINUTES = MONITOR_SETTINGS.metrics_window_minutes
DEVICE_CACHE_TTL_SECONDS = MONITOR_SETTINGS.device_cache_ttl_seconds
PREVIOUS_STATE_MAX_AGE_MINUTES = (
    MONITOR_SETTINGS.previous_state_max_age_minutes
)

OUTPUT_PATH = SHARED_SETTINGS.output_path
DB_PATH = SHARED_SETTINGS.db_path
MF_SLA_HOURS = MONITOR_SETTINGS.mf_sla_hours
EVENT_RETENTION_DAYS = SHARED_SETTINGS.event_retention_days
VERIFY_SSL = SHARED_SETTINGS.verify_ssl

HTTP_RETRIES = SHARED_SETTINGS.http.retries
HTTP_BACKOFF = SHARED_SETTINGS.http.backoff
HTTP_POOL_CONNECTIONS = SHARED_SETTINGS.http.pool_connections
HTTP_POOL_MAXSIZE = SHARED_SETTINGS.http.pool_maxsize

if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


PRTG_STATUS_MAP = {
    0: "None",
    1: "Unknown",
    2: "Scanning",
    3: "Up",
    4: "Warning",
    5: "Down",
    8: "Paused by Dependency",
    9: "Paused by Schedule",
    10: "Unusual",
    12: "Paused Until",
    13: "Down Acknowledged",
    14: "Down Partial",
}

DOWN_STATES = {"Down", "Down Acknowledged", "Down Partial"}

MF_ALERT_STATES = set(MONITOR_SETTINGS.mf_alert_states)


def metrics_max_points() -> int:
    """
    Calcula un limite aproximado para la ventana de metricas.

    Ejemplo:
    UPDATE_INTERVAL=30 y METRICS_WINDOW_MINUTES=120
    => 240 puntos + margen.
    """
    interval = max(1, UPDATE_INTERVAL)
    return max(10, int((METRICS_WINDOW_MINUTES * 60) / interval) + 10)


def load_servers() -> list[dict[str, Any]]:
    return load_monitor_prtg_servers()


def auth_params(server: dict[str, Any]) -> dict[str, Any]:
    params = {"username": server["username"]}

    if server.get("passhash"):
        params["passhash"] = server["passhash"]
    else:
        params["password"] = server["password"]

    return params


def session_factory() -> requests.Session:
    session = requests.Session()

    retries = Retry(
        total=HTTP_RETRIES,
        backoff_factor=HTTP_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=HTTP_POOL_CONNECTIONS,
        pool_maxsize=HTTP_POOL_MAXSIZE,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def conn_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_event_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with conn_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")

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

        state_cutoff = (
            datetime.now() - timedelta(minutes=PREVIOUS_STATE_MAX_AGE_MINUTES)
        ).strftime("%Y-%m-%d %H:%M:%S")
        event_cutoff = (
            datetime.now() - timedelta(days=max(1, EVENT_RETENTION_DAYS))
        ).strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "DELETE FROM sensor_state WHERE last_seen_at < ?",
            (state_cutoff,),
        )
        conn.execute(
            "DELETE FROM sensor_events WHERE created_at < ?",
            (event_cutoff,),
        )




class PRTGMonitor:
    def __init__(self, servers: list[dict[str, Any]]):
        self.servers = servers

        init_event_db()

        self.metrics_series: deque[dict[str, Any]] = deque(maxlen=metrics_max_points())

        self.status_history = {
            "up_events": deque(maxlen=LOG_HISTORY_SIZE),
            "down_events": deque(maxlen=LOG_HISTORY_SIZE),
        }

        previous_states, previous_seen_at = self._load_previous_states()
        self.previous_states: defaultdict[tuple[str, str], str] = defaultdict(
            str,
            previous_states,
        )
        self.previous_seen_at: dict[tuple[str, str], datetime] = previous_seen_at
        self.mf_alert_states = self._load_mf_alert_states()

        self.pending_state_rows: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.pending_events: list[tuple[Any, ...]] = []
        self.pending_mf_upserts: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.pending_mf_deletes: set[tuple[str, str]] = set()

        self.device_ip_cache: dict[str, tuple[datetime, dict[str, str]]] = {}

        self.http = session_factory()
        self.last_good_servers = servers

        self._load_recent_status_history()

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value or ""))
        except (TypeError, ValueError):
            return None

    def _load_previous_states(
        self,
    ) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], datetime]]:
        cutoff = (
            datetime.now() - timedelta(minutes=PREVIOUS_STATE_MAX_AGE_MINUTES)
        ).strftime("%Y-%m-%d %H:%M:%S")

        with conn_db() as conn:
            rows = conn.execute(
                """
                SELECT group_name, sensor_id, status, last_seen_at
                FROM sensor_state
                WHERE last_seen_at >= ?
                """,
                (cutoff,),
            ).fetchall()

        states: dict[tuple[str, str], str] = {}
        seen_at: dict[tuple[str, str], datetime] = {}

        for row in rows:
            key = (str(row["group_name"]), str(row["sensor_id"]))
            states[key] = str(row["status"])

            parsed = self._parse_datetime(row["last_seen_at"])

            if parsed:
                seen_at[key] = parsed

        return states, seen_at

    def _load_mf_alert_states(self) -> dict[tuple[str, str], dict[str, Any]]:
        with conn_db() as conn:
            rows = conn.execute(
                """
                SELECT
                    group_name,
                    sensor_id,
                    device_name,
                    sensor_name,
                    alert_started_at,
                    deadline_at,
                    last_status,
                    updated_at
                FROM mf_alert_state
                """
            ).fetchall()

        result: dict[tuple[str, str], dict[str, Any]] = {}

        for row in rows:
            key = (str(row["group_name"]), str(row["sensor_id"]))
            result[key] = dict(row)

        return result

    def _load_recent_status_history(self) -> None:
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
                WHERE event_type IN ('alert', 'recovery')
                ORDER BY id DESC
                LIMIT ?
                """,
                (LOG_HISTORY_SIZE * 4,),
            ).fetchall()

        for row in reversed(rows):
            entry = {
                "sensor": row["sensor_name"] or "Unknown",
                "device": row["device_name"] or "Unknown",
                "grupo": row["group_name"],
                "sensor_id": row["sensor_id"],
                "timestamp": row["created_at"],
                "previous_status": row["previous_status"],
                "new_status": row["new_status"],
            }

            if row["event_type"] == "recovery":
                self.status_history["up_events"].appendleft(entry)
            elif row["event_type"] == "alert":
                self.status_history["down_events"].appendleft(entry)


    @staticmethod
    def _write_atomic(data: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix="prtg_",
            suffix=".json",
            dir=str(path.parent),
        )

        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")

            os.replace(tmp_name, path)

        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _reload_servers_safe(self) -> None:
        try:
            servers = load_servers()
            self.servers = servers
            self.last_good_servers = servers
        except Exception as exc:
            print(f"[WARN] No se pudo recargar servidores. Uso ultima config valida. Error: {exc}")
            self.servers = self.last_good_servers

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.http.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            verify=VERIFY_SSL,
        )

        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Respuesta PRTG no es JSON objeto")

        return payload

    def _get_device_ip_map(self, server: dict[str, Any], base_params: dict[str, Any]) -> dict[str, str]:
        """
        Cachea mapa device_id -> IP/host.

        Esto evita pedir content=devices en cada ciclo.
        Por defecto se refresca cada 10 minutos.
        """
        server_name = server["name"]
        now = datetime.utcnow()

        cached = self.device_ip_cache.get(server_name)

        if cached:
            expires_at, device_map = cached

            if expires_at > now:
                return device_map

        try:
            devices_payload = self._get_json(
                f"{server['url']}/api/table.json",
                {
                    **base_params,
                    "content": "devices",
                    "columns": "objid,host",
                },
            )

            device_ip = {
                str(device["objid"]): device.get("host", "N/A")
                for device in devices_payload.get("devices", [])
                if device.get("objid")
            }

            self.device_ip_cache[server_name] = (
                now + timedelta(seconds=DEVICE_CACHE_TTL_SECONDS),
                device_ip,
            )

            return device_ip

        except Exception as exc:
            if cached:
                print(f"[WARN] {server_name}: fallo refrescando devices, uso cache anterior. Error: {exc}")
                return cached[1]

            raise

    def _fetch_server(self, server: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]]:
        try:
            base_params = auth_params(server)

            device_ip = self._get_device_ip_map(server, base_params)

            sensors_payload = self._get_json(
                f"{server['url']}/api/table.json",
                {
                    **base_params,
                    "content": "sensors",
                    "columns": "device,sensor,status,status_raw,lastvalue,message,parentid,objid",
                },
            )

            return server["name"], sensors_payload, device_ip

        except requests.RequestException as exc:
            return server["name"], {"error": f"Conn error: {exc}"}, {}

        except Exception as exc:
            return server["name"], {"error": f"Unexpected: {exc}"}, {}

    def _reset_pending_persistence(self) -> None:
        self.pending_state_rows.clear()
        self.pending_events.clear()
        self.pending_mf_upserts.clear()
        self.pending_mf_deletes.clear()

    def _flush_persistence(self) -> None:
        try:
            with conn_db() as conn:
                if self.pending_state_rows:
                    conn.executemany(
                        """
                        INSERT INTO sensor_state(
                            group_name,
                            sensor_id,
                            device_name,
                            sensor_name,
                            status,
                            last_seen_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(group_name, sensor_id) DO UPDATE SET
                            device_name = excluded.device_name,
                            sensor_name = excluded.sensor_name,
                            status = excluded.status,
                            last_seen_at = excluded.last_seen_at,
                            updated_at = excluded.updated_at
                        """,
                        list(self.pending_state_rows.values()),
                    )

                if self.pending_events:
                    conn.executemany(
                        """
                        INSERT INTO sensor_events(
                            group_name,
                            sensor_id,
                            device_name,
                            sensor_name,
                            previous_status,
                            new_status,
                            event_type,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self.pending_events,
                    )

                if self.pending_mf_upserts:
                    conn.executemany(
                        """
                        INSERT INTO mf_alert_state(
                            group_name,
                            sensor_id,
                            device_name,
                            sensor_name,
                            alert_started_at,
                            deadline_at,
                            last_status,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(group_name, sensor_id) DO UPDATE SET
                            device_name = excluded.device_name,
                            sensor_name = excluded.sensor_name,
                            alert_started_at = excluded.alert_started_at,
                            deadline_at = excluded.deadline_at,
                            last_status = excluded.last_status,
                            updated_at = excluded.updated_at
                        """,
                        list(self.pending_mf_upserts.values()),
                    )

                if self.pending_mf_deletes:
                    conn.executemany(
                        """
                        DELETE FROM mf_alert_state
                        WHERE group_name = ? AND sensor_id = ?
                        """,
                        list(self.pending_mf_deletes),
                    )

        except sqlite3.Error as exc:
            print(f"[WARN] No se pudo persistir eventos/estado: {exc}")

    def _apply_mf_sla(
        self,
        record: dict[str, Any],
        group: str,
        sensor_id: str,
        device: str,
        sensor_name: str,
        status: str,
        observed_at: datetime,
    ) -> None:
        is_mf = "MF" in sensor_name.upper()
        record["is_mf"] = is_mf
        record["mf_alert_active"] = False
        record["mf_alert_started_at"] = None
        record["mf_sla_deadline"] = None
        record["mf_sla_hours"] = MF_SLA_HOURS
        record["mf_sla_breached"] = False

        key = (group, sensor_id)

        if not is_mf:
            if key in self.mf_alert_states:
                self.mf_alert_states.pop(key, None)
                self.pending_mf_deletes.add(key)
                self.pending_mf_upserts.pop(key, None)

            return

        alert_active = status in MF_ALERT_STATES

        if not alert_active:
            if key in self.mf_alert_states:
                self.mf_alert_states.pop(key, None)
                self.pending_mf_deletes.add(key)
                self.pending_mf_upserts.pop(key, None)

            return

        state = self.mf_alert_states.get(key)

        if not state:
            started = observed_at
            deadline = started + timedelta(hours=max(1, MF_SLA_HOURS))
            state = {
                "group_name": group,
                "sensor_id": sensor_id,
                "device_name": device,
                "sensor_name": sensor_name,
                "alert_started_at": started.isoformat(timespec="seconds"),
                "deadline_at": deadline.isoformat(timespec="seconds"),
                "last_status": status,
                "updated_at": observed_at.isoformat(timespec="seconds"),
            }
            self.mf_alert_states[key] = state
        else:
            state["device_name"] = device
            state["sensor_name"] = sensor_name
            state["last_status"] = status
            state["updated_at"] = observed_at.isoformat(timespec="seconds")

        self.pending_mf_deletes.discard(key)
        self.pending_mf_upserts[key] = (
            group,
            sensor_id,
            device,
            sensor_name,
            state["alert_started_at"],
            state["deadline_at"],
            status,
            observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        )

        deadline_dt = self._parse_datetime(str(state["deadline_at"]))

        record["mf_alert_active"] = True
        record["mf_alert_started_at"] = state["alert_started_at"]
        record["mf_sla_deadline"] = state["deadline_at"]
        record["mf_sla_breached"] = bool(
            deadline_dt and observed_at >= deadline_dt
        )

    def fetch(self) -> dict[str, Any]:
        started_at = datetime.now()
        now_label = started_at.strftime("%Y-%m-%d %H:%M:%S")

        self._reload_servers_safe()
        self._reset_pending_persistence()

        output: dict[str, Any] = {
            "ultima_actualizacion": now_label,
            "sensores": [],
            "logs": {
                "last_up": [],
                "last_down": [],
            },
            "metrics": {
                "window_minutes": METRICS_WINDOW_MINUTES,
                "series": [],
            },
        }

        seen_keys: set[tuple[str, str]] = set()
        errors = 0

        worker_count = max(1, min(MAX_WORKERS, len(self.servers)))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(self._fetch_server, server)
                for server in self.servers
            ]

            for future in as_completed(futures):
                name, payload, device_ip = future.result()

                if payload.get("error"):
                    errors += 1
                    self._add_error(name, payload["error"], output)
                else:
                    self._process_server(name, payload, device_ip, output, seen_keys)

        self._prune_previous_states(seen_keys)
        self._flush_persistence()
        self._append_metrics(output)

        output["logs"] = {
            "last_up": list(self.status_history["up_events"]),
            "last_down": list(self.status_history["down_events"]),
        }

        output["metrics"] = {
            "window_minutes": METRICS_WINDOW_MINUTES,
            "series": list(self.metrics_series),
        }

        output["collector"] = {
            "type": "prtg",
            "servers_configured": len(self.servers),
            "servers_with_error": errors,
            "duration_seconds": round((datetime.now() - started_at).total_seconds(), 2),
            "device_cache_ttl_seconds": DEVICE_CACHE_TTL_SECONDS,
            "mf_sla_hours": MF_SLA_HOURS,
            "persistent_events": True,
        }

        self._write_atomic(output, OUTPUT_PATH)

        return output


    def _append_metrics(self, output: dict[str, Any]) -> None:
        sensors = output["sensores"]
        now = datetime.now()

        point = {
            "t": now.isoformat(timespec="seconds"),
            "total": len(sensors),
            "up": 0,
            "warning": 0,
            "paused": 0,
            "ack": 0,
            "down": 0,
        }

        for item in sensors:
            status = str(item.get("status", ""))

            if status == "Up":
                point["up"] += 1
            elif status == "Warning":
                point["warning"] += 1

            if "paused" in status.lower():
                point["paused"] += 1

            if status == "Down Acknowledged":
                point["ack"] += 1

            if status in {"Down", "Down Partial"}:
                point["down"] += 1

        self.metrics_series.append(point)

        cutoff = now - timedelta(minutes=METRICS_WINDOW_MINUTES)

        while self.metrics_series:
            try:
                oldest = datetime.fromisoformat(self.metrics_series[0]["t"])
            except Exception:
                self.metrics_series.popleft()
                continue

            if oldest >= cutoff:
                break

            self.metrics_series.popleft()

    def _process_server(
        self,
        name: str,
        data: dict[str, Any],
        device_ip_map: dict[str, str],
        output: dict[str, Any],
        seen_keys: set[tuple[str, str]],
    ) -> None:
        observed_at = datetime.now()
        timestamp = observed_at.strftime("%Y-%m-%d %H:%M:%S")
        sensors = data.get("sensors", [])

        if not isinstance(sensors, list):
            self._add_error(name, "Respuesta sin lista de sensores", output)
            return

        for sensor in sensors:
            if not isinstance(sensor, dict):
                continue

            raw_status = sensor.get("status_raw", 1)
            code = int(raw_status) if str(raw_status).isdigit() else 1
            status = PRTG_STATUS_MAP.get(code, "Unknown")

            parent_id = str(sensor.get("parentid", ""))
            sensor_id = str(sensor.get("objid", "N/A"))

            ip_address = device_ip_map.get(
                parent_id,
                device_ip_map.get(sensor_id, "N/A"),
            )

            device = str(sensor.get("device", "Unknown"))
            sensor_name = str(sensor.get("sensor", "Unknown"))

            record = {
                "device": device,
                "sensor": sensor_name,
                "status": status,
                "status_code": code,
                "lastvalue": sensor.get("lastvalue", "N/A"),
                "grupo": name,
                "timestamp": timestamp,
                "message": sensor.get("message", ""),
                "ip_address": ip_address,
                "sensor_id": sensor_id,
                "parent_id": parent_id,
            }

            key = (name, sensor_id)
            seen_keys.add(key)

            self._track_change(
                group=name,
                sensor_id=sensor_id,
                device=device,
                sensor=sensor_name,
                status=status,
                timestamp=timestamp,
                key=key,
            )

            self._apply_mf_sla(
                record=record,
                group=name,
                sensor_id=sensor_id,
                device=device,
                sensor_name=sensor_name,
                status=status,
                observed_at=observed_at,
            )

            self.pending_state_rows[key] = (
                name,
                sensor_id,
                device,
                sensor_name,
                status,
                timestamp,
                timestamp,
            )

            output["sensores"].append(record)

    def _track_change(
        self,
        group: str,
        sensor_id: str,
        device: str,
        sensor: str,
        status: str,
        timestamp: str,
        key: tuple[str, str],
    ) -> None:
        previous = self.previous_states.get(key)
        event_type = ""

        if not previous and status in DOWN_STATES:
            event_type = "alert"
        elif previous and previous != status:
            if status == "Up" and previous in DOWN_STATES:
                event_type = "recovery"
            elif status in DOWN_STATES and previous not in DOWN_STATES:
                event_type = "alert"
            else:
                event_type = "status_change"

        if event_type:
            entry = {
                "sensor": sensor,
                "device": device,
                "grupo": group,
                "sensor_id": sensor_id,
                "timestamp": timestamp,
                "previous_status": previous or "Sin estado previo",
                "new_status": status,
            }

            if event_type == "recovery":
                self.status_history["up_events"].appendleft(entry)
            elif event_type == "alert":
                self.status_history["down_events"].appendleft(entry)

            self.pending_events.append(
                (
                    group,
                    sensor_id,
                    device,
                    sensor,
                    previous or "",
                    status,
                    event_type,
                    timestamp,
                )
            )

        self.previous_states[key] = status
        self.previous_seen_at[key] = datetime.now()


    def _prune_previous_states(self, seen_keys: set[tuple[str, str]]) -> None:
        """
        Evita que previous_states crezca indefinidamente si sensores desaparecen,
        cambian de nombre o se borran en PRTG.
        """
        if not self.previous_states:
            return

        cutoff = datetime.now() - timedelta(minutes=PREVIOUS_STATE_MAX_AGE_MINUTES)
        to_delete: list[tuple[str, str]] = []

        for key in list(self.previous_states.keys()):
            if key in seen_keys:
                continue

            last_seen = self.previous_seen_at.get(key)

            if not last_seen or last_seen < cutoff:
                to_delete.append(key)

        for key in to_delete:
            self.previous_states.pop(key, None)
            self.previous_seen_at.pop(key, None)
            self.pending_state_rows.pop(key, None)

            if key in self.mf_alert_states:
                self.mf_alert_states.pop(key, None)
                self.pending_mf_upserts.pop(key, None)
                self.pending_mf_deletes.add(key)


    @staticmethod
    def _add_error(name: str, message: str, output: dict[str, Any]) -> None:
        output["sensores"].append(
            {
                "device": "Error de Conexion",
                "sensor": message,
                "status": "Down",
                "status_code": -1,
                "lastvalue": "N/A",
                "grupo": name,
                "message": message,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ip_address": "N/A",
                "sensor_id": "N/A",
                "parent_id": "N/A",
            }
        )


def main() -> None:
    try:
        servers = load_servers()
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

    monitor = PRTGMonitor(servers)

    print(f"[{datetime.now()}] Monitor iniciado con {len(servers)} servidores.")
    print(f"[{datetime.now()}] Salida JSON: {OUTPUT_PATH}")
    print(f"[{datetime.now()}] Intervalo: {UPDATE_INTERVAL}s | Workers: {MAX_WORKERS}")
    print(f"[{datetime.now()}] Cache devices: {DEVICE_CACHE_TTL_SECONDS}s | Ventana metricas: {METRICS_WINDOW_MINUTES} min")

    try:
        while True:
            started = time.monotonic()

            data = monitor.fetch()

            elapsed = time.monotonic() - started
            collector = data.get("collector", {})

            print(
                f"[{datetime.now()}] OK: "
                f"{len(data.get('sensores', []))} sensores | "
                f"{collector.get('servers_configured', len(monitor.servers))} servidores | "
                f"{collector.get('servers_with_error', 0)} errores | "
                f"{elapsed:.2f}s"
            )

            sleep_for = max(1.0, UPDATE_INTERVAL - elapsed)
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\nMonitor detenido por el usuario.")


if __name__ == "__main__":
    main()