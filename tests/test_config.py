from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import config


def server_record(
    name: str,
    *,
    enabled=True,
    server_type: str = "prtg",
    password: str | None = "synthetic-password",
    passhash: str | None = None,
) -> dict:
    record = {
        "id": f"id-{name.lower().replace(' ', '-')}",
        "type": server_type,
        "enabled": enabled,
        "name": name,
        "url": f"http://127.0.0.1:19090/{name.lower().replace(' ', '-')}",
        "username": "synthetic-user",
    }

    if password is not None:
        record["password"] = password
    if passhash is not None:
        record["passhash"] = passhash

    return record


class ConfigurationTests(unittest.TestCase):
    def test_boolean_parsing_preserves_app_and_monitor_profiles(self) -> None:
        self.assertTrue(config.parse_bool("1"))
        self.assertTrue(config.parse_bool("TRUE"))
        self.assertTrue(config.parse_bool("y"))
        self.assertFalse(config.parse_bool("0"))
        self.assertTrue(config.parse_bool(None, True))

        self.assertFalse(
            config.parse_bool(
                "y",
                true_values=config.MONITOR_TRUE_VALUES,
            )
        )
        self.assertFalse(config.parse_config_bool("inactive"))
        self.assertTrue(config.parse_config_bool("anything-else"))

        app_settings = config.load_app_settings(
            environ={"PRTG_VERIFY_SSL": "y"}
        )
        monitor_settings = config.load_monitor_settings(
            environ={"PRTG_VERIFY_SSL": "y"}
        )
        self.assertTrue(app_settings.shared.verify_ssl)
        self.assertFalse(monitor_settings.shared.verify_ssl)

    def test_defaults_preserve_distinct_app_and_monitor_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_settings = config.load_app_settings(
                environ={},
                base_dir=root,
            )
            monitor_settings = config.load_monitor_settings(
                environ={},
                base_dir=root,
            )

        self.assertEqual(app_settings.request_timeout, 20)
        self.assertEqual(
            app_settings.secret_key,
            config.DEFAULT_APP_SECRET_KEY,
        )
        self.assertEqual(monitor_settings.request_timeout, 10)
        self.assertEqual(app_settings.shared.db_path, root / "usuarios.db")
        self.assertEqual(
            monitor_settings.shared.output_path,
            root / "estado_sensores_prtg.json",
        )
        self.assertEqual(app_settings.shared.http.retries, 3)
        self.assertEqual(app_settings.shared.http.backoff, 0.5)
        self.assertEqual(monitor_settings.update_interval, 30)
        self.assertEqual(monitor_settings.mf_sla_hours, 6)

    def test_environment_overrides_are_typed(self) -> None:
        environment = {
            "PORT": "6000",
            "APP_SECRET_KEY": "synthetic-explicit-secret",
            "REQUEST_TIMEOUT": "17",
            "HTTP_RETRIES": "5",
            "HTTP_BACKOFF": "1.25",
            "HTTP_POOL_CONNECTIONS": "30",
            "HTTP_POOL_MAXSIZE": "70",
            "PRTG_VERIFY_SSL": "yes",
            "SESSION_COOKIE_SECURE": "on",
            "MF_ALERT_STATES": "Down,Warning",
        }

        app_settings = config.load_app_settings(environ=environment)
        monitor_settings = config.load_monitor_settings(environ=environment)

        self.assertEqual(app_settings.port, 6000)
        self.assertEqual(
            app_settings.secret_key,
            "synthetic-explicit-secret",
        )
        self.assertEqual(app_settings.request_timeout, 17)
        self.assertEqual(app_settings.shared.http.retries, 5)
        self.assertEqual(app_settings.shared.http.backoff, 1.25)
        self.assertEqual(app_settings.shared.http.pool_connections, 30)
        self.assertEqual(app_settings.shared.http.pool_maxsize, 70)
        self.assertTrue(app_settings.shared.verify_ssl)
        self.assertTrue(app_settings.session_cookie_secure)
        self.assertEqual(monitor_settings.request_timeout, 17)
        self.assertEqual(
            monitor_settings.mf_alert_states,
            frozenset({"Down", "Warning"}),
        )

    def test_relative_paths_are_project_relative_and_absolute_paths_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            absolute_db = root / "absolute" / "database.db"
            settings = config.load_app_settings(
                environ={
                    "DB_PATH": str(absolute_db),
                    "OUTPUT_PATH": "runtime/state.json",
                    "PRTG_SERVERS_FILE": "runtime/servers.json",
                },
                base_dir=root,
            )

        self.assertEqual(settings.shared.db_path, absolute_db)
        self.assertEqual(
            settings.shared.output_path,
            root / "runtime" / "state.json",
        )
        self.assertEqual(
            settings.shared.servers_file,
            root / "runtime" / "servers.json",
        )

    def test_server_source_environment_precedes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_server = server_record("FILE SERVER")
            env_server = server_record("ENV SERVER")
            (root / "servers.json").write_text(
                json.dumps([file_server]),
                encoding="utf-8",
            )
            environment = {
                "PRTG_SERVERS_JSON": json.dumps([env_server]),
                "PRTG_SERVERS_FILE": "servers.json",
            }

            loaded = config.load_monitor_prtg_servers(
                environ=environment,
                base_dir=root,
                emit=None,
            )

        self.assertEqual([server["name"] for server in loaded], ["ENV SERVER"])

    def test_monitor_loader_filters_disabled_type_and_missing_secret(self) -> None:
        records = [
            server_record("ENABLED"),
            server_record("DISABLED", enabled=False),
            server_record("OTHER TYPE", server_type="zabbix"),
            server_record("NO SECRET", password=None),
            server_record(
                "PASSHASH",
                password=None,
                passhash="synthetic-passhash",
            ),
            server_record("INACTIVE STRING", enabled="inactive"),
        ]

        loaded = config.load_monitor_prtg_servers(
            environ={"PRTG_SERVERS_JSON": json.dumps(records)},
            emit=None,
        )

        self.assertEqual(
            [server["name"] for server in loaded],
            ["ENABLED", "PASSHASH", "INACTIVE STRING"],
        )
        self.assertNotIn("password", loaded[1])
        self.assertEqual(loaded[1]["passhash"], "synthetic-passhash")

    def test_app_loader_preserves_disabled_and_non_prtg_compatibility(self) -> None:
        records = [
            server_record("ENABLED"),
            server_record("DISABLED", enabled=False),
            server_record("OTHER TYPE", server_type="zabbix"),
            server_record("NO SECRET", password=None),
        ]

        loaded = config.load_app_prtg_servers(
            environ={"PRTG_SERVERS_JSON": json.dumps(records)}
        )

        self.assertEqual(
            list(loaded),
            ["ENABLED", "DISABLED", "OTHER TYPE"],
        )
        self.assertTrue(loaded["DISABLED"]["enabled"])
        self.assertEqual(loaded["OTHER TYPE"]["type"], "prtg")

    def test_password_and_passhash_are_both_valid_server_secrets(self) -> None:
        records = [
            server_record("PASSWORD"),
            server_record(
                "PASSHASH",
                password=None,
                passhash="synthetic-passhash",
            ),
        ]

        loaded = config.load_monitor_prtg_servers(
            environ={"PRTG_SERVERS_JSON": json.dumps(records)},
            emit=None,
        )

        self.assertIn("password", loaded[0])
        self.assertNotIn("passhash", loaded[0])
        self.assertNotIn("password", loaded[1])
        self.assertIn("passhash", loaded[1])

    def test_read_servers_file_adds_ids_and_defaults_in_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            servers_path = root / "servers.json"
            servers_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "PRTG LAB 01",
                            "url": "http://127.0.0.1:19090/prtg-01",
                            "username": "synthetic-user",
                            "password": "synthetic-password",
                        },
                        {
                            "id": "duplicate",
                            "name": "PRTG LAB 02",
                            "url": "http://127.0.0.1:19090/prtg-02",
                            "username": "synthetic-user",
                            "password": "synthetic-password",
                        },
                        {
                            "id": "duplicate",
                            "name": "PRTG LAB 03",
                            "url": "http://127.0.0.1:19090/prtg-03",
                            "username": "synthetic-user",
                            "password": "synthetic-password",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            environment = {"PRTG_SERVERS_FILE": str(servers_path)}

            loaded = config.read_servers_file(
                environ=environment,
                base_dir=root,
            )
            persisted = json.loads(servers_path.read_text(encoding="utf-8"))

        self.assertEqual(len(loaded), 3)
        self.assertEqual(len({server["id"] for server in loaded}), 3)
        self.assertTrue(all(server["type"] == "prtg" for server in loaded))
        self.assertTrue(all(server["enabled"] is True for server in loaded))
        self.assertEqual(persisted, loaded)

    def test_servers_json_configured_preserves_ui_truthiness(self) -> None:
        self.assertFalse(config.servers_json_is_configured(environ={}))
        self.assertFalse(
            config.servers_json_is_configured(
                environ={"PRTG_SERVERS_JSON": ""}
            )
        )
        self.assertTrue(
            config.servers_json_is_configured(
                environ={"PRTG_SERVERS_JSON": "   "}
            )
        )


if __name__ == "__main__":
    unittest.main()
