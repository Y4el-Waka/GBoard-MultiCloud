from __future__ import annotations

import sqlite3


_MONITORING_SCHEMA_STATEMENTS = (
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sensor_events_created_at
    ON sensor_events(created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sensor_events_sensor
    ON sensor_events(group_name, sensor_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sensor_events_type
    ON sensor_events(event_type)
    """,
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sensor_state_last_seen
    ON sensor_state(last_seen_at)
    """,
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
    """,
)


def ensure_monitoring_schema(connection: sqlite3.Connection) -> None:
    """Create the monitoring tables shared by Flask and the collector."""
    for statement in _MONITORING_SCHEMA_STATEMENTS:
        connection.execute(statement)
