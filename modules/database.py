"""SQLite storage for thermostat readings, weather, commands, and alerts."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "facility.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS thermostat_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    zone_id TEXT NOT NULL,
    indoor_temp REAL,
    indoor_humidity REAL,
    cool_setpoint REAL,
    heat_setpoint REAL,
    mode TEXT,
    fan_mode TEXT,
    is_heating INTEGER,
    is_cooling INTEGER
);
CREATE INDEX IF NOT EXISTS idx_thermostat_zone_time
    ON thermostat_readings(zone_id, timestamp);

CREATE TABLE IF NOT EXISTS weather_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    outdoor_temp REAL,
    outdoor_humidity REAL,
    dewpoint REAL,
    wind_speed REAL,
    cloud_cover REAL,
    precipitation REAL
);
CREATE INDEX IF NOT EXISTS idx_weather_time ON weather_readings(timestamp);

CREATE TABLE IF NOT EXISTS commands_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    system TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    parameters TEXT,
    success INTEGER,
    shadow_mode INTEGER,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_time ON commands_log(timestamp);

CREATE TABLE IF NOT EXISTS alerts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT,
    message TEXT NOT NULL,
    recipient TEXT,
    sent INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts_log(timestamp);

CREATE TABLE IF NOT EXISTS poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    events_count INTEGER,
    errors TEXT
);
CREATE INDEX IF NOT EXISTS idx_poll_time ON poll_log(timestamp);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def record_thermostat_reading(zone_id: str, status: dict, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO thermostat_readings
               (timestamp, zone_id, indoor_temp, indoor_humidity,
                cool_setpoint, heat_setpoint, mode, fan_mode,
                is_heating, is_cooling)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(),
                zone_id,
                status.get("indoor_temp"),
                status.get("indoor_humidity"),
                status.get("cool_setpoint"),
                status.get("heat_setpoint"),
                status.get("mode"),
                status.get("fan_mode"),
                1 if status.get("is_heating") else 0,
                1 if status.get("is_cooling") else 0,
            ),
        )


def record_weather_reading(reading: dict, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO weather_readings
               (timestamp, outdoor_temp, outdoor_humidity, dewpoint,
                wind_speed, cloud_cover, precipitation)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(),
                reading.get("outdoor_temp"),
                reading.get("outdoor_humidity"),
                reading.get("dewpoint"),
                reading.get("wind_speed"),
                reading.get("cloud_cover"),
                reading.get("precipitation"),
            ),
        )


def record_command(
    system: str,
    target_id: str,
    action: str,
    parameters: dict,
    success: bool,
    shadow_mode: bool,
    error_message: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO commands_log
               (timestamp, system, target_id, action, parameters,
                success, shadow_mode, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(),
                system,
                target_id,
                action,
                json.dumps(parameters),
                1 if success else 0,
                1 if shadow_mode else 0,
                error_message,
            ),
        )


def record_alert(
    alert_type: str,
    severity: str,
    message: str,
    recipient: str,
    sent: bool,
    db_path: Path = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO alerts_log
               (timestamp, alert_type, severity, message, recipient, sent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_now_iso(), alert_type, severity, message, recipient, 1 if sent else 0),
        )


def record_poll(duration_ms: int, events_count: int, errors: list[str] | None = None,
                db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO poll_log (timestamp, duration_ms, events_count, errors)
               VALUES (?, ?, ?, ?)""",
            (_now_iso(), duration_ms, events_count,
             json.dumps(errors) if errors else None),
        )


# ---------- read helpers ----------


def last_successful_command(system: str, target_id: str,
                             db_path: Path = DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT action, parameters FROM commands_log
               WHERE system = ? AND target_id = ? AND success = 1
               ORDER BY id DESC LIMIT 1""",
            (system, target_id),
        ).fetchone()
    if not row:
        return None
    return {
        "action": row["action"],
        "parameters": json.loads(row["parameters"] or "{}"),
    }


def latest_thermostat_readings(db_path: Path = DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT t.* FROM thermostat_readings t
               JOIN (SELECT zone_id, MAX(id) AS max_id
                     FROM thermostat_readings GROUP BY zone_id) latest
                 ON t.id = latest.max_id
               ORDER BY t.zone_id"""
        ).fetchall()
    return [dict(r) for r in rows]


def latest_weather(db_path: Path = DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM weather_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def recent_commands(limit: int = 50, db_path: Path = DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM commands_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def recent_alerts(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM alerts_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def latest_poll(db_path: Path = DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM poll_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
