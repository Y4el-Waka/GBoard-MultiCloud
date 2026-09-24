from __future__ import annotations

import ipaddress
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from history_service import (
    HistoricalMetricsSensorNotFoundError,
    HistoricalMetricsSourceError,
)


RequestGet = Callable[..., Any]


class SyntheticHistoricalMetricsSource:
    """Read PRTG-shaped multichannel history from the local lab simulator.

    The provider owns source acquisition and temporal filtering only. Channel
    mapping, unit conversion, issues, and canonical samples remain exclusively
    in ``PRTGMetricAdapter`` through ``HistoryService``.
    """

    MAX_POINTS = 1000

    def __init__(
        self,
        *,
        timeout: int | float,
        request_get: RequestGet | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive number")
        self.timeout = timeout
        self._request_get = request_get or self._loopback_request

    @staticmethod
    def _loopback_request(url: str, **kwargs: Any) -> requests.Response:
        with requests.Session() as session:
            session.trust_env = False
            return session.get(url, **kwargs)

    def fetch_history(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        *,
        points: int | None,
        start: datetime | None,
        end: datetime | None,
    ) -> Mapping[str, Any]:
        base_url = self._local_simulator_url(server)
        sensor_text = str(sensor_id or "").strip()
        if not sensor_text or isinstance(sensor_id, bool):
            raise HistoricalMetricsSourceError(
                "synthetic sensor identity is invalid"
            )

        if points is not None:
            if (
                isinstance(points, bool)
                or not isinstance(points, int)
                or not 1 <= points <= self.MAX_POINTS
            ):
                raise HistoricalMetricsSourceError(
                    "synthetic points request is outside supported limits"
                )
            if start is not None or end is not None:
                raise HistoricalMetricsSourceError(
                    "synthetic points and range modes cannot be combined"
                )
            return self._request_history(
                base_url,
                sensor_id=sensor_text,
                points=points,
            )

        normalized_start = self._aware_utc(start, "start")
        normalized_end = self._aware_utc(end, "end")
        if normalized_start >= normalized_end:
            raise HistoricalMetricsSourceError(
                "synthetic history range is invalid"
            )

        probe = self._request_history(
            base_url,
            sensor_id=sensor_text,
            points=1,
        )
        tick_seconds = self._tick_seconds(probe)
        latest = self._sample_timestamp(probe["samples"][-1])

        if normalized_start > latest:
            raise HistoricalMetricsSourceError(
                "synthetic history range contains no available samples"
            )

        required_points = (
            math.ceil(
                (latest - normalized_start).total_seconds() / tick_seconds
            )
            + 1
        )
        if required_points > self.MAX_POINTS:
            raise HistoricalMetricsSourceError(
                "synthetic history range exceeds available point resolution"
            )

        payload = (
            probe
            if required_points == 1
            else self._request_history(
                base_url,
                sensor_id=sensor_text,
                points=required_points,
            )
        )
        filtered_samples = []
        for sample in payload["samples"]:
            timestamp = self._sample_timestamp(sample)
            if normalized_start <= timestamp <= normalized_end:
                filtered_samples.append(sample)

        if not filtered_samples:
            raise HistoricalMetricsSourceError(
                "synthetic history range contains no available samples"
            )

        return {**payload, "samples": filtered_samples}

    def _request_history(
        self,
        base_url: str,
        *,
        sensor_id: str,
        points: int,
    ) -> dict[str, Any]:
        endpoint = f"{base_url}/api/multichannel_history.json"
        response = None
        try:
            response = self._request_get(
                endpoint,
                params={"id": sensor_id, "points": points},
                timeout=self.timeout,
                allow_redirects=False,
                headers={"Accept": "application/json"},
            )
        except (requests.Timeout, requests.ConnectionError):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source is unavailable"
            ) from None
        except requests.RequestException:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source request failed"
            ) from None

        try:
            status_code = getattr(response, "status_code", 200)
            if status_code == 404:
                raise HistoricalMetricsSensorNotFoundError(
                    "synthetic sensor was not found"
                )
            if not isinstance(status_code, int) or not 200 <= status_code < 300:
                raise HistoricalMetricsSourceError(
                    "synthetic metrics source returned an HTTP error"
                )

            try:
                payload = response.json()
            except (TypeError, ValueError):
                raise HistoricalMetricsSourceError(
                    "synthetic metrics source returned invalid JSON"
                ) from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, dict) or payload.get("synthetic") is not True:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source returned an invalid payload"
            )
        if str(payload.get("sensor_id") or "").strip() != sensor_id:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source returned a mismatched sensor"
            )
        samples = payload.get("samples")
        if (
            not isinstance(samples, Sequence)
            or isinstance(samples, (str, bytes, bytearray))
            or not samples
        ):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source returned no samples"
            )
        return payload

    @staticmethod
    def _aware_utc(value: datetime | None, name: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise HistoricalMetricsSourceError(
                f"synthetic {name} boundary must be timezone-aware"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _tick_seconds(payload: Mapping[str, Any]) -> int:
        value = payload.get("tick_seconds")
        if isinstance(value, bool):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source has invalid tick resolution"
            )
        try:
            tick_seconds = int(value)
        except (TypeError, ValueError):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source has invalid tick resolution"
            ) from None
        if tick_seconds <= 0:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source has invalid tick resolution"
            )
        return tick_seconds

    @staticmethod
    def _sample_timestamp(sample: Any) -> datetime:
        if not isinstance(sample, Mapping):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source returned an invalid sample"
            )
        raw = sample.get("timestamp")
        if not isinstance(raw, str) or not raw.strip():
            raise HistoricalMetricsSourceError(
                "synthetic metrics source sample has no timestamp"
            )
        text = raw.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source sample has an invalid timestamp"
            ) from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source sample timestamp is naive"
            )
        return parsed.astimezone(UTC)

    @staticmethod
    def _local_simulator_url(server: Mapping[str, Any]) -> str:
        raw_url = str(server.get("url") or "").strip().rstrip("/")
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source URL is invalid"
            ) from None

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.strip("/")
        ):
            raise HistoricalMetricsSourceError(
                "synthetic metrics source URL is invalid"
            )

        host = parsed.hostname.casefold()
        is_loopback = host == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise HistoricalMetricsSourceError(
                "synthetic metrics source must use a loopback host"
            )

        netloc = parsed.hostname
        if ":" in netloc:
            netloc = f"[{netloc}]"
        if port is not None:
            netloc = f"{netloc}:{port}"
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")
        )


__all__ = ["SyntheticHistoricalMetricsSource"]
