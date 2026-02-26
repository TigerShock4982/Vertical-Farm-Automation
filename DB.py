# DB.py
import os
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "farm.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        # Sensor events
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                device TEXT NOT NULL,
                seq INTEGER NOT NULL,

                air_t_c REAL,
                air_rh_pct REAL,
                air_p_hpa REAL,

                water_t_c REAL,
                water_ph REAL,
                water_ec_ms_cm REAL,

                light_lux REAL,
                level_float INTEGER,

                raw_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_events_device_seq
            ON sensor_events(device, seq);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_events_ts
            ON sensor_events(ts);
            """
        )

        # Alerts
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                device TEXT,
                severity TEXT NOT NULL,      -- INFO/WARN/CRIT
                code TEXT NOT NULL,          -- PH_HIGH, EC_LOW, WATER_LOW, etc.
                message TEXT NOT NULL,
                raw_json TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_ts
            ON alerts(ts);
            """
        )

        conn.commit()
    finally:
        conn.close()


def insert_sensor_event(event: Dict[str, Any]) -> None:
    ts = str(event.get("ts", ""))
    device = str(event.get("device", ""))
    seq = int(event.get("seq", 0))

    air = event.get("air") or {}
    water = event.get("water") or {}
    light = event.get("light") or {}
    level = event.get("level") or {}

    air_t_c = air.get("t_c")
    air_rh_pct = air.get("rh_pct")
    air_p_hpa = air.get("p_hpa")

    water_t_c = water.get("t_c")
    water_ph = water.get("ph")
    water_ec = water.get("ec_ms_cm")

    light_lux = light.get("lux")
    level_float = level.get("float")

    raw_json = json.dumps(event, separators=(",", ":"), ensure_ascii=False)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO sensor_events (
                ts, device, seq,
                air_t_c, air_rh_pct, air_p_hpa,
                water_t_c, water_ph, water_ec_ms_cm,
                light_lux, level_float,
                raw_json
            ) VALUES (?, ?, ?,
                      ?, ?, ?,
                      ?, ?, ?,
                      ?, ?,
                      ?);
            """,
            (
                ts, device, seq,
                air_t_c, air_rh_pct, air_p_hpa,
                water_t_c, water_ph, water_ec,
                light_lux, int(level_float) if level_float is not None else None,
                raw_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_sensor_event() -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT raw_json
            FROM sensor_events
            ORDER BY id DESC
            LIMIT 1;
            """
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["raw_json"])
    finally:
        conn.close()


def get_sensor_history(limit: int = 500) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT raw_json
            FROM sensor_events
            ORDER BY id DESC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]
    finally:
        conn.close()


def insert_alert(ts: str, device: Optional[str], severity: str, code: str, message: str, raw: Optional[Dict[str, Any]] = None) -> None:
    raw_json = json.dumps(raw, separators=(",", ":"), ensure_ascii=False) if raw is not None else None
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO alerts (ts, device, severity, code, message, raw_json)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (ts, device, severity, code, message, raw_json),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, ts, device, severity, code, message
            FROM alerts
            ORDER BY id DESC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()