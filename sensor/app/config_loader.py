import json
import os
from copy import deepcopy
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "config_version": 4,
    "device_name": "MUTEq Sensor",
    "local_device_id": None,
    "location": {"address": "", "lat": None, "lon": None, "country": ""},
    "environment_profile": "traffic_roadside",
    "custom_environment_label": "",
    "db_path": "/var/lib/muteq-sensor/muteq.db",
    # Lambda API
    "api_endpoint": "",
    "api_key": "",
    "http_post_interval_seconds": 300,
    # S3 / CloudFront
    "s3_bucket": "",
    "aws_region": "us-east-1",
    "aws_access_key_id": None,
    "aws_secret_access_key": None,
    "cloudfront_distribution_id": None,
    # USB override
    "usb_override": {"vendor_id": None, "product_id": None},
    "log_level": "INFO",
}


def sanitize_device_name(name: str) -> str:
    clean = (name or "MUTEq Sensor").strip()
    if not clean:
        clean = "MUTEq Sensor"
    return clean[:64]


def merge_defaults(cfg: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_CONFIG)
    for key, value in cfg.items():
        if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load_config(path: str, logger) -> Dict[str, Any]:
    if not os.path.exists(path):
        logger.warning(f"Config file not found at {path}; using defaults.")
        return deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        logger.error("Failed to read or parse config file; falling back to defaults.")
        cfg = deepcopy(DEFAULT_CONFIG)
    cfg = merge_defaults(cfg)
    cfg["device_name"] = sanitize_device_name(cfg.get("device_name"))
    return cfg


def persist_config(path: str, cfg: Dict[str, Any], logger) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        logger.info(f"[CONFIG] Saved config to {path}")
    except Exception as exc:
        logger.error(f"Failed to save config to {path}: {exc}")


def validate_config(cfg: Dict[str, Any], logger) -> Dict[str, Any]:
    cfg = merge_defaults(cfg)
    cfg["device_name"] = sanitize_device_name(cfg.get("device_name"))

    try:
        cfg["http_post_interval_seconds"] = int(cfg.get("http_post_interval_seconds", 300))
        if cfg["http_post_interval_seconds"] < 10:
            cfg["http_post_interval_seconds"] = 300
    except Exception:
        cfg["http_post_interval_seconds"] = 300

    if not cfg.get("db_path"):
        cfg["db_path"] = DEFAULT_CONFIG["db_path"]

    if not cfg.get("s3_bucket"):
        logger.warning("[CONFIG] s3_bucket is not set — S3 uploads will be skipped.")

    if not cfg.get("api_endpoint"):
        logger.warning("[CONFIG] api_endpoint is not set — live chart will show placeholder.")

    return cfg
