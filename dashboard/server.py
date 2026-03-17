"""FastAPI dashboard server for MUTEq.

Launch:
    uv run python -m dashboard.server
    MUTEQ_DB=/tmp/muteq-test.db uv run uvicorn dashboard.server:app --reload --port 8080
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_CONFIG_PATH = os.environ.get("MUTEQ_CONFIG", "sensor/client_config.json")
_DB_PATH_ENV = os.environ.get("MUTEQ_DB")
_HMAC_SECRET = os.environ.get("MUTEQ_HMAC_SECRET", "")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_MAX_DB_FETCH = 20_000


def _load_db_path() -> str:
    if _DB_PATH_ENV:
        return _DB_PATH_ENV
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("db_path", "/var/lib/muteq-sensor/muteq.db")
    except OSError:
        return "/var/lib/muteq-sensor/muteq.db"


def _load_device_info() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        loc = cfg.get("location", {})
        return {
            "device_name": cfg.get("device_name", "MUTEq Sensor"),
            "location": loc.get("address", "") if isinstance(loc, dict) else "",
            "environment_profile": cfg.get("environment_profile", ""),
        }
    except OSError:
        return {"device_name": "MUTEq Sensor", "location": "", "environment_profile": ""}


_DB_PATH = _load_db_path()
_DEVICE_INFO = _load_device_info()


def _init_server_db(db_path: str) -> None:
    """Create tables in the server's own DB if they don't exist."""
    conn = sqlite3.connect(db_path)
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


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _init_server_db(_DB_PATH)
    yield


app = FastAPI(title="MUTEq Dashboard", docs_url="/docs", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _to_unix(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _downsample(rows: list, limit: int) -> list:
    if len(rows) <= limit:
        return rows
    step = len(rows) // limit
    return rows[::step]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    return (_HERE / "static" / "index.html").read_text()


@app.get("/api/config")
async def get_config():
    """Return device name, location and environment profile."""
    return _DEVICE_INFO


@app.get("/api/readings")
async def get_readings(
    from_ts: int | None = Query(None, description="Start unix timestamp (seconds, inclusive)"),
    to_ts: int | None = Query(None, description="End unix timestamp (seconds, inclusive)"),
    limit: int = Query(2000, ge=1, le=10000),
):
    """
    Return SPL readings as [{time, value}].

    - No params: latest `limit` readings.
    - from_ts only: readings from that point forward.
    - to_ts only: latest `limit` readings up to that timestamp (scroll-back).
    - Both: readings in range, downsampled to `limit`.
    """
    from_iso = _to_iso(from_ts) if from_ts is not None else None
    to_iso = _to_iso(to_ts) if to_ts is not None else None

    conn = _connect()
    try:
        if from_iso and to_iso:
            rows = conn.execute(
                "SELECT timestamp, noise_value FROM readings "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?",
                (from_iso, to_iso, _MAX_DB_FETCH),
            ).fetchall()
        elif from_iso:
            rows = conn.execute(
                "SELECT timestamp, noise_value FROM readings "
                "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
                (from_iso, _MAX_DB_FETCH),
            ).fetchall()
        elif to_iso:
            # Scroll-back: fetch rows ending at to_iso, return ascending
            rows = conn.execute(
                "SELECT timestamp, noise_value FROM readings "
                "WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT ?",
                (to_iso, _MAX_DB_FETCH),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                "SELECT timestamp, noise_value FROM readings ORDER BY timestamp DESC LIMIT ?",
                (_MAX_DB_FETCH,),
            ).fetchall()
            rows = list(reversed(rows))
    finally:
        conn.close()

    rows = _downsample(list(rows), limit)
    return [
        {"time": _to_unix(r["timestamp"]), "value": r["noise_value"]}
        for r in rows
        if r["noise_value"] is not None
    ]


@app.get("/api/events")
async def get_events(
    from_ts: int | None = Query(None),
    to_ts: int | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """Return threshold-breach events as [{time, noise, peak}], newest first."""
    from_iso = _to_iso(from_ts) if from_ts is not None else None
    to_iso = _to_iso(to_ts) if to_ts is not None else None

    conn = _connect()
    try:
        if from_iso and to_iso:
            rows = conn.execute(
                "SELECT timestamp, noise_value, peak_value FROM events "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT ?",
                (from_iso, to_iso, limit),
            ).fetchall()
        elif from_iso:
            rows = conn.execute(
                "SELECT timestamp, noise_value, peak_value FROM events "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                (from_iso, limit),
            ).fetchall()
        elif to_iso:
            rows = conn.execute(
                "SELECT timestamp, noise_value, peak_value FROM events "
                "WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT ?",
                (to_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, noise_value, peak_value FROM events "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()

    return [
        {"time": _to_unix(r["timestamp"]), "noise": r["noise_value"], "peak": r["peak_value"]}
        for r in rows
    ]


@app.get("/api/stats")
async def get_stats(
    from_ts: int | None = Query(
        None, description="Window start unix timestamp; defaults to 24h ago"
    ),
):
    """Return KPI stats, histogram, hourly heatmap, and daily summary."""
    if from_ts is None:
        from_ts = int((datetime.now(UTC) - timedelta(hours=24)).timestamp())
    from_iso = _to_iso(from_ts)
    month_ago_iso = _to_iso(int((datetime.now(UTC) - timedelta(days=30)).timestamp()))
    week_ago_iso = _to_iso(int((datetime.now(UTC) - timedelta(days=7)).timestamp()))

    conn = _connect()
    try:
        # All noise values in the window (for percentiles + histogram)
        values = [
            r["noise_value"]
            for r in conn.execute(
                "SELECT noise_value FROM readings WHERE timestamp >= ? ORDER BY timestamp ASC",
                (from_iso,),
            ).fetchall()
        ]

        # Single latest reading (regardless of window)
        latest_row = conn.execute(
            "SELECT noise_value FROM readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        latest = latest_row["noise_value"] if latest_row else None

        # Event count in window
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp >= ?", (from_iso,)
        ).fetchone()[0]

        # Hourly heatmap: events by hour-of-day over the last 7 days
        heatmap = [0] * 24
        for r in conn.execute(
            "SELECT CAST(strftime('%H', timestamp, 'localtime') AS INTEGER) AS hour, "
            "COUNT(*) AS count FROM events WHERE timestamp >= ? GROUP BY hour ORDER BY hour",
            (week_ago_iso,),
        ).fetchall():
            heatmap[r["hour"]] = r["count"]

        # Daily stats (last 30 days)
        daily_rows = conn.execute(
            "SELECT date(timestamp, 'localtime') AS day, "
            "  ROUND(AVG(noise_value), 1) AS avg_noise, "
            "  ROUND(MAX(noise_value), 1) AS peak_noise "
            "FROM readings WHERE timestamp >= ? GROUP BY day ORDER BY day DESC LIMIT 30",
            (month_ago_iso,),
        ).fetchall()
        daily_stats = [dict(r) for r in daily_rows]

        event_day_rows = conn.execute(
            "SELECT date(timestamp, 'localtime') AS day, COUNT(*) AS event_count "
            "FROM events WHERE timestamp >= ? GROUP BY day",
            (month_ago_iso,),
        ).fetchall()
        event_day_map = {r["day"]: r["event_count"] for r in event_day_rows}
        for s in daily_stats:
            s["event_count"] = event_day_map.get(s["day"], 0)

    finally:
        conn.close()

    # Percentiles
    def pct(p: int) -> float | None:
        if not values:
            return None
        s = sorted(values)
        return round(s[min(int(len(s) * p / 100), len(s) - 1)], 1)

    # Histogram: 5 dB bins 40–100, plus ≥100 overflow
    bin_starts = list(range(40, 100, 5))
    hist_labels = [f"{b}-{b + 5}" for b in bin_starts] + ["\u2265100"]
    hist_counts = [0] * len(hist_labels)
    for v in values:
        if v < 40:
            continue
        if v >= 100:
            hist_counts[-1] += 1
        else:
            idx = int((v - 40) / 5)
            hist_counts[min(idx, len(bin_starts) - 1)] += 1

    return {
        "latest": latest,
        "peak": round(max(values), 1) if values else None,
        "event_count": event_count,
        "percentiles": {"p50": pct(50), "p90": pct(90), "p99": pct(99)},
        "histogram": {"labels": hist_labels, "counts": hist_counts},
        "heatmap": heatmap,
        "daily_stats": daily_stats,
    }


@app.post("/api/ingest")
async def ingest(request: Request):
    """Receive a batch of readings and events from a sensor Pi.

    Requires HMAC-SHA256 authentication via X-HMAC-Signature header.
    """
    if not _HMAC_SECRET:
        raise HTTPException(status_code=503, detail="Server HMAC secret not configured")

    body = await request.body()
    provided = request.headers.get("X-HMAC-Signature", "")
    expected = "sha256=" + hmac.new(_HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    readings = payload.get("readings") or []
    events = payload.get("events") or []

    conn = _connect()
    try:
        for r in readings:
            conn.execute(
                "INSERT INTO readings (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
                (r["timestamp"], r["noise_value"], r["peak_value"]),
            )
        for e in events:
            conn.execute(
                "INSERT INTO events (timestamp, noise_value, peak_value) VALUES (?, ?, ?)",
                (e["timestamp"], e["noise_value"], e["peak_value"]),
            )
        conn.commit()
    finally:
        conn.close()

    return {"inserted": {"readings": len(readings), "events": len(events)}}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8080, reload=True)
