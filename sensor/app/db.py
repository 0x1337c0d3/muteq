import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create tables and indexes if they don't exist. Call once at startup."""
    conn = _connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                noise_value REAL NOT NULL,
                peak_value  REAL
            );
            CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (timestamp);

            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                noise_value REAL NOT NULL,
                peak_value  REAL
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events (timestamp);
        """)
    finally:
        conn.close()


def write_reading(db_path: str, timestamp_iso: str, noise_value: float, peak_value: float) -> None:
    """Insert one SPL reading. Called every 2 seconds."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO readings (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
            (timestamp_iso, noise_value, peak_value),
        )
    finally:
        conn.close()


def write_event(db_path: str, timestamp_iso: str, noise_value: float, peak_value: float) -> None:
    """Insert a threshold-breach event (noise >= MINIMUM_NOISE_LEVEL)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO events (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
            (timestamp_iso, noise_value, peak_value),
        )
    finally:
        conn.close()


def query_readings(db_path: str, since_iso: str, limit: int) -> List[Dict[str, Any]]:
    """Return readings since `since_iso`, oldest first, capped at `limit`."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT timestamp, noise_value, peak_value FROM readings "
            "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_events(db_path: str, since_iso: str, limit: int) -> List[Dict[str, Any]]:
    """Return events since `since_iso`, newest first, capped at `limit`."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT timestamp, noise_value, peak_value FROM events "
            "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prune_old_data(db_path: str, retain_days: int = 35) -> None:
    """Delete readings and events older than `retain_days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    finally:
        conn.close()
