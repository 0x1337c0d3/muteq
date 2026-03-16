"""MUTEq Lambda — ingest endpoint.

POST /ingest
Body (JSON):
  {
    "device_id": "abc123def456",
    "api_key":   "secret",
    "readings":  [
      {"ts": "2026-03-16T14:30:00+00:00", "noise_value": 65.3, "peak_value": 67.1},
      ...
    ]
  }

Writes one Parquet file per batch to:
  s3://{DATA_BUCKET}/readings/{device_id}/YYYY/MM/DD/YYYYMMDD_HHMMSS.parquet

Environment variables (set by CloudFormation):
  DATA_BUCKET  — S3 bucket name for Parquet storage
  API_KEY      — shared secret; reject requests that don't match
"""

import io
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def handler(event: dict, context) -> dict:
    # OPTIONS pre-flight
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": "",
        }

    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid JSON"})

    # Auth
    expected_key = os.environ.get("API_KEY", "")
    if expected_key and body.get("api_key") != expected_key:
        return _resp(401, {"error": "unauthorized"})

    device_id = body.get("device_id", "")
    if not device_id or not _DEVICE_ID_RE.match(device_id):
        return _resp(400, {"error": "invalid device_id"})

    readings = body.get("readings")
    if not isinstance(readings, list) or len(readings) == 0:
        return _resp(400, {"error": "readings must be a non-empty list"})

    # Cap batch size to prevent abuse
    readings = readings[:10_000]

    # Build PyArrow table
    timestamps = []
    noise_values = []
    peak_values = []
    for r in readings:
        ts = r.get("ts") or r.get("timestamp")
        if not ts:
            continue
        timestamps.append(str(ts))
        noise_values.append(float(r.get("noise_value", 0)))
        peak_values.append(float(r.get("peak_value") or r.get("noise_value", 0)))

    if not timestamps:
        return _resp(400, {"error": "no valid readings in batch"})

    table = pa.table(
        {
            "timestamp": pa.array(timestamps, type=pa.string()),
            "noise_value": pa.array(noise_values, type=pa.float64()),
            "peak_value": pa.array(peak_values, type=pa.float64()),
        }
    )

    # Write Parquet to S3
    now = datetime.now(timezone.utc)
    s3_key = (
        f"readings/{device_id}/"
        f"{now.strftime('%Y/%m/%d')}/"
        f"{now.strftime('%Y%m%d_%H%M%S')}.parquet"
    )
    data_bucket = os.environ["DATA_BUCKET"]

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=data_bucket,
        Key=s3_key,
        Body=buf.read(),
        ContentType="application/octet-stream",
    )

    logger.info(
        f"Ingested {len(timestamps)} readings for {device_id} → s3://{data_bucket}/{s3_key}"
    )
    return _resp(200, {"ingested": len(timestamps), "key": s3_key})
