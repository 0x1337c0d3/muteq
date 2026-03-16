"""HTTP poster — replaces MQTT.

Accumulates readings in an in-memory buffer and POSTs them as a JSON batch
to the MUTEq Lambda ingest endpoint every `post_interval_seconds` (default 300 s).
Falls back gracefully when no endpoint is configured.
"""

import json
import threading
from typing import Any

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]


class HttpPoster:
    def __init__(
        self,
        api_endpoint: str,
        device_id: str,
        api_key: str,
        logger,
    ) -> None:
        self._endpoint = (api_endpoint or "").rstrip("/")
        self._device_id = device_id
        self._api_key = api_key or ""
        self._logger = logger
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint) and _requests is not None

    def add_reading(
        self,
        timestamp_iso: str,
        noise_value: float,
        peak_value: float,
    ) -> None:
        """Buffer one reading. Thread-safe; called from the measurement loop."""
        if not self.enabled:
            return
        with self._lock:
            self._buffer.append(
                {
                    "ts": timestamp_iso,
                    "noise_value": round(noise_value, 2),
                    "peak_value": round(peak_value, 2),
                }
            )

    def flush(self) -> None:
        """POST the buffered batch to the ingest endpoint and clear the buffer.

        Safe to call from a daemon thread; logs warnings on failure without raising.
        """
        if not self.enabled:
            return
        with self._lock:
            batch = self._buffer[:]
            self._buffer = []
        if not batch:
            return
        url = f"{self._endpoint}/ingest"
        payload = {
            "device_id": self._device_id,
            "api_key": self._api_key,
            "readings": batch,
        }
        try:
            resp = _requests.post(  # type: ignore[union-attr]
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            self._logger.info(f"[HTTP] Flushed {len(batch)} readings → {url}")
        except Exception as exc:
            self._logger.warning(f"[HTTP] Ingest POST failed ({len(batch)} readings dropped): {exc}")
