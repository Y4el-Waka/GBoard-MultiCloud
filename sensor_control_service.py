from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from history_service import HistoryService
from prtg_client import PRTGClient, PRTGClientError


class SensorControlError(Exception):
    """Base error for sensor-control operations."""


class SensorServerNotFoundError(SensorControlError):
    """The requested PRTG server is not configured."""


class SensorControlValidationError(SensorControlError):
    """A sensor-control parameter is invalid."""


class SensorControlUpstreamError(SensorControlError):
    """The upstream PRTG-compatible API rejected a control operation."""


ServerMap = Mapping[str, Mapping[str, Any]]
ServerProvider = Callable[[], ServerMap]
ClientFactory = Callable[[], PRTGClient]


class SensorControlService:
    """Application service for PRTG pause and resume operations."""

    MIN_PAUSE_MINUTES = 1
    MAX_PAUSE_MINUTES = 365 * 24 * 60

    def __init__(
        self,
        *,
        server_provider: ServerProvider,
        client_factory: ClientFactory,
        history_service: HistoryService,
        server_reloader: ServerProvider | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._server_provider = server_provider
        self._server_reloader = server_reloader
        self._client_factory = client_factory
        self._history_service = history_service
        self._logger = logger or logging.getLogger(__name__)

    def pause_for(
        self,
        server_identifier: str,
        sensor_id: int,
        minutes: Any,
        message: str,
    ) -> int:
        identifier, clean_sensor_id = self._validate_target(
            server_identifier,
            sensor_id,
        )
        duration_minutes = self._normalize_minutes(minutes)
        server = self._lookup_server(identifier)

        try:
            with self._client_factory() as client:
                client.pause_for(
                    server,
                    clean_sensor_id,
                    duration_minutes,
                    message or "Pausado desde dashboard",
                )
        except PRTGClientError:
            self._log_upstream_failure("pause_for", identifier, clean_sensor_id)
            raise SensorControlUpstreamError(
                "No se pudo pausar el sensor en PRTG"
            ) from None

        self._history_service.clear()
        return duration_minutes

    def pause_indefinitely(
        self,
        server_identifier: str,
        sensor_id: int,
        message: str,
    ) -> None:
        identifier, clean_sensor_id = self._validate_target(
            server_identifier,
            sensor_id,
        )
        server = self._lookup_server(identifier)

        try:
            with self._client_factory() as client:
                client.pause_indefinitely(
                    server,
                    clean_sensor_id,
                    message or "Pausado indefinidamente desde dashboard",
                )
        except PRTGClientError:
            self._log_upstream_failure(
                "pause_indefinitely",
                identifier,
                clean_sensor_id,
            )
            raise SensorControlUpstreamError(
                "No se pudo pausar el sensor en PRTG"
            ) from None

        self._history_service.clear()

    def resume(
        self,
        server_identifier: str,
        sensor_id: int,
    ) -> None:
        identifier, clean_sensor_id = self._validate_target(
            server_identifier,
            sensor_id,
        )
        server = self._lookup_server(identifier)

        try:
            with self._client_factory() as client:
                client.resume(server, clean_sensor_id)
        except PRTGClientError:
            self._log_upstream_failure("resume", identifier, clean_sensor_id)
            raise SensorControlUpstreamError(
                "No se pudo reanudar el sensor en PRTG"
            ) from None

        self._history_service.clear()

    def _lookup_server(self, identifier: str) -> Mapping[str, Any]:
        server = self._server_provider().get(identifier)

        if server is None and self._server_reloader is not None:
            server = self._server_reloader().get(identifier)

        if server is None:
            raise SensorServerNotFoundError(
                f"Grupo '{identifier}' no encontrado"
            )

        return server

    def _validate_target(
        self,
        server_identifier: str,
        sensor_id: int,
    ) -> tuple[str, int]:
        identifier = str(server_identifier or "").strip()

        try:
            clean_sensor_id = int(sensor_id)
        except (TypeError, ValueError) as exc:
            raise SensorControlValidationError("id debe ser numerico") from exc

        if not identifier or not clean_sensor_id:
            raise SensorControlValidationError(
                "group e id son obligatorios"
            )

        return identifier, clean_sensor_id

    def _normalize_minutes(self, minutes: Any) -> int:
        try:
            duration_minutes = int(minutes)
        except (TypeError, ValueError) as exc:
            raise SensorControlValidationError(
                "duration_minutes debe ser numerico"
            ) from exc

        return max(
            self.MIN_PAUSE_MINUTES,
            min(duration_minutes, self.MAX_PAUSE_MINUTES),
        )

    def _log_upstream_failure(
        self,
        operation: str,
        identifier: str,
        sensor_id: int,
    ) -> None:
        self._logger.warning(
            "Error de control PRTG operation=%s group=%s sensor=%s",
            operation,
            identifier,
            sensor_id,
        )
