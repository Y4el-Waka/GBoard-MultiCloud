from __future__ import annotations

import csv
import io
import logging
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from threading import RLock
from typing import Any, Callable, Protocol

from metrics.definitions import get_sensor_definition
from metrics.models import MetricChannelDefinition, MetricSample, SensorIdentity
from metrics.prtg_adapter import (
    AdapterIssue,
    PRTGAdapterError,
    PRTGMetricAdapter,
    UnknownChannel,
)
from prtg_client import PRTGClient, PRTGClientError


class HistoryServiceError(Exception):
    """Base error for historic-data operations."""


class HistoryServerNotFoundError(HistoryServiceError):
    """The requested PRTG server is not configured."""


class HistoryValidationError(HistoryServiceError):
    """The requested historic range is invalid."""


class HistoryUpstreamError(HistoryServiceError):
    """The upstream PRTG-compatible API could not be queried."""


class HistorySensorNotFoundError(HistoryServiceError):
    """The requested sensor does not exist in the normalized source."""


class HistorySensorUnsupportedError(HistoryServiceError):
    """The source explicitly identifies an unsupported sensor family."""


class HistorySensorUnknownError(HistoryServiceError):
    """The source cannot safely classify the selected sensor."""


class HistoryUpstreamAuthenticationError(HistoryUpstreamError):
    """The configured upstream credentials were rejected."""


class HistoryUpstreamRateLimitError(HistoryUpstreamError):
    """The conservative upstream request budget is exhausted."""


class HistoryUpstreamInvalidResponseError(HistoryUpstreamError):
    """The upstream response was structurally unusable."""


class HistoricalMetricsSourceError(Exception):
    """A normalized-history source failed before adaptation."""


class HistoricalMetricsSensorNotFoundError(HistoricalMetricsSourceError):
    """A normalized-history source could not locate the requested sensor."""


class HistoricalMetricsAuthenticationError(HistoricalMetricsSourceError):
    """A normalized source rejected configured credentials."""


class HistoricalMetricsRateLimitError(HistoricalMetricsSourceError):
    """A normalized source exhausted its read request budget."""


class HistoricalMetricsInvalidResponseError(HistoricalMetricsSourceError):
    """A normalized source returned structurally invalid data."""


class HistoricalMetricsTransportError(HistoricalMetricsSourceError):
    """A normalized source could not reach its configured upstream."""


class HistoricalMetricsUnsupportedSensorError(HistoricalMetricsSourceError):
    """The source explicitly identifies an unsupported sensor family."""


class HistoricalMetricsUnknownSensorError(HistoricalMetricsSourceError):
    """The source has insufficient evidence to classify a sensor."""


ServerMap = Mapping[str, Mapping[str, Any]]
ServerProvider = Callable[[], ServerMap]
ClientFactory = Callable[[], PRTGClient]
Clock = Callable[[], datetime]
CacheKey = str


