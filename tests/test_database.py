from __future__ import annotations

import sqlite3
import unittest

from database import ensure_monitoring_schema


class MonitoringSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")

    def tearDown(self) -> None:
        self.connection.close()

    def test_shared_monitoring_schema_preserves_tables_columns_and_indexes(self) -> None:
        ensure_monitoring_schema(self.connection)

        expected_columns = {
            "sensor_events": [
                "id",
                "group_name",
                "sensor_id",
                "device_name",
                "sensor_name",
                "previous_status",
                "new_status",
                "event_type",
                "created_at",
            ],
            "sensor_state": [
                "group_name",
                "sensor_id",
                "device_name",
                "sensor_name",
                "status",
                "last_seen_at",
                "updated_at",
            ],
            "mf_alert_state": [
                "group_name",
                "sensor_id",
                "device_name",
                "sensor_name",
                "alert_started_at",
                "deadline_at",
                "last_status",
                "updated_at",
            ],
        }

        for table_name, columns in expected_columns.items():
            with self.subTest(table=table_name):
                rows = self.connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
                self.assertEqual([row[1] for row in rows], columns)

        indexes = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "idx_sensor_events_created_at",
                "idx_sensor_events_sensor",
                "idx_sensor_events_type",
                "idx_sensor_state_last_seen",
            }.issubset(indexes)
        )

    def test_shared_monitoring_schema_is_idempotent(self) -> None:
        ensure_monitoring_schema(self.connection)
        ensure_monitoring_schema(self.connection)

        self.connection.execute(
            """
            INSERT INTO sensor_state(
                group_name,
                sensor_id,
                status,
                last_seen_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "PRTG LAB 01",
                "100001",
                "Up",
                "2026-09-23 12:00:00",
                "2026-09-23 12:00:00",
            ),
        )

        count = self.connection.execute(
            "SELECT COUNT(*) FROM sensor_state"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
