import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict

from . import db as db_module

logger = logging.getLogger(__name__)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def flush(config: Dict[str, Any], db_path: str) -> None:
    """Read unsent rows from local SQLite and POST them to the server.

    On success the rows are marked sent. On any failure the rows are left
    untouched so the next flush cycle will retry them.
    """
    server_url = config.get("server_url")
    secret = config.get("server_hmac_secret")
    if not server_url or not secret:
        return

    readings = db_module.get_unsent_readings(db_path, limit=5000)
    events = db_module.get_unsent_events(db_path, limit=2000)

    if not readings and not events:
        return

    device_id = config.get("local_device_id", "")
    payload = {
        "device_id": device_id,
        "readings": [
            {
                "timestamp": r["timestamp"],
                "noise_value": r["noise_value"],
                "peak_value": r["peak_value"],
            }
            for r in readings
        ],
        "events": [
            {
                "timestamp": e["timestamp"],
                "noise_value": e["noise_value"],
                "peak_value": e["peak_value"],
            }
            for e in events
        ],
    }

    body = json.dumps(payload).encode()
    signature = _sign(secret, body)

    url = server_url.rstrip("/") + "/api/ingest"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-HMAC-Signature": f"sha256={signature}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                reading_ids = [r["id"] for r in readings]
                event_ids = [e["id"] for e in events]
                db_module.mark_readings_sent(db_path, reading_ids)
                db_module.mark_events_sent(db_path, event_ids)
                logger.info(
                    "[SERVER] Flushed %d readings and %d events to %s",
                    len(readings),
                    len(events),
                    server_url,
                )
            else:
                logger.warning("Server returned unexpected status %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.warning("Server ingest failed: HTTP %s", e.code)
    except Exception as e:
        logger.warning("Server ingest failed: %s", e)
