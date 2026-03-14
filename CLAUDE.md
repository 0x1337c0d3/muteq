# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MUTEq is a self-hostable acoustic (noise) monitoring platform. A Raspberry Pi 3 reads SPL from a USB sound meter, writes readings to a local SQLite database, and every 60 seconds generates a static HTML dashboard and uploads it to S3. The dashboard is served at `https://www.hoongram.com` via CloudFront.

There is no backend server. Everything runs on the Pi.

## Commands

```bash
make fmt             # Auto-format Python with ruff + sort imports
make lint            # Lint check with ruff (no auto-fix)
make fmt-check       # CI-safe format check
make clean           # Remove Python caches and build artifacts

# Local test (no USB meter needed)
python sensor/test_runner.py   # writes to /tmp/muteq-test.db, HTML to /tmp/muteq-dashboard.html

# AWS — deploy S3/CloudFront/ACM stack (must run in us-east-1)
make aws-deploy HostedZoneId=Z02362723Q3704KGHR8UQ    # deploy or update CloudFormation stack
make aws-status                       # show stack status and outputs
make aws-delete                       # tear down stack (prompts for confirmation)
```

## Architecture

### Data Flow
1. Sensor reads USB SPL meter every 0.1s, batches into 0.5-second windows
2. Every 0.5 seconds: writes a row to local SQLite (`readings` table); if peak ≥ 70 dB also writes to `events` table
3. Every 60 seconds (`publish_interval_seconds`): queries SQLite, generates a self-contained static HTML dashboard, uploads it as `index.html` to the configured S3 bucket
4. Browsers visit `https://www.hoongram.com` (CloudFront → S3); page auto-refreshes every 60s via `<meta http-equiv="refresh">`

### Sensor Components (`sensor/app/`)
- `main.py` — Orchestration: USB init, MQTT init, measurement loop, periodic S3 publish
- `db.py` — stdlib `sqlite3`: `init_db`, `write_reading`, `write_event`, `query_readings`, `query_events`, `prune_old_data`
- `html_generator.py` — Generates self-contained HTML; all four timeframes (1h/1d/1w/1m) embedded as `const ALL_DATA` JS object so switching is instant; `<meta refresh=60>`
- `s3_uploader.py` — boto3 `put_object` with `CacheControl: no-cache`; falls back to `~/.aws/credentials` if keys not in config
- `usb_reader.py` — pyusb wrapper (default device: VID=0x16C0, PID=0x05DC)
- `mqtt_client.py` — Optional MQTT with Home Assistant auto-discovery support
- `config_loader.py` — JSON config load/persist/validate with defaults

### SQLite Schema (local, on Pi)
- `readings(id, timestamp TEXT, noise_value REAL, peak_value REAL)` — one row every 0.5s; pruned to 35 days
- `events(id, timestamp TEXT, noise_value REAL, peak_value REAL)` — threshold breaches ≥ 70 dB

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
#   s3_bucket, aws_access_key_id, aws_secret_access_key (or use ~/.aws/credentials)
sudo systemctl restart muteq-sensor
```

### Updating the Pi
```bash
cd ~/muteq                        # or wherever the repo is cloned
git pull
sudo systemctl restart muteq-sensor

# Verify it came up cleanly:
journalctl -u muteq-sensor -n 100 -f
```

The Pi IAM user only needs: `s3:PutObject` on `arn:aws:s3:::www.hoongram.com/index.html`

## Configuration

Sensor config at `sensor/client_config.json` (template) or `/var/lib/muteq-sensor/config_client.json` (live):

| Key | Default | Description |
|-----|---------|-------------|
| `device_name` | `"MUTEq Sensor"` | Display name on the dashboard |
| `location.address` | `""` | Shown in dashboard header |
| `environment_profile` | `"traffic_roadside"` | Noise context label |
| `db_path` | `/var/lib/muteq-sensor/muteq.db` | SQLite file path |
| `publish_interval_seconds` | `60` | How often to regenerate + upload HTML |
| `s3_bucket` | `"www.hoongram.com"` | Target S3 bucket |
| `aws_region` | `"us-east-1"` | AWS region |
| `aws_access_key_id` | `null` | Leave null to use `~/.aws/credentials` |
| `aws_secret_access_key` | `null` | Leave null to use `~/.aws/credentials` |
| `mqtt_enabled` | `false` | Enable optional Home Assistant MQTT |

## Code Style

Uses **ruff** for formatting and linting (replaces black/isort/flake8). Run `make fmt` before committing.
