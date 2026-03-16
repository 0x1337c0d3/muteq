"""MUTEq Lambda — query endpoint.

GET /data?device_id=abc123

Returns all data needed to render the dashboard for the requested device.
Uses DuckDB with S3 HTTPFS to query Parquet files written by the ingest Lambda.

Response shape (JSON):
  {
    "device_id": "abc123",
    "generated_at": "2026-03-16T14:30:00Z",
    "timeframes": {
      "10m": {
        "readings":    [{"time": 1710591000, "value": 65.3}, ...],
        "percentiles": {"p50": 64.0, "p90": 68.5, "p99": 72.1},
        "histogram":   {"labels": ["40-45", ...], "counts": [...]},
        "events":      [{"timestamp": "2026-03-16 14:20:30 UTC", "noise_value": 72.1, "peak_value": 73.0}],
        "event_count": 3,
        "latest":      65.3,
        "peak":        72.1
      },
      "1h": { ... }, "1d": { ... }, "1w": { ... }, "1m": { ... }
    },
    "heatmap":     [0, 2, 5, ...],   // 24 UTC-hour event counts (last 7d)
    "daily_stats": [{"day": "2026-03-16", "avg_noise": 63.5, "peak_noise": 78.2, "event_count": 12}]
  }

Environment variables (set by CloudFormation):
  DATA_BUCKET  — S3 bucket containing Parquet files
  AWS_REGION   — set automatically by Lambda runtime
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

MINIMUM_NOISE_LEVEL = 70.0
DOWNSAMPLE_TARGET = 600

TIMEFRAMES: dict[str, timedelta] = {
    "10m": timedelta(minutes=10),
    "1h":  timedelta(hours=1),
    "1d":  timedelta(hours=24),
    "1w":  timedelta(hours=168),
    "1m":  timedelta(hours=720),
}
TIMEFRAME_LIMIT: dict[str, int] = {
    "10m": 600,
    "1h":  1800,
    "1d":  1440,
    "1w":  1008,
    "1m":  1440,
}


def _resp(status: int, body: Any, extra_headers: dict | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
    }
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body),
    }


def _get_duckdb_conn(data_bucket: str):
    """Create an in-memory DuckDB connection with S3 credentials from the Lambda env."""
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    region = os.environ.get("AWS_REGION", "us-east-1")
    conn.execute(f"SET s3_region='{region}';")

    # Use Lambda's temporary credentials (set as env vars by the runtime)
    key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    token = os.environ.get("AWS_SESSION_TOKEN", "")
    if key_id:
        conn.execute(f"SET s3_access_key_id='{key_id}';")
        conn.execute(f"SET s3_secret_access_key='{secret}';")
        if token:
            conn.execute(f"SET s3_session_token='{token}';")

    return conn


def _fetch_readings(conn, parquet_glob: str, since_iso: str, limit: int) -> list[dict]:
    """Fetch rows from Parquet, returning [{time: unix_s, value: float, ...}]."""
    try:
        rows = conn.execute(
            f"""
            SELECT
                timestamp,
                noise_value,
                peak_value
            FROM read_parquet('{parquet_glob}', union_by_name=true)
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            [since_iso, limit],
        ).fetchall()
    except Exception as exc:
        err_str = str(exc)
        if any(kw in err_str for kw in ("No files found", "FileNotFound", "NoSuchKey", "does not exist")):
            return []
        raise
    return rows


def _downsample(rows: list, target: int) -> list:
    if len(rows) <= target:
        return rows
    step = len(rows) // target
    return rows[::step]


