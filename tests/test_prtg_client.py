from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

import requests

import config
import prtg_client
from prtg_client import (
    PRTGAuthenticationError,
    PRTGClient,
    PRTGConnectionError,
    PRTGResponseError,
)


class FakeResponse:
    def __init__(
        self,
        *,
        payload=None,
        text: str = "",
        status_code: int = 200,
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.text = text
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                "upstream URL contained synthetic-secret"
            )


class RecordingSession:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse(payload={})
        self.error: Exception | None = None
        self.calls: list[tuple[str, dict]] = []
        self.adapters = {}
        self.closed = False

    def mount(self, prefix, adapter) -> None:
        self.adapters[prefix] = adapter

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def close(self) -> None:
        self.closed = True


class PRTGClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.http_settings = config.HTTPSettings(
            retries=3,
            backoff=0.5,
            pool_connections=20,
            pool_maxsize=50,
        )
        self.password_server = {
            "name": "PRTG LAB 01",
            "url": "http://127.0.0.1:19090/prtg-01",
            "username": "synthetic-user",
            "password": "synthetic-password",
        }
        self.passhash_server = {
            "name": "PRTG LAB 02",
            "url": "http://127.0.0.1:19090/prtg-02",
            "username": "synthetic-user",
            "passhash": "synthetic-passhash",
        }

    def client(self, session, *, timeout=10, verify_ssl=False, logger=None):
        return PRTGClient(
            timeout=timeout,
            verify_ssl=verify_ssl,
            http_settings=self.http_settings,
            logger=logger,
            session=session,
        )

    def test_password_auth(self) -> None:
        session = RecordingSession(FakeResponse(payload={"sensors": []}))
        self.client(session).get_sensors(self.password_server)
        params = session.calls[0][1]["params"]

        self.assertEqual(params["username"], "synthetic-user")
        self.assertEqual(params["password"], "synthetic-password")
        self.assertNotIn("passhash", params)

    def test_passhash_auth_takes_precedence(self) -> None:
        server = {
            **self.password_server,
            "passhash": "synthetic-passhash",
        }
        session = RecordingSession(FakeResponse(payload={"sensors": []}))
        self.client(session).get_sensors(server)
        params = session.calls[0][1]["params"]

        self.assertEqual(params["passhash"], "synthetic-passhash")
        self.assertNotIn("password", params)

    def test_app_and_monitor_timeouts_are_preserved(self) -> None:
        app_settings = config.load_app_settings(environ={})
        monitor_settings = config.load_monitor_settings(environ={})
        app_session = RecordingSession(FakeResponse(payload={"sensors": []}))
        monitor_session = RecordingSession(
            FakeResponse(payload={"sensors": []})
        )

        self.client(
            app_session,
            timeout=app_settings.request_timeout,
        ).get_sensors(self.password_server)
        self.client(
            monitor_session,
            timeout=monitor_settings.request_timeout,
        ).get_sensors(self.password_server)

        self.assertEqual(app_session.calls[0][1]["timeout"], 20)
        self.assertEqual(monitor_session.calls[0][1]["timeout"], 10)

    def test_ssl_verification_is_forwarded(self) -> None:
        false_session = RecordingSession(FakeResponse(payload={"devices": []}))
        true_session = RecordingSession(FakeResponse(payload={"devices": []}))

        self.client(false_session, verify_ssl=False).get_devices(
            self.password_server
        )
        self.client(true_session, verify_ssl=True).get_devices(
            self.password_server
        )

        self.assertFalse(false_session.calls[0][1]["verify"])
        self.assertTrue(true_session.calls[0][1]["verify"])

    def test_retries_backoff_and_connection_pools_are_configured(self) -> None:
        session = RecordingSession()

        with mock.patch.object(
            prtg_client.requests,
            "Session",
            return_value=session,
        ):
            client = PRTGClient(
                timeout=10,
                verify_ssl=False,
                http_settings=self.http_settings,
            )

        adapter = session.adapters["https://"]
        self.assertIs(adapter, session.adapters["http://"])
        self.assertEqual(adapter.max_retries.total, 3)
        self.assertEqual(adapter.max_retries.backoff_factor, 0.5)
        self.assertEqual(adapter._pool_connections, 20)
        self.assertEqual(adapter._pool_maxsize, 50)
        client.close()
        self.assertTrue(session.closed)

    def test_devices_request(self) -> None:
        session = RecordingSession(
            FakeResponse(payload={"devices": [{"objid": 1, "host": "192.0.2.10"}]})
        )
        payload = self.client(session).get_devices(self.password_server)
        url, options = session.calls[0]

        self.assertEqual(payload["devices"][0]["host"], "192.0.2.10")
        self.assertTrue(url.endswith("/api/table.json"))
        self.assertEqual(options["params"]["content"], "devices")
        self.assertEqual(options["params"]["columns"], "objid,host")

    def test_sensors_request(self) -> None:
        session = RecordingSession(
            FakeResponse(payload={"sensors": [{"objid": 100001}]})
        )
        payload = self.client(session).get_sensors(self.password_server)
        url, options = session.calls[0]

        self.assertEqual(payload["sensors"][0]["objid"], 100001)
        self.assertTrue(url.endswith("/api/table.json"))
        self.assertEqual(options["params"]["content"], "sensors")
        self.assertIn("lastvalue", options["params"]["columns"])
        self.assertNotIn("allow_redirects", options)

    def test_read_only_discovery_requests_disable_redirects(self) -> None:
        metadata_session = RecordingSession(
            FakeResponse(payload={"sensors": [{"objid": 100001}]})
        )
        channels_session = RecordingSession(
            FakeResponse(text='"ID";"Name";"Last Value"\n1;"Ping Time";"10 ms"\n')
        )

        metadata = self.client(metadata_session).get_sensor_metadata(
            self.password_server,
            100001,
        )
        channels = self.client(channels_session).get_channels_csv(
            self.password_server,
            100001,
        )

        metadata_url, metadata_options = metadata_session.calls[0]
        channels_url, channels_options = channels_session.calls[0]
        self.assertEqual(metadata["sensors"][0]["objid"], 100001)
        self.assertIn("Ping Time", channels)
        self.assertTrue(metadata_url.endswith("/api/table.json"))
        self.assertEqual(metadata_options["params"]["content"], "sensors")
        self.assertEqual(metadata_options["params"]["filter_objid"], 100001)
        self.assertEqual(metadata_options["params"]["count"], 1)
        self.assertFalse(metadata_options["allow_redirects"])
        self.assertTrue(channels_url.endswith("/api/table.csv"))
        self.assertEqual(channels_options["params"]["content"], "channels")
        self.assertEqual(channels_options["params"]["id"], 100001)
        self.assertFalse(channels_options["allow_redirects"])

    def test_historic_request_returns_source_csv(self) -> None:
        csv_text = "Date Time;Ping Time\n2026-09-23 12:00:00;10 ms\n"
        session = RecordingSession(FakeResponse(text=csv_text))
        text = self.client(session).get_historic_csv(
            self.password_server,
            100001,
            datetime(2026, 9, 23, 11, 0, 0),
            datetime(2026, 9, 23, 12, 0, 0),
            average_seconds=300,
        )
        url, options = session.calls[0]

        self.assertEqual(text, csv_text)
        self.assertTrue(url.endswith("/api/historicdata.csv"))
        self.assertEqual(options["params"]["id"], 100001)
        self.assertEqual(options["params"]["sdate"], "2026-09-23-11-00-00")
        self.assertEqual(options["params"]["edate"], "2026-09-23-12-00-00")
        self.assertEqual(options["params"]["avg"], 300)
        self.assertEqual(options["params"]["usecaption"], 1)
        self.assertNotIn("allow_redirects", options)

    def test_historic_request_can_disable_redirects_for_discovery(self) -> None:
        session = RecordingSession(FakeResponse(text="Date Time;Ping Time\n"))

        self.client(session).get_historic_csv(
            self.password_server,
            100001,
            "2026-09-23-11-00-00",
            "2026-09-23-12-00-00",
            average_seconds=300,
            allow_redirects=False,
        )

        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_pause_temporary_indefinite_and_resume(self) -> None:
        session = RecordingSession(FakeResponse())
        client = self.client(session)

        client.pause_for(
            self.password_server,
            100001,
            60,
            "Synthetic timed pause",
        )
        client.pause_indefinitely(
            self.password_server,
            100001,
            "Synthetic indefinite pause",
        )
        client.resume(self.password_server, 100001)

        timed_url, timed = session.calls[0]
        indefinite_url, indefinite = session.calls[1]
        resume_url, resume = session.calls[2]
        self.assertTrue(timed_url.endswith("/api/pauseobjectfor.htm"))
        self.assertEqual(timed["params"]["duration"], 60)
        self.assertTrue(indefinite_url.endswith("/api/pause.htm"))
        self.assertEqual(indefinite["params"]["action"], 0)
        self.assertEqual(indefinite["params"]["pausemsg"], "Synthetic indefinite pause")
        self.assertTrue(resume_url.endswith("/api/pause.htm"))
        self.assertEqual(resume["params"]["action"], 1)

    def test_http_and_authentication_errors_are_typed(self) -> None:
        auth_session = RecordingSession(FakeResponse(status_code=401))
        http_session = RecordingSession(FakeResponse(status_code=503))

        with self.assertRaises(PRTGAuthenticationError):
            self.client(auth_session).get_sensors(self.password_server)

        with self.assertRaises(PRTGResponseError):
            self.client(http_session).get_sensors(self.password_server)

    def test_timeout_and_connection_failures_are_typed(self) -> None:
        timeout_session = RecordingSession()
        timeout_session.error = requests.Timeout(
            "synthetic-password appeared upstream"
        )
        connection_session = RecordingSession()
        connection_session.error = requests.ConnectionError(
            "synthetic-passhash appeared upstream"
        )

        with self.assertRaises(PRTGConnectionError):
            self.client(timeout_session).get_sensors(self.password_server)

        with self.assertRaises(PRTGConnectionError):
            self.client(connection_session).get_sensors(self.passhash_server)

    def test_invalid_response_is_typed(self) -> None:
        invalid_json = RecordingSession(
            FakeResponse(json_error=ValueError("not json"))
        )
        invalid_object = RecordingSession(FakeResponse(payload=[]))

        with self.assertRaises(PRTGResponseError):
            self.client(invalid_json).get_sensors(self.password_server)

        with self.assertRaises(PRTGResponseError):
            self.client(invalid_object).get_sensors(self.password_server)

    def test_secrets_do_not_leak_in_errors_or_logs(self) -> None:
        logger = mock.Mock()
        session = RecordingSession(FakeResponse(status_code=401))
        server = {
            **self.password_server,
            "url": "https://example.test?password=do-not-leak-url-secret",
            "password": "do-not-leak-password",
            "passhash": "do-not-leak-passhash",
        }

        with self.assertRaises(PRTGAuthenticationError) as captured:
            self.client(session, logger=logger).get_sensors(server)

        emitted = " ".join(
            str(value)
            for call in logger.warning.call_args_list
            for value in (*call.args, *call.kwargs.values())
        )
        combined = f"{captured.exception} {emitted}"
        self.assertNotIn("synthetic-secret", combined)
        self.assertNotIn("do-not-leak-password", combined)
        self.assertNotIn("do-not-leak-passhash", combined)
        self.assertNotIn("do-not-leak-url-secret", combined)
        self.assertNotIn("username=", combined)

    def test_each_instance_owns_a_distinct_session(self) -> None:
        first_session = RecordingSession()
        second_session = RecordingSession()

        with mock.patch.object(
            prtg_client.requests,
            "Session",
            side_effect=[first_session, second_session],
        ):
            first = PRTGClient(
                timeout=10,
                verify_ssl=False,
                http_settings=self.http_settings,
            )
            second = PRTGClient(
                timeout=10,
                verify_ssl=False,
                http_settings=self.http_settings,
            )

        self.assertIsNot(first._session, second._session)
        first.close()
        second.close()
        self.assertTrue(first_session.closed)
        self.assertTrue(second_session.closed)


if __name__ == "__main__":
    unittest.main()