class HistoricalMetricsSource(Protocol):
    """Minimal provider boundary for PRTG-shaped multichannel history."""

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]: ...

    def fetch_sensor_metadata(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class NormalizedHistoryResult:
    """Provider-neutral history result with no presentation serialization."""

    identity: SensorIdentity
    sensor_type: str
    samples: tuple[MetricSample, ...]
    channels: tuple[MetricChannelDefinition, ...]
    unknown_channels: tuple[UnknownChannel, ...]
    issues: tuple[AdapterIssue, ...]
    start: datetime | None
    end: datetime | None

    def by_channel(self) -> dict[str, tuple[MetricSample, ...]]:
        """Group samples on demand without storing a duplicate series index."""
        grouped: dict[str, list[MetricSample]] = {}
        for sample in self.samples:
            grouped.setdefault(sample.channel_key, []).append(sample)
        return {
            channel.key: tuple(grouped[channel.key])
            for channel in self.channels
            if channel.key in grouped
        }


@dataclass(frozen=True, slots=True)
class NormalizedSensorMetadata:
    """Stable identity and explicit metric family resolved by a provider."""

    identity: SensorIdentity
    sensor_type: str


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
        normalized_source: HistoricalMetricsSource | None = None,
        metric_adapter: PRTGMetricAdapter | None = None,
    ) -> None:
        self._server_provider = server_provider
        self._server_reloader = server_reloader
        self._client_factory = client_factory
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._cache_max_entries = int(cache_max_entries)
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._range_clock = range_clock
        self._normalized_source = normalized_source
        self._metric_adapter = metric_adapter or (
            PRTGMetricAdapter() if normalized_source is not None else None
        )
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

    def get_normalized_history(
        self,
        server_identifier: str,
        sensor_id: int | str,
        *,
        sensor_type: str,
        points: int | None = 12,
        source_timezone: tzinfo | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> NormalizedHistoryResult:
        """Return canonical samples through the opt-in multichannel path.

        This path intentionally has no cache. The legacy parser and cache
        remain isolated behind the unchanged legacy HTTP contract.
        """
        identifier = str(server_identifier or "").strip()
        sensor_text = str(sensor_id or "").strip()
        selected_type = str(sensor_type or "").strip().casefold()

        if not identifier or not sensor_text:
            raise HistoryValidationError("server identifier e id son obligatorios")
        if isinstance(sensor_id, bool):
            raise HistoryValidationError("sensor_id debe ser un identificador valido")
        if points is None:
            if start is None or end is None:
                raise HistoryValidationError(
                    "start y end son obligatorios cuando points no se proporciona"
                )
        elif (
            isinstance(points, bool)
            or not isinstance(points, int)
            or not 1 <= points <= 10_000
        ):
            raise HistoryValidationError("points debe estar entre 1 y 10000")

        try:
            sensor_definition = get_sensor_definition(selected_type)
        except KeyError:
            raise HistoryValidationError(
                f"tipo de sensor normalizado no soportado: {sensor_type!r}"
            ) from None

        normalized_start = self._normalize_boundary(start, "start")
        normalized_end = self._normalize_boundary(end, "end")
        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start > normalized_end
        ):
            raise HistoryValidationError("start no puede ser posterior a end")

        if self._normalized_source is None or self._metric_adapter is None:
            raise HistoryUpstreamError(
                "la fuente de historico normalizado no esta configurada"
            )

        server = self._lookup_server(identifier)
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            raise HistoryValidationError(
                "el servidor requiere un id estable para historico normalizado"
            )

        try:
            payload = self._normalized_source.fetch_history(
                server,
                sensor_id,
                points=points,
                start=normalized_start,
                end=normalized_end,
            )
        except HistoricalMetricsSensorNotFoundError:
            raise HistorySensorNotFoundError(
                f"Sensor '{sensor_text}' no encontrado"
            ) from None
        except HistoricalMetricsUnsupportedSensorError:
            raise HistorySensorUnsupportedError(
                f"Sensor '{sensor_text}' no soportado"
            ) from None
        except HistoricalMetricsUnknownSensorError:
            raise HistorySensorUnknownError(
                f"Sensor '{sensor_text}' sin clasificacion confiable"
            ) from None
        except HistoricalMetricsAuthenticationError:
            raise HistoryUpstreamAuthenticationError(
                "La autenticacion PRTG fue rechazada"
            ) from None
        except HistoricalMetricsRateLimitError:
            raise HistoryUpstreamRateLimitError(
                "Se agoto el presupuesto de consultas historicas"
            ) from None
        except HistoricalMetricsInvalidResponseError:
            raise HistoryUpstreamInvalidResponseError(
                "PRTG devolvio una respuesta historica invalida"
            ) from None
        except (HistoricalMetricsSourceError, PRTGClientError) as exc:
            self._logger.warning(
                "Error consultando historico normalizado "
                "server_id=%s source_error=%s",
                server_id,
                type(exc).__name__,
            )
            raise HistoryUpstreamError(
                "No se pudo consultar la fuente historica normalizada"
            ) from None

        if not isinstance(payload, Mapping):
            raise HistoryUpstreamError(
                "La fuente historica normalizada devolvio un payload invalido"
            )

        expected_identity = SensorIdentity(
            server_id=server_id,
            sensor_id=sensor_text,
        )
        adapted_identity: SensorIdentity | None = None
        collected_samples: list[MetricSample] = []
        unknown_channels: list[UnknownChannel] = []
        issues: list[AdapterIssue] = []
        effective_start = normalized_start
        effective_end = normalized_end

        if payload.get("format") == "prtg_historic_csv":
            payload_server_id = str(payload.get("server_id") or "").strip()
            payload_sensor_id = str(
                payload.get("sensor_id", payload.get("objid", ""))
            ).strip()
            payload_sensor_type = str(
                payload.get("sensor_type") or ""
            ).strip().casefold()
            if payload_server_id != server_id or payload_sensor_id != sensor_text:
                raise HistoryUpstreamError(
                    "La identidad del historico normalizado no coincide "
                    "con la solicitud"
                )
            if payload_sensor_type != selected_type:
                raise HistoryValidationError(
                    "sensor_type no coincide con la clasificacion del sensor"
                )
            historic_csv = payload.get("historic_csv")
            payload_timezone = payload.get("source_timezone", source_timezone)
            source_metadata = payload.get("source_metadata")
            requested_start = self._normalize_boundary(
                payload.get("requested_start"),
                "provider requested_start",
            )
            requested_end = self._normalize_boundary(
                payload.get("requested_end"),
                "provider requested_end",
            )
            if requested_start is None or requested_end is None:
                raise HistoryUpstreamInvalidResponseError(
                    "La fuente PRTG no declaro el rango consultado"
                )
            if requested_start >= requested_end:
                raise HistoryUpstreamInvalidResponseError(
                    "La fuente PRTG declaro un rango invalido"
                )
            effective_start = requested_start
            effective_end = requested_end
            if not isinstance(historic_csv, str) or not historic_csv.strip():
                raise HistoryUpstreamInvalidResponseError(
                    "La fuente PRTG no devolvio CSV historico"
                )
            if source_metadata is not None and not isinstance(source_metadata, Mapping):
                raise HistoryUpstreamInvalidResponseError(
                    "La metadata de historico PRTG es invalida"
                )
            try:
                adapted = self._metric_adapter.adapt_historic_csv(
                    historic_csv,
                    server_id=server_id,
                    sensor_id=sensor_text,
                    sensor_type=selected_type,
                    source_timezone=payload_timezone,
                    device_id=payload.get("device_id"),
                    source_metadata=source_metadata,
                )
            except PRTGAdapterError as exc:
                self._logger.warning(
                    "CSV historico PRTG invalido "
                    "server_id=%s adapter_error=%s",
                    server_id,
                    type(exc).__name__,
                )
                raise HistoryUpstreamInvalidResponseError(
                    "No se pudo normalizar el CSV historico PRTG"
                ) from None
            if adapted.identity != expected_identity:
                raise HistoryUpstreamError(
                    "La identidad del historico normalizado no coincide "
                    "con la solicitud"
                )
            adapted_identity = adapted.identity
            collected_samples.extend(adapted.samples)
            unknown_channels.extend(adapted.unknown_channels)
            issues.extend(adapted.issues)
        else:
            source_samples = payload.get("samples")
            if (
                not isinstance(source_samples, Sequence)
                or isinstance(source_samples, (str, bytes, bytearray))
                or not source_samples
            ):
                raise HistoryUpstreamError(
                    "La fuente historica normalizada no devolvio muestras"
                )

            for source_sample in source_samples:
                try:
                    adapted = self._metric_adapter.adapt_sensor(
                        source_sample,
                        server_id=server_id,
                        sensor_type=selected_type,
                        source_timezone=source_timezone,
                    )
                except PRTGAdapterError as exc:
                    self._logger.warning(
                        "Fuente historica normalizada invalida "
                        "server_id=%s adapter_error=%s",
                        server_id,
                        type(exc).__name__,
                    )
                    raise HistoryUpstreamError(
                        "No se pudo normalizar la fuente historica"
                    ) from None

                if adapted.identity != expected_identity:
                    raise HistoryUpstreamError(
                        "La identidad del historico normalizado no coincide "
                        "con la solicitud"
                    )
                if adapted_identity is None:
                    adapted_identity = adapted.identity
                collected_samples.extend(adapted.samples)
                unknown_channels.extend(adapted.unknown_channels)
                issues.extend(adapted.issues)

        # Provider order is authoritative: for a duplicate timestamp/channel,
        # the last observation replaces earlier values deterministically.
        deduplicated: dict[tuple[datetime, str], MetricSample] = {}
        for sample in collected_samples:
            deduplicated[(sample.timestamp, sample.channel_key)] = sample
        ordered_samples = tuple(
            sorted(
                deduplicated.values(),
                key=lambda sample: (sample.timestamp, sample.channel_key),
            )
        )

        if effective_start is not None and effective_end is not None:
            ordered_samples = tuple(
                sample
                for sample in ordered_samples
                if effective_start <= sample.timestamp <= effective_end
            )
        if points is not None and ordered_samples:
            selected_timestamps = set(
                sorted({sample.timestamp for sample in ordered_samples})[-points:]
            )
            ordered_samples = tuple(
                sample
                for sample in ordered_samples
                if sample.timestamp in selected_timestamps
            )

        present_keys = {sample.channel_key for sample in ordered_samples}
        channels = tuple(
            channel
            for channel in sensor_definition.channels
            if channel.key in present_keys
        )
        result_start = ordered_samples[0].timestamp if ordered_samples else None
        result_end = ordered_samples[-1].timestamp if ordered_samples else None

        return NormalizedHistoryResult(
            identity=adapted_identity or expected_identity,
            sensor_type=selected_type,
            samples=ordered_samples,
            channels=channels,
            unknown_channels=tuple(unknown_channels),
            issues=tuple(issues),
            start=result_start,
            end=result_end,
        )

    def get_normalized_sensor_metadata(
        self,
        server_identifier: str,
        sensor_id: int | str,
    ) -> NormalizedSensorMetadata:
        """Resolve provider metadata without inspecting mutable sensor names."""
        identifier = str(server_identifier or "").strip()
        sensor_text = str(sensor_id or "").strip()
        if not identifier or not sensor_text or isinstance(sensor_id, bool):
            raise HistoryValidationError(
                "server identifier e id son obligatorios"
            )

        if self._normalized_source is None:
            raise HistoryUpstreamError(
                "la fuente de historico normalizado no esta configurada"
            )

        resolver = getattr(
            self._normalized_source,
            "fetch_sensor_metadata",
            None,
        )
        if not callable(resolver):
            raise HistoryUpstreamError(
                "la fuente normalizada no ofrece metadata de sensores"
            )

        server = self._lookup_server(identifier)
        server_id = str(server.get("id") or "").strip()
        if not server_id:
            raise HistoryValidationError(
                "el servidor requiere un id estable para metadata normalizada"
            )

        try:
            payload = resolver(server, sensor_id)
        except HistoricalMetricsSensorNotFoundError:
            raise HistorySensorNotFoundError(
                f"Sensor '{sensor_text}' no encontrado"
            ) from None
        except HistoricalMetricsUnsupportedSensorError:
            raise HistorySensorUnsupportedError(
                f"Sensor '{sensor_text}' no soportado"
            ) from None
        except HistoricalMetricsUnknownSensorError:
            raise HistorySensorUnknownError(
                f"Sensor '{sensor_text}' sin clasificacion confiable"
            ) from None
        except HistoricalMetricsAuthenticationError:
            raise HistoryUpstreamAuthenticationError(
                "La autenticacion PRTG fue rechazada"
            ) from None
        except HistoricalMetricsRateLimitError:
            raise HistoryUpstreamRateLimitError(
                "Se agoto el presupuesto de consultas historicas"
            ) from None
        except HistoricalMetricsInvalidResponseError:
            raise HistoryUpstreamInvalidResponseError(
                "PRTG devolvio metadata invalida"
            ) from None
        except (HistoricalMetricsSourceError, PRTGClientError) as exc:
            self._logger.warning(
                "Error consultando metadata normalizada "
                "server_id=%s source_error=%s",
                server_id,
                type(exc).__name__,
            )
            raise HistoryUpstreamError(
                "No se pudo consultar metadata normalizada"
            ) from None

        if not isinstance(payload, Mapping):
            raise HistoryUpstreamError(
                "La fuente normalizada devolvio metadata invalida"
            )

        payload_sensor_id = str(
            payload.get("sensor_id", payload.get("objid", ""))
        ).strip()
        if payload_sensor_id != sensor_text:
            raise HistoryUpstreamError(
                "La identidad de metadata normalizada no coincide"
            )

        sensor_type = str(payload.get("sensor_type") or "").strip().casefold()
        classification_status = str(
            payload.get("classification_status") or "supported"
        ).strip().casefold()
        if classification_status == "unsupported" or sensor_type == "unsupported":
            raise HistorySensorUnsupportedError(
                "tipo de sensor normalizado no soportado"
            )
        if classification_status == "unknown" or sensor_type == "unknown":
            raise HistorySensorUnknownError(
                "tipo de sensor normalizado desconocido"
            )
        try:
            get_sensor_definition(sensor_type)
        except KeyError:
            raise HistoryValidationError(
                "tipo de sensor normalizado no soportado"
            ) from None

        return NormalizedSensorMetadata(
            identity=SensorIdentity(
                server_id=server_id,
                sensor_id=sensor_text,
            ),
            sensor_type=sensor_type,
        )

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

    @staticmethod
    def _normalize_boundary(
        value: datetime | None,
        field_name: str,
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise HistoryValidationError(f"{field_name} debe ser datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise HistoryValidationError(
                f"{field_name} debe incluir timezone"
            )
        return value.astimezone(UTC)

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
