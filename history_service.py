from __future__ import annotations

import csv
import io
import logging
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Callable, Mapping

from prtg_client import PRTGClient, PRTGClientError


class HistoryServiceError(Exception):
    """Base error for historic-data operations."""


class HistoryServerNotFoundError(HistoryServiceError):
    """The requested PRTG server is not configured."""


class HistoryValidationError(HistoryServiceError):
    """The requested historic range is invalid."""


class HistoryUpstreamError(HistoryServiceError):
    """The upstream PRTG-compatible API could not be queried."""


ServerMap = Mapping[str, Mapping[str, Any]]
ServerProvider = Callable[[], ServerMap]
ClientFactory = Callable[[], PRTGClient]
Clock = Callable[[], datetime]
CacheKey = str


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class HistoryService:
    """Historic PRTG orchestration without Flask or presentation concerns."""

    MIN_HISTORY_MINUTES = 5
    MAX_HISTORY_MINUTES = 24 * 60
    MIN_AVERAGE_SECONDS = 60
    MAX_AVERAGE_SECONDS = 3600

    def __init__(
        self,
        *,
        server_provider: ServerProvider,
        client_factory: ClientFactory,
        cache_ttl_seconds: int,
        cache_max_entries: int,
        server_reloader: ServerProvider | None = None,
        logger: logging.Logger | None = None,
        clock: Clock = _utcnow_naive,
        range_clock: Clock = datetime.now,
    ) -> None:
        self._server_provider = server_provider
        self._server_reloader = server_reloader
        self._client_factory = client_factory
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._cache_max_entries = int(cache_max_entries)
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._range_clock = range_clock
        self._cache: OrderedDict[
            CacheKey,
            tuple[datetime, dict[str, Any]],
        ] = OrderedDict()
        self._cache_lock = RLock()

        if self._cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds no puede ser negativo")
        if self._cache_max_entries < 1:
            raise ValueError("cache_max_entries debe ser mayor que cero")

    @property
    def cache_max_entries(self) -> int:
        return self._cache_max_entries

    def get_history(
        self,
        server_identifier: str,
        sensor_id: int,
        *,
        minutes: int,
        average_seconds: int,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        self._validate_range(minutes, average_seconds)

        identifier = str(server_identifier or "").strip()

        if not identifier or not isinstance(sensor_id, int) or not sensor_id:
            raise HistoryValidationError("group e id son obligatorios")

        server = self._lookup_server(identifier)
        cache_key = f"{identifier}:{sensor_id}:{minutes}:{average_seconds}"
        cached = self._cache_get(cache_key)

        if cached is not None:
            return cached

        range_end = end or self._range_clock()
        range_start = range_end - timedelta(minutes=minutes)

        try:
            with self._client_factory() as client:
                historical_csv = client.get_historic_csv(
                    server,
                    sensor_id,
                    range_start,
                    range_end,
                    average_seconds=average_seconds,
                )
        except PRTGClientError as exc:
            self._logger.warning(
                "Error consultando historico PRTG %s/%s: %s",
                identifier,
                sensor_id,
                exc,
            )
            raise HistoryUpstreamError("No se pudo consultar PRTG") from None

        payload = parse_history_csv(historical_csv)

        if not payload.get("series"):
            self._logger.warning(
                "Historico sin puntos group=%s sensor=%s avg=%s status=%s error=%s preview=%s",
                identifier,
                sensor_id,
                average_seconds,
                200,
                payload.get("error"),
                historical_csv[:300],
            )

        self._cache_set(cache_key, payload)
        return payload

    def clear(self) -> None:
        """Invalidate every cached historic response."""
        with self._cache_lock:
            self._cache.clear()

    def invalidate_sensor(self, server_identifier: str, sensor_id: int) -> None:
        """Invalidate cached ranges for one sensor."""
        identifier = str(server_identifier or "").strip()
        key_prefix = f"{identifier}:{sensor_id}:"

        with self._cache_lock:
            keys = [
                key
                for key in self._cache
                if key.startswith(key_prefix)
            ]

            for key in keys:
                self._cache.pop(key, None)

    def cleanup(self) -> None:
        now = self._clock()

        with self._cache_lock:
            expired_keys = [
                key
                for key, (expires_at, _) in self._cache.items()
                if expires_at < now
            ]

            for key in expired_keys:
                self._cache.pop(key, None)

    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    def _lookup_server(self, identifier: str) -> Mapping[str, Any]:
        server = self._server_provider().get(identifier)

        if server is None and self._server_reloader is not None:
            server = self._server_reloader().get(identifier)

        if server is None:
            raise HistoryServerNotFoundError(
                f"Grupo '{identifier}' no encontrado"
            )

        return server

    def _validate_range(self, minutes: int, average_seconds: int) -> None:
        if (
            not isinstance(minutes, int)
            or isinstance(minutes, bool)
            or not self.MIN_HISTORY_MINUTES
            <= minutes
            <= self.MAX_HISTORY_MINUTES
        ):
            raise HistoryValidationError(
                "minutes debe estar entre 5 y 1440"
            )

        if (
            not isinstance(average_seconds, int)
            or isinstance(average_seconds, bool)
            or not self.MIN_AVERAGE_SECONDS
            <= average_seconds
            <= self.MAX_AVERAGE_SECONDS
        ):
            raise HistoryValidationError(
                "avg debe estar entre 60 y 3600"
            )

    def _cache_get(self, key: CacheKey) -> dict[str, Any] | None:
        now = self._clock()

        with self._cache_lock:
            item = self._cache.get(key)

            if item is None:
                return None

            expires_at, payload = item

            if expires_at < now:
                self._cache.pop(key, None)
                return None

            self._cache.move_to_end(key)
            return payload

    def _cache_set(self, key: CacheKey, value: dict[str, Any]) -> None:
        expires_at = self._clock() + timedelta(
            seconds=self._cache_ttl_seconds
        )

        with self._cache_lock:
            self._cache[key] = (expires_at, value)
            self._cache.move_to_end(key)

            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)


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
    Parse the current PRTG historic CSV contract.

    This intentionally preserves the existing Ping-oriented behavior. Phase 0.3
    must replace these fixed channel classifications with metric metadata rather
    than adding more sensor-specific conditions here.
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
