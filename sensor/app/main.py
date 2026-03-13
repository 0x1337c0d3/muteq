import hashlib
import logging
import os
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config_loader import DEFAULT_CONFIG, load_config, persist_config, validate_config
from .db import init_db, prune_old_data, write_event, write_reading
from .html_generator import generate_html
from .mqtt_client import MuteqMqttClient
from .s3_uploader import upload_dashboard
from .usb_reader import find_usb_device, read_spl_value

CLIENT_VERSION = "0.1.0"
TIME_WINDOW_SECONDS = 0.1
MINIMUM_NOISE_LEVEL = 80.0


def setup_logging(level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, (level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return logging.getLogger("muteq_client")


class MuteqClientApp:
    """Sensor orchestration: reads USB SPL → SQLite → periodic S3 HTML upload."""

    def __init__(self):
        self.logger = setup_logging("INFO")
        self.config_path = Path()
        self._ensure_persistent_config()
        self.cfg = load_config(self.config_path, self.logger)
        self._apply_local_mqtt_overrides()
        self.cfg = validate_config(self.cfg, self.logger)
        self.logger = setup_logging(self.cfg.get("log_level", "INFO"))
        self.logger.info(f"Loaded configuration from {self.config_path}")
        self._ensure_local_device_id()
        self.stop_event = False
        self.mqtt_client: Optional[MuteqMqttClient] = None
        self.usb_device = None
        self.db_path: str = self.cfg.get("db_path", DEFAULT_CONFIG["db_path"])
        self._upload_thread: Optional[threading.Thread] = None

    def _ensure_local_device_id(self):
        """Derive a stable local device ID from device_name if not already set."""
        if not self.cfg.get("local_device_id"):
            device_name = self.cfg.get("device_name", "MUTEq Sensor")
            self.cfg["local_device_id"] = hashlib.sha256(device_name.encode()).hexdigest()[:16]
            persist_config(self.config_path, self.cfg, self.logger)

    def register_signals(self):
        def handler(signum, frame):
            self.logger.info("Shutdown signal received. Exiting...")
            self.stop_event = True

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def init_mqtt(self):
        if not self.cfg.get("mqtt_enabled"):
            return
        location = self.cfg.get("location") or {}
        self.mqtt_client = MuteqMqttClient(
            device_id=self.cfg.get("local_device_id"),
            device_name=self.cfg.get("device_name"),
            address=location.get("address") or "",
            env_profile=self.cfg.get("environment_profile") or "traffic_roadside",
            server=self.cfg.get("mqtt_server") or "",
            port=self.cfg.get("mqtt_port") or 1883,
            username=self.cfg.get("mqtt_user") or "",
            password=self.cfg.get("mqtt_pass") or "",
            tls=bool(self.cfg.get("mqtt_tls")),
            logger=self.logger,
        )
        self.mqtt_client.connect()

    def init_usb(self):
        usb_override = self.cfg.get("usb_override") or {}
        vendor_id = usb_override.get("vendor_id")
        product_id = usb_override.get("product_id")
        try:
            vendor_id_int = int(str(vendor_id), 0) if vendor_id is not None else None
        except Exception:
            vendor_id_int = None
        try:
            product_id_int = int(str(product_id), 0) if product_id is not None else None
        except Exception:
            product_id_int = None
        self.usb_device = find_usb_device(vendor_id_int, product_id_int, self.logger)

    def _publish_dashboard(self):
        """Generate static HTML from SQLite and upload to S3."""
        location = self.cfg.get("location") or {}
        try:
            html_content = generate_html(
                db_path=self.db_path,
                device_name=self.cfg.get("device_name", "MUTEq Sensor"),
                location=location.get("address") or "",
                environment_profile=self.cfg.get("environment_profile") or "",
                generated_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            self.logger.error(f"HTML generation failed: {exc}")
            return

        bucket = self.cfg.get("s3_bucket", "")
        if not bucket:
            self.logger.warning("[S3] s3_bucket not configured; skipping upload.")
            return

        success = upload_dashboard(
            html_content=html_content,
            db_path=self.db_path,
            bucket=bucket,
            aws_region=self.cfg.get("aws_region", "us-east-1"),
            aws_access_key_id=self.cfg.get("aws_access_key_id") or None,
            aws_secret_access_key=self.cfg.get("aws_secret_access_key") or None,
            cloudfront_distribution_id=self.cfg.get("cloudfront_distribution_id") or None,
            logger=self.logger,
        )
        if success:
            self.logger.info(f"[S3] Dashboard uploaded to s3://{bucket}/index.html")

    def measurement_loop(self):
        self.logger.info("Starting measurement loop.")
        publish_interval = float(self.cfg.get("publish_interval_seconds", 60))
        last_publish = 0.0

        while not self.stop_event:
            window_start = time.time()
            current_peak = None
            while (time.time() - window_start) < TIME_WINDOW_SECONDS and not self.stop_event:
                value = read_spl_value(self.usb_device, self.logger)
                if value is None:
                    time.sleep(0.1)
                    continue
                if current_peak is None or value > current_peak:
                    current_peak = value
                time.sleep(0.1)

            if current_peak is None:
                continue

            ts = datetime.now(timezone.utc).isoformat()
            write_reading(self.db_path, ts, current_peak, current_peak)
            print(
                f"\n{datetime.now().strftime('%H:%M:%S')}  {current_peak:5.1f} dB",
                end="",
                flush=True,
            )

            if self.mqtt_client:
                self.mqtt_client.publish_realtime(current_peak)

            if current_peak >= MINIMUM_NOISE_LEVEL:
                write_event(self.db_path, ts, current_peak, current_peak)
                if self.mqtt_client:
                    self.mqtt_client.publish_threshold(current_peak, current_peak)

            now = time.time()
            if now - last_publish >= publish_interval:
                if self._upload_thread and self._upload_thread.is_alive():
                    self.logger.warning("[S3] Previous upload still running, skipping this cycle.")
                else:
                    self._upload_thread = threading.Thread(
                        target=self._publish_dashboard, daemon=True
                    )
                    self._upload_thread.start()
                last_publish = now

    def shutdown(self):
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        self.logger.info("Shutdown complete.")

    def _ensure_persistent_config(self):
        config_dir_env = os.environ.get("MUTEQ_CONFIG_DIR")
        if config_dir_env:
            config_dir = Path(config_dir_env)
        else:
            config_dir = Path.home() / ".config" / "muteq"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.error(f"[FATAL] Cannot create config dir {config_dir}: {exc}")
            sys.exit(1)
        config_file = config_dir / "config_client.json"
        if not config_file.exists():
            template_path = Path(__file__).parent.parent / "client_config.json"
            try:
                if template_path.exists():
                    shutil.copyfile(template_path, config_file)
                else:
                    import json as _json

                    config_file.write_text(_json.dumps(DEFAULT_CONFIG, indent=2))
            except Exception as exc:
                self.logger.error(f"[FATAL] Unable to initialize config at {config_file}: {exc}")
                sys.exit(1)
        self.config_path = config_file

    def _apply_local_mqtt_overrides(self):
        def parse_bool(val: str):
            return val.strip().lower() in ("1", "true", "yes", "on")

        overrides = {
            "LOCAL_MQTT_ENABLED": ("mqtt_enabled", "bool"),
            "LOCAL_MQTT_SERVER": ("mqtt_server", "str"),
            "LOCAL_MQTT_PORT": ("mqtt_port", "int"),
            "LOCAL_MQTT_USER": ("mqtt_user", "str"),
            "LOCAL_MQTT_PASS": ("mqtt_pass", "str"),
            "LOCAL_MQTT_TLS": ("mqtt_tls", "bool"),
        }
        for env_key, (cfg_key, kind) in overrides.items():
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            try:
                if kind == "bool":
                    self.cfg[cfg_key] = parse_bool(raw)
                elif kind == "int":
                    self.cfg[cfg_key] = int(raw)
                else:
                    self.cfg[cfg_key] = raw
            except Exception:
                continue

    def run(self):
        self.register_signals()
        init_db(self.db_path)
        prune_old_data(self.db_path)
        self.init_usb()
        self.init_mqtt()
        try:
            self.measurement_loop()
        finally:
            self.shutdown()


def main():
    app = MuteqClientApp()
    app.run()


if __name__ == "__main__":
    main()
