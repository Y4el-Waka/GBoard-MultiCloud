from __future__ import annotations

import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from threading import RLock
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from history_service import (
    HistoricalMetricsAuthenticationError,
    HistoricalMetricsInvalidResponseError,
    HistoricalMetricsRateLimitError,
    HistoricalMetricsSensorNotFoundError,
    HistoricalMetricsSourceError,
    HistoricalMetricsTransportError,
    HistoricalMetricsUnknownSensorError,
    HistoricalMetricsUnsupportedSensorError,
)
from prtg_client import PRTGClient
from prtg_discovery import (
    PRTGDiscoveryAuthenticationError,
    PRTGDiscoveryConnectionError,
    PRTGDiscoveryNotFoundError,
    PRTGDiscoveryUpstreamError,
    PRTGSensorDiscovery,
    PRTGSensorDiscoveryResult,
    PRTGSensorFamily,
)


ClientFactory = Callable[[], PRTGClient]
WallClock = Callable[[], datetime]
MonotonicClock = Callable[[], float]


class PRTGHistoryRateLimiter:
    """Thread-safe, per-process request budget keyed by PRTG server ID."""

    def __init__(
        self,
        *,
        max_requests: int = 5,
        window_seconds: float = 60.0,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if isinstance(max_requests, bool) or not 1 <= max_requests <= 5:
            raise ValueError("max_requests must be between 1 and 5")
        if isinstance(window_seconds, bool) or window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._requests: dict[str, deque[float]] = {}
        self._lock = RLock()

    def acquire(self, server_id: str) -> None:
        key = str(server_id or "").strip()
        if not key:
            raise HistoricalMetricsSourceError("PRTG server identity is invalid")
        now = float(self._clock())
        threshold = now - self.window_seconds
        with self._lock:
            bucket = self._requests.setdefault(key, deque())
            while bucket and bucket[0] <= threshold:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                raise HistoricalMetricsRateLimitError(
                    "PRTG historic request budget is exhausted"
                )
            bucket.append(now)


class RealHistoricalMetricsSource:
    """Read-only normalized-history source for an explicitly configured PRTG.

    The provider owns discovery, request bounds, metadata caching, and the
    historic request budget. It returns the original CSV to HistoryService;
    PRTGMetricAdapter remains the only numeric/channel normalizer.
    """

    MAX_POINTS = 1000
    SUPPORTED_FAMILIES = frozenset(
        {
            PRTGSensorFamily.PING,
            PRTGSensorFamily.CPU,
            PRTGSensorFamily.MEMORY,
            PRTGSensorFamily.TRAFFIC,
            PRTGSensorFamily.SFP,
        }
    )

    def __init__(
        self,
        *,
        client_factory: ClientFactory,
        source_timezone: str | tzinfo,
        history_average_seconds: int = 300,
        metadata_cache_ttl_seconds: int = 300,
        metadata_cache_max_entries: int = 128,
        max_history_requests_per_minute: int = 5,
        expected_processes: int = 1,
        discovery: PRTGSensorDiscovery | None = None,
        clock: WallClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if (
            isinstance(history_average_seconds, bool)
            or not isinstance(history_average_seconds, int)
            or not 60 <= history_average_seconds <= 3600
        ):
            raise ValueError("history_average_seconds must be between 60 and 3600")
        if (
            isinstance(metadata_cache_ttl_seconds, bool)
            or not isinstance(metadata_cache_ttl_seconds, int)
            or metadata_cache_ttl_seconds < 0
        ):
            raise ValueError("metadata cache TTL cannot be negative")
        if (
            isinstance(metadata_cache_max_entries, bool)
            or not isinstance(metadata_cache_max_entries, int)
            or metadata_cache_max_entries < 1
        ):
            raise ValueError("metadata cache max entries must be positive")
        if expected_processes != 1:
            raise ValueError(
                "experimental PRTG metrics provider requires exactly one process"
            )

        self._client_factory = client_factory
        self._source_timezone = self._parse_timezone(source_timezone)
        self._history_average_seconds = history_average_seconds
        self._metadata_cache_ttl_seconds = metadata_cache_ttl_seconds
        self._metadata_cache_max_entries = metadata_cache_max_entries
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._discovery = discovery or PRTGSensorDiscovery(
            client_factory=client_factory
        )
        self._rate_limiter = PRTGHistoryRateLimiter(
            max_requests=max_history_requests_per_minute,
            clock=monotonic_clock,
        )
        self._metadata_cache: OrderedDict[
            tuple[str, str],
            tuple[float, PRTGSensorDiscoveryResult],
        ] = OrderedDict()
        self._metadata_cache_lock = RLock()

    def fetch_sensor_metadata(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> Mapping[str, Any]:
        server_id = self._validate_server(server)
        sensor_text = self._sensor_id(sensor_id)
        result = self._discover(server, server_id, sensor_text)
        family = result.classification.family

        return {
            "server_id": server_id,
            "sensor_id": sensor_text,
            "sensor_type": family.value,
            "classification_status": self._classification_status(family),
            "classification_evidence": result.classification.evidence,
            "classification_reason": result.classification.reason,
            "source_sensor_type": result.sensor.prtg_sensor_type,
            "source_prtg_version": result.sensor.prtg_version,
            "channels": tuple(
                {
                    "source_channel_id": channel.source_channel_id,
                    "source_channel": channel.name,
                    "canonical_key": channel.canonical_key,
                    "source_units": tuple(
                        dict.fromkeys(
                            value.source_unit
                            for value in channel.values
                            if value.source_unit is not None
                        )
                    ),
                    "kinds": tuple(
                        dict.fromkeys(value.kind.value for value in channel.values)
                    ),
                    "issues": channel.issues,
                }
                for channel in result.channels
            ),
        }

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]:
        server_id = self._validate_server(server)
        sensor_text = self._sensor_id(sensor_id)
        discovery = self._discover(server, server_id, sensor_text)
        family = self._require_supported(discovery)
        range_start, range_end = self._request_range(
            points=points,
            start=start,
            end=end,
        )

        self._rate_limiter.acquire(server_id)
        try:
            historic_csv = self._discovery.fetch_historic_csv(
                server,
                sensor_text,
                range_start.astimezone(self._source_timezone),
                range_end.astimezone(self._source_timezone),
                average_seconds=self._history_average_seconds,
            )
        except PRTGDiscoveryAuthenticationError:
            raise HistoricalMetricsAuthenticationError(
                "PRTG authentication failed"
            ) from None
        except PRTGDiscoveryConnectionError:
            raise HistoricalMetricsTransportError(
                "PRTG historic transport failed"
            ) from None
        except PRTGDiscoveryUpstreamError:
            raise HistoricalMetricsInvalidResponseError(
                "PRTG historic response is invalid"
            ) from None

        return {
            "format": "prtg_historic_csv",
            "server_id": server_id,
            "sensor_id": sensor_text,
            "sensor_type": family.value,
            "device_id": discovery.sensor.device_id,
            "historic_csv": historic_csv,
            "source_timezone": self._source_timezone,
            "requested_points": points,
            "requested_start": range_start,
            "requested_end": range_end,
            "aggregation_seconds": self._history_average_seconds,
            "source_metadata": {
                "sensor": discovery.sensor.name,
                "device": discovery.sensor.device_name,
                "group_name": discovery.sensor.group_name,
                "server_name": server.get("name"),
                "status": discovery.sensor.status,
                "source_sensor_type": discovery.sensor.prtg_sensor_type,
                "aggregation_seconds": self._history_average_seconds,
            },
        }

    def clear_metadata_cache(self) -> None:
        with self._metadata_cache_lock:
            self._metadata_cache.clear()

    def metadata_cache_size(self) -> int:
        with self._metadata_cache_lock:
            return len(self._metadata_cache)

    def _discover(
        self,
        server: Mapping[str, Any],
        server_id: str,
        sensor_id: str,
    ) -> PRTGSensorDiscoveryResult:
        cache_key = (server_id, sensor_id)
        now = float(self._monotonic_clock())
        with self._metadata_cache_lock:
            cached = self._metadata_cache.get(cache_key)
            if cached is not None:
                expires_at, result = cached
                if expires_at >= now:
                    self._metadata_cache.move_to_end(cache_key)
                    return result
                self._metadata_cache.pop(cache_key, None)

        try:
            result = self._discovery.discover_sensor(server, sensor_id)
        except PRTGDiscoveryNotFoundError:
            raise HistoricalMetricsSensorNotFoundError(
                "PRTG sensor was not found"
            ) from None
        except PRTGDiscoveryAuthenticationError:
            raise HistoricalMetricsAuthenticationError(
                "PRTG authentication failed"
            ) from None
        except PRTGDiscoveryConnectionError:
            raise HistoricalMetricsTransportError(
                "PRTG discovery transport failed"
            ) from None
        except PRTGDiscoveryUpstreamError:
            raise HistoricalMetricsInvalidResponseError(
                "PRTG discovery response is invalid"
            ) from None

        if result.sensor.identity.server_id != server_id:
            raise HistoricalMetricsInvalidResponseError(
                "PRTG discovery returned a mismatched server"
            )
        if result.sensor.identity.sensor_id != sensor_id:
            raise HistoricalMetricsInvalidResponseError(
                "PRTG discovery returned a mismatched sensor"
            )

        if self._metadata_cache_ttl_seconds > 0:
            expires_at = now + self._metadata_cache_ttl_seconds
            with self._metadata_cache_lock:
                self._metadata_cache[cache_key] = (expires_at, result)
                self._metadata_cache.move_to_end(cache_key)
                while len(self._metadata_cache) > self._metadata_cache_max_entries:
                    self._metadata_cache.popitem(last=False)
        return result

    @classmethod
    def _require_supported(
        cls,
        discovery: PRTGSensorDiscoveryResult,
    ) -> PRTGSensorFamily:
        family = discovery.classification.family
        if family is PRTGSensorFamily.UNSUPPORTED:
            raise HistoricalMetricsUnsupportedSensorError(
                "PRTG sensor family is unsupported"
            )
        if family is PRTGSensorFamily.UNKNOWN:
            raise HistoricalMetricsUnknownSensorError(
                "PRTG sensor family is unknown"
            )
        if family not in cls.SUPPORTED_FAMILIES:
            raise HistoricalMetricsUnknownSensorError(
                "PRTG sensor family could not be classified"
            )
        return family

    def _request_range(
        self,
        *,
        points: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[datetime, datetime]:
        if points is not None:
            if (
                isinstance(points, bool)
                or not isinstance(points, int)
                or not 1 <= points <= self.MAX_POINTS
                or start is not None
                or end is not None
            ):
                raise HistoricalMetricsSourceError(
                    "PRTG points request is invalid"
                )
            range_end = self._aware_utc(self._clock(), "clock")
            range_start = range_end - timedelta(
                seconds=self._history_average_seconds * points
            )
            return range_start, range_end

        range_start = self._aware_utc(start, "start")
        range_end = self._aware_utc(end, "end")
        if range_start >= range_end:
            raise HistoricalMetricsSourceError("PRTG history range is invalid")
        return range_start, range_end

    @staticmethod
    def _classification_status(family: PRTGSensorFamily) -> str:
        if family is PRTGSensorFamily.UNKNOWN:
            return "unknown"
        if family is PRTGSensorFamily.UNSUPPORTED:
            return "unsupported"
        return "supported"

    @staticmethod
    def _sensor_id(value: int | str) -> str:
        if isinstance(value, bool):
            raise HistoricalMetricsSourceError("PRTG sensor identity is invalid")
        text = str(value or "").strip()
        try:
            numeric = int(text)
        except (TypeError, ValueError):
            raise HistoricalMetricsSourceError(
                "PRTG sensor identity is invalid"
            ) from None
        if numeric <= 0 or str(numeric) != text:
            raise HistoricalMetricsSourceError("PRTG sensor identity is invalid")
        return text

    @staticmethod
    def _parse_timezone(value: str | tzinfo) -> tzinfo:
        if isinstance(value, str):
            name = value.strip()
            if not name:
                raise ValueError("source_timezone is required")
            try:
                return ZoneInfo(name)
            except ZoneInfoNotFoundError:
                raise ValueError("source_timezone is not a valid IANA timezone") from None
        if not isinstance(value, tzinfo):
            raise ValueError("source_timezone must be an IANA name or tzinfo")
        probe = datetime(2026, 1, 1, tzinfo=value)
        if probe.utcoffset() is None:
            raise ValueError("source_timezone cannot resolve timestamps")
        return value

    @staticmethod
    def _aware_utc(value: datetime | None, name: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise HistoricalMetricsSourceError(
                f"PRTG {name} must be timezone-aware"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _validate_server(server: Mapping[str, Any]) -> str:
        if not isinstance(server, Mapping):
            raise HistoricalMetricsSourceError("PRTG server is invalid")
        server_id = str(server.get("id") or "").strip()
        username = str(server.get("username") or "").strip()
        has_secret = bool(server.get("passhash") or server.get("password"))
        raw_url = str(server.get("url") or "").strip()
        if not server_id or not username or not has_secret or not raw_url:
            raise HistoricalMetricsSourceError(
                "PRTG server configuration is incomplete"
            )
        try:
            parsed = urlsplit(raw_url)
            host = parsed.hostname
        except ValueError:
            raise HistoricalMetricsSourceError("PRTG server URL is invalid") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise HistoricalMetricsSourceError("PRTG server URL is invalid")
        if parsed.scheme == "http":
            is_loopback = host.casefold() == "localhost"
            if not is_loopback:
                try:
                    is_loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    is_loopback = False
            if not is_loopback:
                raise HistoricalMetricsSourceError(
                    "non-loopback PRTG servers require HTTPS"
                )
        return server_id


__all__ = ["PRTGHistoryRateLimiter", "RealHistoricalMetricsSource"]
