from __future__ import annotations

import logging
import re
import warnings
from datetime import datetime
from typing import Any, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

from config import HTTPSettings


class PRTGClientError(Exception):
    """Base error for sanitized PRTG transport failures."""


class PRTGConnectionError(PRTGClientError):
    """The PRTG endpoint could not be reached or timed out."""


class PRTGAuthenticationError(PRTGClientError):
    """Credentials are missing or the endpoint rejected authentication."""


class PRTGResponseError(PRTGClientError):
    """PRTG returned an HTTP error or an invalid response."""


class PRTGClient:
    """Small transport client for PRTG-compatible HTTP APIs.

    Each instance owns exactly one requests.Session and is intended to stay
    inside one request or worker. Consumers should use the client as a context
    manager; this prevents a Session from being shared across threads and gives
    its connection pool a deterministic lifecycle.
    """

    RETRY_STATUS_CODES = (429, 500, 502, 503, 504)

    def __init__(
        self,
        *,
        timeout: int | float,
        verify_ssl: bool,
        http_settings: HTTPSettings,
        logger: logging.Logger | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.http_settings = http_settings
        self.logger = logger
        self._owns_session = session is None
        self._session = session or self._build_session()
        self._closed = False

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retries = Retry(
            total=self.http_settings.retries,
            backoff_factor=self.http_settings.backoff,
            status_forcelist=self.RETRY_STATUS_CODES,
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=self.http_settings.pool_connections,
            pool_maxsize=self.http_settings.pool_maxsize,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    @staticmethod
    def _server_name(server: Mapping[str, Any]) -> str:
        raw_name = str(server.get("name") or "unnamed")
        return re.sub(r"[^A-Za-z0-9_. -]", "_", raw_name)[:120]

    @staticmethod
    def _auth_params(server: Mapping[str, Any]) -> dict[str, Any]:
        username = server.get("username")
        passhash = server.get("passhash")
        password = server.get("password")

        if not username or not (passhash or password):
            raise PRTGAuthenticationError(
                "PRTG credentials are incomplete"
            )

        params: dict[str, Any] = {"username": username}

        if passhash:
            params["passhash"] = passhash
        else:
            params["password"] = password

        return params

    @staticmethod
    def _format_history_time(value: datetime | str) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d-%H-%M-%S")
        return str(value)

    def _log_failure(
        self,
        server: Mapping[str, Any],
        operation: str,
        reason: str,
    ) -> None:
        if self.logger is not None:
            self.logger.warning(
                "PRTG %s for server=%s operation=%s",
                reason,
                self._server_name(server),
                operation,
            )

    def _request(
        self,
        server: Mapping[str, Any],
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        operation: str,
        allow_redirects: bool | None = None,
    ) -> requests.Response:
        if self._closed:
            raise PRTGClientError("PRTG client is closed")

        base_url = str(server.get("url") or "").strip().rstrip("/")
        if not base_url:
            raise PRTGClientError("PRTG server URL is missing")

        request_params = self._auth_params(server)
        if params:
            request_params.update(params)

        try:
            with warnings.catch_warnings():
                if not self.verify_ssl:
                    warnings.simplefilter("ignore", InsecureRequestWarning)

                request_options = {
                    "params": request_params,
                    "timeout": self.timeout,
                    "verify": self.verify_ssl,
                }
                if allow_redirects is not None:
                    request_options["allow_redirects"] = allow_redirects

                response = self._session.get(
                    f"{base_url}{endpoint}",
                    **request_options,
                )
        except requests.Timeout:
            self._log_failure(server, operation, "timeout")
            raise PRTGConnectionError(
                f"PRTG request timed out for server '{self._server_name(server)}'"
            ) from None
        except requests.ConnectionError:
            self._log_failure(server, operation, "connection failure")
            raise PRTGConnectionError(
                f"Could not connect to PRTG server '{self._server_name(server)}'"
            ) from None
        except requests.RequestException:
            self._log_failure(server, operation, "request failure")
            raise PRTGClientError(
                f"PRTG request failed for server '{self._server_name(server)}'"
            ) from None

        try:
            response.raise_for_status()
        except requests.HTTPError:
            status_code = getattr(response, "status_code", None)
            if status_code in {401, 403}:
                self._log_failure(server, operation, "authentication failure")
                raise PRTGAuthenticationError(
                    f"PRTG authentication failed for server "
                    f"'{self._server_name(server)}'"
                ) from None

            self._log_failure(server, operation, "HTTP response failure")
            status_label = (
                str(status_code)
                if isinstance(status_code, int)
                else "unknown"
            )
            raise PRTGResponseError(
                f"PRTG returned HTTP {status_label} for server "
                f"'{self._server_name(server)}'"
            ) from None

        return response

    def _get_json_object(
        self,
        server: Mapping[str, Any],
        endpoint: str,
        *,
        params: Mapping[str, Any],
        operation: str,
        allow_redirects: bool | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            server,
            endpoint,
            params=params,
            operation=operation,
            allow_redirects=allow_redirects,
        )

        try:
            payload = response.json()
        except (TypeError, ValueError):
            self._log_failure(server, operation, "invalid JSON response")
            raise PRTGResponseError(
                f"PRTG returned invalid JSON for server "
                f"'{self._server_name(server)}'"
            ) from None

        if not isinstance(payload, dict):
            self._log_failure(server, operation, "invalid JSON object")
            raise PRTGResponseError(
                f"PRTG response is not a JSON object for server "
                f"'{self._server_name(server)}'"
            )

        return payload

    def get_devices(
        self,
        server: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._get_json_object(
            server,
            "/api/table.json",
            params={
                "content": "devices",
                "columns": "objid,host",
            },
            operation="get_devices",
        )

    def get_sensors(
        self,
        server: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._get_json_object(
            server,
            "/api/table.json",
            params={
                "content": "sensors",
                "columns": (
                    "device,sensor,status,status_raw,lastvalue,message,"
                    "parentid,objid"
                ),
            },
            operation="get_sensors",
        )

    def get_sensor_metadata(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> dict[str, Any]:
        """Return one sensor's read-only table metadata for discovery."""
        return self._get_json_object(
            server,
            "/api/table.json",
            params={
                "content": "sensors",
                "columns": (
                    "objid,parentid,probe,group,device,sensor,type,tags,"
                    "interval,status,lastvalue,lastcheck,channel"
                ),
                "count": 1,
                "filter_objid": sensor_id,
            },
            operation="get_sensor_metadata",
            allow_redirects=False,
        )

    def get_channels_csv(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> str:
        """Return channel rows without JSON's volume/speed key collision."""
        response = self._request(
            server,
            "/api/table.csv",
            params={
                "id": sensor_id,
                "content": "channels",
                "columns": "objid,name,lastvalue_",
                "count": 500,
            },
            operation="get_channels_csv",
            allow_redirects=False,
        )
        text = response.text
        if not isinstance(text, str):
            self._log_failure(server, "get_channels_csv", "invalid text response")
            raise PRTGResponseError(
                f"PRTG returned invalid text for server "
                f"'{self._server_name(server)}'"
            )
        return text

    def get_historic_csv(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        start: datetime | str,
        end: datetime | str,
        *,
        average_seconds: int,
        allow_redirects: bool | None = None,
    ) -> str:
        response = self._request(
            server,
            "/api/historicdata.csv",
            params={
                "id": sensor_id,
                "sdate": self._format_history_time(start),
                "edate": self._format_history_time(end),
                "avg": average_seconds,
                "usecaption": 1,
            },
            operation="get_historic_csv",
            allow_redirects=allow_redirects,
        )
        text = response.text

        if not isinstance(text, str):
            self._log_failure(server, "get_historic_csv", "invalid text response")
            raise PRTGResponseError(
                f"PRTG returned invalid text for server "
                f"'{self._server_name(server)}'"
            )

        return text

    def pause_for(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        minutes: int,
        message: str,
    ) -> None:
        self._request(
            server,
            "/api/pauseobjectfor.htm",
            params={
                "id": sensor_id,
                "duration": minutes,
                "pausemsg": message,
            },
            operation="pause_for",
        )

    def pause_indefinitely(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
        message: str,
    ) -> None:
        self._request(
            server,
            "/api/pause.htm",
            params={
                "id": sensor_id,
                "action": 0,
                "pausemsg": message,
            },
            operation="pause_indefinitely",
        )

    def resume(
        self,
        server: Mapping[str, Any],
        sensor_id: int | str,
    ) -> None:
        self._request(
            server,
            "/api/pause.htm",
            params={
                "id": sensor_id,
                "action": 1,
            },
            operation="resume",
        )

    def close(self) -> None:
        if self._closed:
            return

        if self._owns_session:
            self._session.close()

        self._closed = True

    def __enter__(self) -> PRTGClient:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