def _to_unix(ts_str: str) -> int:
    """Convert ISO timestamp string to Unix seconds."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def _compute_percentiles(values: list[float]) -> dict:
    if not values:
        return {"p50": None, "p90": None, "p99": None}
    s = sorted(values)

    def pct(p: int) -> float:
        return round(s[min(int(len(s) * p / 100), len(s) - 1)], 1)

    return {"p50": pct(50), "p90": pct(90), "p99": pct(99)}


def _compute_histogram(values: list[float]) -> dict:
    bin_starts = list(range(40, 100, 5))
    labels = [f"{b}-{b + 5}" for b in bin_starts] + ["≥100"]
    counts = [0] * len(labels)
    for v in values:
        if v < 40:
            continue
        elif v >= 100:
            counts[-1] += 1
        else:
            idx = int((v - 40) / 5)
            counts[min(idx, len(bin_starts) - 1)] += 1
    return {"labels": labels, "counts": counts}


def handler(event: dict, context) -> dict:
    # OPTIONS pre-flight
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": "",
        }

    params = event.get("queryStringParameters") or {}
    device_id = params.get("device_id", "")
    if not device_id or not _DEVICE_ID_RE.match(device_id):
        return _resp(400, {"error": "invalid or missing device_id"})

    data_bucket = os.environ["DATA_BUCKET"]
    parquet_glob = f"s3://{data_bucket}/readings/{device_id}/**/*.parquet"

    now = datetime.now(timezone.utc)
    conn = _get_duckdb_conn(data_bucket)

    result: dict[str, Any] = {
        "device_id": device_id,
        "generated_at": now.isoformat(),
        "timeframes": {},
        "heatmap": [0] * 24,
        "daily_stats": [],
    }

    # ── Per-timeframe data ────────────────────────────────────────────────────
    for tf, delta in TIMEFRAMES.items():
        since = (now - delta).isoformat()
        limit = TIMEFRAME_LIMIT[tf]

        rows = _fetch_readings(conn, parquet_glob, since, limit)
        rows = _downsample(rows, DOWNSAMPLE_TARGET)

        readings_chart = []
        values = []
        for r in rows:
            ts_str, noise, _peak = r[0], r[1], r[2]
            readings_chart.append({"time": _to_unix(ts_str), "value": round(noise, 2)})
            values.append(noise)

        # Events (noise >= threshold) in this timeframe
        events_rows = []
        try:
            events_rows = conn.execute(
                f"""
                SELECT timestamp, noise_value, peak_value
                FROM read_parquet('{parquet_glob}', union_by_name=true)
                WHERE timestamp >= ?
                  AND noise_value >= {MINIMUM_NOISE_LEVEL}
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                [since],
            ).fetchall()
        except Exception as exc:
            err_str = str(exc)
            if not any(kw in err_str for kw in ("No files found", "FileNotFound", "NoSuchKey", "does not exist")):
                logger.warning(f"Events query failed for {tf}: {exc}")

        events = [
            {
                "timestamp": r[0].replace("T", " ").replace("+00:00", "") + " UTC",
                "noise_value": round(r[1], 2),
                "peak_value": round(r[2], 2) if r[2] is not None else None,
            }
            for r in events_rows
        ]

        result["timeframes"][tf] = {
            "readings": readings_chart,
            "percentiles": _compute_percentiles(values),
            "histogram": _compute_histogram(values),
            "events": events,
            "event_count": len(events),
            "latest": round(values[-1], 2) if values else None,
            "peak": round(max(values), 2) if values else None,
        }

    # ── Heatmap: events by UTC hour over last 7 days ──────────────────────────
    since_7d = (now - timedelta(days=7)).isoformat()
    try:
        heatmap_rows = conn.execute(
            f"""
            SELECT
                CAST(SUBSTRING(timestamp, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS cnt
            FROM read_parquet('{parquet_glob}', union_by_name=true)
            WHERE timestamp >= ?
              AND noise_value >= {MINIMUM_NOISE_LEVEL}
            GROUP BY hour
            ORDER BY hour
            """,
            [since_7d],
        ).fetchall()
        heatmap = [0] * 24
        for hr, cnt in heatmap_rows:
            if 0 <= hr <= 23:
                heatmap[hr] = cnt
        result["heatmap"] = heatmap
    except Exception as exc:
        logger.warning(f"Heatmap query failed: {exc}")

    # ── Daily stats: last 30 days ─────────────────────────────────────────────
    since_30d = (now - timedelta(days=30)).isoformat()
    try:
        daily_rows = conn.execute(
            f"""
            SELECT
                SUBSTRING(timestamp, 1, 10) AS day,
                ROUND(AVG(noise_value), 1)  AS avg_noise,
                ROUND(MAX(noise_value), 1)  AS peak_noise,
                COUNT(CASE WHEN noise_value >= {MINIMUM_NOISE_LEVEL} THEN 1 END) AS event_count
            FROM read_parquet('{parquet_glob}', union_by_name=true)
            WHERE timestamp >= ?
            GROUP BY day
            ORDER BY day DESC
            LIMIT 30
            """,
            [since_30d],
        ).fetchall()
        result["daily_stats"] = [
            {
                "day": r[0],
                "avg_noise": r[1],
                "peak_noise": r[2],
                "event_count": r[3],
            }
            for r in daily_rows
        ]
    except Exception as exc:
        logger.warning(f"Daily stats query failed: {exc}")

    conn.close()

    logger.info(
        f"Query for {device_id}: "
        + ", ".join(f"{tf}={len(result['timeframes'][tf]['readings'])}pts" for tf in TIMEFRAMES)
    )
    return _resp(200, result)
