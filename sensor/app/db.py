import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import duckdb

# One persistent connection per db_path, shared across threads with a reentrant lock.
_connections: dict[str, duckdb.DuckDBPyConnection] = {}
_db_lock = threading.RLock()


def _conn(db_path: str) -> duckdb.DuckDBPyConnection:
    """Return (or create) the persistent DuckDB connection for db_path.
    Must be called while holding _db_lock."""
    if db_path not in _connections:
        abs_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        _connections[db_path] = duckdb.connect(abs_path)
    return _connections[db_path]


def init_db(db_path: str) -> None:
    """Create tables and indexes if they don't exist. Call once at startup."""
    with _db_lock:
        c = _conn(db_path)
        c.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                timestamp   VARCHAR NOT NULL,
                noise_value DOUBLE  NOT NULL,
                peak_value  DOUBLE
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (timestamp)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                timestamp   VARCHAR NOT NULL,
                noise_value DOUBLE  NOT NULL,
                peak_value  DOUBLE
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events (timestamp)")


def write_reading(db_path: str, timestamp_iso: str, noise_value: float, peak_value: float) -> None:
    """Insert one SPL reading. Called every ~0.5 s."""
    with _db_lock:
        _conn(db_path).execute(
            "INSERT INTO readings (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
            [timestamp_iso, noise_value, peak_value],
        )


def write_event(db_path: str, timestamp_iso: str, noise_value: float, peak_value: float) -> None:
    """Insert a threshold-breach event (noise >= MINIMUM_NOISE_LEVEL)."""
    with _db_lock:
        _conn(db_path).execute(
            "INSERT INTO events (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
            [timestamp_iso, noise_value, peak_value],
        )


def query_readings(db_path: str, since_iso: str, limit: int) -> List[Dict[str, Any]]:
    """Return readings since `since_iso`, oldest first, capped at `limit`."""
    with _db_lock:
        rows = _conn(db_path).execute(
            "SELECT timestamp, noise_value, peak_value FROM readings "
            "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
            [since_iso, limit],
        ).fetchall()
    return [{"timestamp": r[0], "noise_value": r[1], "peak_value": r[2]} for r in rows]


def query_events(db_path: str, since_iso: str, limit: int) -> List[Dict[str, Any]]:
    """Return events since `since_iso`, newest first, capped at `limit`."""
    with _db_lock:
        rows = _conn(db_path).execute(
            "SELECT timestamp, noise_value, peak_value FROM events "
            "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            [since_iso, limit],
        ).fetchall()
    return [{"timestamp": r[0], "noise_value": r[1], "peak_value": r[2]} for r in rows]


def query_hourly_event_counts(db_path: str, since_iso: str) -> list[dict]:
    """Return event counts grouped by UTC hour-of-day (0-23) since `since_iso`."""
    with _db_lock:
        rows = _conn(db_path).execute(
            # timestamp is stored as ISO string 'YYYY-MM-DDTHH:MM:SS+00:00'
            # characters 12-13 (0-indexed 11-12) are the UTC hour
            "SELECT CAST(SUBSTRING(timestamp, 12, 2) AS INTEGER) AS hour, COUNT(*) AS count "
            "FROM events WHERE timestamp >= ? GROUP BY hour ORDER BY hour",
            [since_iso],
        ).fetchall()
    return [{"hour": r[0], "count": r[1]} for r in rows]


def query_daily_stats(db_path: str, since_iso: str) -> list[dict]:
    """Return per-day avg/peak from readings and event count, newest first."""
    with _db_lock:
        c = _conn(db_path)
        rows = c.execute(
            # First 10 chars of ISO string = 'YYYY-MM-DD'
            "SELECT SUBSTRING(timestamp, 1, 10) AS day, "
            "  ROUND(AVG(noise_value), 1) AS avg_noise, "
            "  ROUND(MAX(noise_value), 1) AS peak_noise "
            "FROM readings WHERE timestamp >= ? GROUP BY day ORDER BY day DESC LIMIT 30",
            [since_iso],
        ).fetchall()
        stats = [{"day": r[0], "avg_noise": r[1], "peak_noise": r[2]} for r in rows]
        event_rows = c.execute(
            "SELECT SUBSTRING(timestamp, 1, 10) AS day, COUNT(*) AS event_count "
            "FROM events WHERE timestamp >= ? GROUP BY day",
            [since_iso],
        ).fetchall()
    event_map = {r[0]: r[1] for r in event_rows}
    for s in stats:
        s["event_count"] = event_map.get(s["day"], 0)
    return stats


def prune_old_data(db_path: str, retain_days: int = 35) -> None:
    """Delete readings and events older than `retain_days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
    with _db_lock:
        c = _conn(db_path)
        c.execute("DELETE FROM readings WHERE timestamp < ?", [cutoff])
        c.execute("DELETE FROM events WHERE timestamp < ?", [cutoff])
