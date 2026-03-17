# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MUTEq is a self-hostable acoustic (noise) monitoring platform. A Raspberry Pi 3 reads SPL from a USB sound meter, writes smoothed readings to a local SQLite database, and periodically flushes them to a self-hosted FastAPI dashboard server. The dashboard is a live-updating web app served by that server.

## Commands

```bash
make install         # Install dev dependencies (uv sync --group dev)
make fmt             # Auto-format Python with ruff + sort imports
make lint            # Lint check with ruff (no auto-fix)
make fmt-check       # CI-safe format check
make clean           # Remove Python caches and build artifacts

# Local test (no USB meter needed) — run in two terminals:
uv run python sensor/test_runner.py
#   writes a reading every 2s to /tmp/muteq-test.db; set SERVER_URL + SERVER_HMAC_SECRET
#   to flush via /api/ingest, or omit to let the dashboard read the DB directly (simpler)

MUTEQ_DB=/tmp/muteq-test.db uv run uvicorn dashboard.server:app --reload --port 8080
#   dashboard server reading the test DB directly — open http://localhost:8080

# AWS — deploy S3/CloudFront/ACM stack (must run in us-east-1)
make aws-deploy HostedZoneId=Z02362723Q3704KGHR8UQ    # deploy or update CloudFormation stack
make aws-status                       # show stack status and outputs
make aws-delete                       # tear down stack (prompts for confirmation)
```

## Architecture

### Data Flow
1. Sensor reads USB SPL meter every 0.1s, batches into 0.1-second windows
2. Every 0.1s: applies **AsymmetricEMA smoothing** (fast attack, slow decay) to the raw peak; writes the smoothed value to `readings`; if raw peak ≥ 70 dB also writes raw peak to `events`
3. Every 60s (`publish_interval_seconds`): flushes unsent rows to the dashboard server via `POST /api/ingest` with HMAC-SHA256 auth
4. Dashboard server stores ingested data in its own SQLite DB
5. Browser polls `/api/readings`, `/api/events`, `/api/stats` every 30s; live mode shows a rolling 30-min window

### Sensor Components (`sensor/app/`)
- `main.py` — Orchestration: USB init, MQTT init, measurement loop, periodic server flush
- `smoothing.py` — `AsymmetricEMA(rise_alpha, fall_alpha)`: fast-attack slow-decay smoothing applied to each reading before it is stored
- `db.py` — stdlib `sqlite3`: `init_db`, `write_reading`, `write_event`, `get_unsent_readings`, `get_unsent_events`, `mark_readings_sent`, `mark_events_sent`, `prune_old_data`
- `server_client.py` — `flush(config, db_path)`: batches unsent rows, signs with HMAC-SHA256, POSTs to `/api/ingest`; marks rows sent on HTTP 200
- `usb_reader.py` — pyusb wrapper (default device: VID=0x16C0, PID=0x05DC)
- `mqtt_client.py` — Optional MQTT with Home Assistant auto-discovery; `publish_realtime` is rate-limited to `mqtt_realtime_interval_seconds` (default 5s)
- `config_loader.py` — JSON config load/persist/validate with defaults

### Dashboard Server (`dashboard/`)
- `server.py` — FastAPI app; endpoints: `GET /api/readings`, `GET /api/events`, `GET /api/stats`, `POST /api/ingest`, `GET /api/config`
- `static/index.html` + `static/dashboard.js` + `static/dashboard.css` — single-page dashboard using TradingView Lightweight Charts; Live mode button for rolling 30-min view; scroll-back loads older history on demand

### SQLite Schema
Both the sensor DB (local, on Pi) and the dashboard server DB share the same core schema:
- `readings(id, timestamp TEXT, noise_value REAL NOT NULL, peak_value REAL, sent INTEGER DEFAULT 0)` — smoothed SPL; `sent` column only present on sensor side
- `events(id, timestamp TEXT, noise_value REAL NOT NULL, peak_value REAL, sent INTEGER DEFAULT 0)` — raw peak for threshold breaches ≥ 70 dB

For local testing, `MUTEQ_DB=/tmp/muteq-test.db` points the dashboard server at the sensor's DB directly — no ingest needed; the extra `sent` column is ignored by the server's queries.

### AWS Infrastructure (`cloudformation.yml`)
Deploy to **us-east-1** (required for CloudFront ACM certs):
- S3 bucket (`www.hoongram.com`) with static website hosting + public read policy
- ACM certificate with DNS validation via Route53
- CloudFront distribution with `CachingDisabled` managed policy + HTTPS redirect
- Route53 A alias record → CloudFront

### Deployment (Pi — first time)
```bash
./sensor/install.sh          # installs venv, udev rules, systemd service
# then edit /var/lib/muteq-sensor/config_client.json:
#   server_url, server_hmac_secret
sudo systemctl restart muteq-sensor
```

### Updating the Pi
```bash
cd ~/muteq
git pull
sudo systemctl restart muteq-sensor
journalctl -u muteq-sensor -n 100 -f
```

## Configuration

Sensor config at `sensor/client_config.json` (template) or `/var/lib/muteq-sensor/config_client.json` (live):

| Key | Default | Description |
|-----|---------|-------------|
| `device_name` | `"MUTEq Sensor"` | Display name on the dashboard |
| `location.address` | `""` | Shown in dashboard header |
| `environment_profile` | `"traffic_roadside"` | Noise context label |
| `db_path` | `/var/lib/muteq-sensor/muteq.db` | SQLite file path |
| `publish_interval_seconds` | `60` | How often to flush readings to the dashboard server |
| `server_url` | `null` | Dashboard server base URL (e.g. `http://localhost:8080`) |
| `server_hmac_secret` | `null` | Shared HMAC secret for `/api/ingest` authentication |
| `mqtt_enabled` | `false` | Enable optional Home Assistant MQTT |
| `mqtt_realtime_interval_seconds` | `5` | How often to publish the smoothed SPL to MQTT realtime topic |

Dashboard server env vars:
- `MUTEQ_DB` — path to SQLite DB (defaults to reading `sensor/client_config.json`)
- `MUTEQ_HMAC_SECRET` — must match sensor's `server_hmac_secret`
- `MUTEQ_CONFIG` — path to sensor config file (default: `sensor/client_config.json`)

## Code Style

Uses **uv** for dependency management and **ruff** for formatting and linting (replaces black/isort/flake8). Run `make fmt` before committing. Dev dependencies are in `pyproject.toml` under `[dependency-groups] dev`. The Pi's runtime dependencies remain in `sensor/requirements.txt` (installed via pip by `sensor/install.sh`).
