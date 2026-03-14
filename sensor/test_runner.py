"""
Test runner for the MUTEq sensor.

Simulates a real sensor by writing randomly generated SPL readings directly to
a local SQLite database and generating the static HTML dashboard, without
requiring a USB SPL meter.

Usage (from the repo root):
    python sensor/test_runner.py

Optional env vars:
    DEVICE_NAME     Sensor display name  (default: Test Sensor)
    INTERVAL        Seconds between readings  (default: 2)
    DB_PATH         SQLite database path  (default: /tmp/muteq-test.db)
    S3_BUCKET       If set, upload generated HTML to this bucket
    AWS_REGION      AWS region for S3  (default: us-east-1)
"""

import logging
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the sensor/ directory or repo root
sys.path.insert(0, str(Path(__file__).parent))

from app.db import init_db, prune_old_data, write_event, write_reading
from app.html_generator import generate_html
from app.s3_uploader import upload_dashboard

MINIMUM_NOISE_LEVEL = 70.0
TIME_WINDOW_SECONDS = 2.0

DEVICE_NAME: str = os.environ.get("DEVICE_NAME", "Test Sensor")
INTERVAL: float = float(os.environ.get("INTERVAL", "2"))
DB_PATH: str = os.environ.get("DB_PATH", "/tmp/muteq-test.db")
S3_BUCKET: str = os.environ.get("S3_BUCKET", "")
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("muteq_test")

# ── Realistic random SPL generator ───────────────────────────────────────────
_baseline = 65.0
_spike_countdown = random.randint(10, 30)


def next_spl() -> float:
    global _baseline, _spike_countdown
    _baseline += random.gauss(0, 0.3)
    _baseline = max(58.0, min(75.0, _baseline))
    _spike_countdown -= 1
    if _spike_countdown <= 0:
        _spike_countdown = random.randint(15, 40)
        return round(random.uniform(80.0, 95.0), 1)
    return round(max(45.0, _baseline + random.gauss(0, 1.5)), 1)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    logger.info(f"Test runner starting — DB: {DB_PATH}")
    init_db(DB_PATH)
    prune_old_data(DB_PATH)

    stop = False

    def _handle_sig(signum, frame):
        nonlocal stop
        logger.info("Stopping test runner...")
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    logger.info(
        f"Writing readings every {INTERVAL}s to {DB_PATH}\n"
        f"  Device : {DEVICE_NAME}\n"
        f"  Press Ctrl-C to stop."
    )

    last_publish = 0.0
    publish_interval = 60.0
    upload_thread: threading.Thread | None = None

    while not stop:
        window_start = time.time()
        current_peak = 0.0
        while (time.time() - window_start) < TIME_WINDOW_SECONDS and not stop:
            value = next_spl()
            if value > current_peak:
                current_peak = value
            time.sleep(0.1)

        ts = datetime.now(timezone.utc).isoformat()
        write_reading(DB_PATH, ts, current_peak, current_peak)

        level_indicator = "🔊" if current_peak >= MINIMUM_NOISE_LEVEL else "  "
        logger.info(f"{level_indicator} {current_peak:.1f} dB  written to DB")

        if current_peak >= MINIMUM_NOISE_LEVEL:
            write_event(DB_PATH, ts, current_peak, current_peak)
            logger.info(f"   -> threshold event  peak={current_peak:.1f} dB")

        now = time.time()
        if now - last_publish >= publish_interval:
            if upload_thread and upload_thread.is_alive():
                logger.warning("[S3] Previous upload still running, skipping this cycle.")
            else:
                upload_thread = threading.Thread(target=_do_publish, daemon=True)
                upload_thread.start()
            last_publish = now

        sleep_remaining = INTERVAL - (time.time() - window_start)
        if sleep_remaining > 0:
            time.sleep(sleep_remaining)

    logger.info("Test runner stopped.")


def _do_publish():
    try:
        html_content = generate_html(
            db_path=DB_PATH,
            device_name=DEVICE_NAME,
            location="Test Location",
            environment_profile="traffic_roadside",
            generated_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error(f"HTML generation failed: {exc}")
        return

    out_path = Path("/tmp/muteq-dashboard.html")
    out_path.write_text(html_content, encoding="utf-8")
    logger.info(f"[HTML] Dashboard written to {out_path}")

    if S3_BUCKET:
        success = upload_dashboard(
            html_content=html_content,
            db_path=DB_PATH,
            bucket=S3_BUCKET,
            aws_region=AWS_REGION,
            aws_access_key_id=None,
            aws_secret_access_key=None,
            logger=logger,
        )
        if success:
            logger.info(f"[S3] Uploaded to s3://{S3_BUCKET}/index.html")


if __name__ == "__main__":
    main()
