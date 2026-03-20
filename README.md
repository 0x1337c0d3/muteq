## 🔗 Quick Links

| | |
|---|---|
| 📊 **Dashboard** | [www.hoongram.com](https://www.hoongram.com) — Live noise monitoring & analytics |

---

## 🎯 What is MUTEq?

**MUTEq** is a self-hostable acoustic monitoring platform.

Connect a USB sound meter to a Raspberry Pi. The sensor reads SPL every 0.1s, applies smoothing, and stores readings in a local SQLite database. Every 60 seconds it flushes new data to a FastAPI dashboard server running on EC2. The dashboard is a live-updating web app served directly by that server.

- 📈 **Continuous noise monitoring** — readings every 0.1s with asymmetric EMA smoothing
- 🌐 **Live dashboard** — FastAPI server on EC2, HTTPS via nginx + Let's Encrypt
- 🔐 **HMAC-authenticated ingest** — sensor authenticates to the server with HMAC-SHA256
- 🏠 **Home Assistant integration** via optional MQTT
- ☁️ **One-command AWS deployment** — EC2 + nginx + Let's Encrypt + Route53 via CloudFormation
- 🔧 **Full Makefile** for formatting and cloud ops

---

## ⚡ How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  🎤 USB Sound   │ ──▶ │  sensor/        │ ──▶ │  SQLite (local) │     │  📊 Dashboard   │
│     Meter       │     │  Python / Pi    │     │  on Pi SD card  │     │  EC2 FastAPI    │
└─────────────────┘     └─────────────────┘     └─────────────────┘     │  + nginx/HTTPS  │
                                │                        │  every 60s     └─────────────────┘
                                │                        │  POST /api/ingest (HMAC-SHA256)
                                │ (optional)             │
                                ▼
                        ┌─────────────────┐
                        │  📡 MQTT        │
                        │  Home Assistant │
                        └─────────────────┘
```

### Data Flow

1. Sensor reads USB SPL meter every **0.1s**
2. Applies **AsymmetricEMA smoothing** (fast attack, slow decay) to each reading
3. Writes smoothed value to local `readings` table; if raw peak ≥ 70 dB also writes to `events`
4. Every **60s**: flushes unsent rows to the dashboard server via `POST /api/ingest` with HMAC-SHA256 auth
5. Dashboard server stores ingested data in its own SQLite DB on EC2
6. Browser polls `/api/readings`, `/api/events`, `/api/stats` every 30s — live mode shows a rolling 30-min window

### Project Layout

```
muteq/
├── sensor/
│   ├── app/
│   │   ├── main.py             # orchestration & measurement loop
│   │   ├── smoothing.py        # AsymmetricEMA (fast attack, slow decay)
│   │   ├── db.py               # SQLite: init, write_reading, write_event, prune
│   │   ├── server_client.py    # HMAC-SHA256 flush to /api/ingest
│   │   ├── usb_reader.py       # pyusb wrapper for sound meters
│   │   ├── mqtt_client.py      # optional MQTT + Home Assistant auto-discovery
│   │   └── config_loader.py    # JSON config load/validate with defaults
│   ├── client.py               # entry point
│   ├── install.sh              # Pi setup: venv, udev rule, systemd service
│   ├── test_runner.py          # simulated sensor (no USB needed)
│   └── requirements.txt
├── dashboard/
│   ├── server.py               # FastAPI: /api/readings, /api/events, /api/stats, /api/ingest
│   └── static/
│       ├── index.html          # single-page app (TradingView Lightweight Charts)
│       ├── dashboard.js
│       └── dashboard.css
├── cloudformation.yml          # EC2 + nginx + Let's Encrypt + Elastic IP + Route53
└── Makefile                    # fmt, lint, aws-deploy, aws-delete
```

---

## 🚀 Quick Start

### Prerequisites

1. ✅ A supported USB sound meter connected to your device
   → **Search for "volume meter HY1361" on Aliexpress — this model is tested and will be detected automatically**

   <p align="center">
     <img src="https://github.com/user-attachments/assets/3148ad40-651c-4b55-9bf0-4b42e53e2c46" width="50%" />
     <br/>
     <em>this is what you are looking for on Aliexpress</em>
   </p>

2. ✅ A Raspberry Pi 3 or any Linux device
3. ✅ Python 3.11+
4. ✅ An AWS account with a Route53 hosted zone for your domain
5. ✅ An EC2 key pair in `us-east-1`

---

## ☁️ Step 1 — Deploy the Dashboard Server (EC2)

This creates a t4g.micro EC2 instance (Graviton ARM64) running Amazon Linux 2023, with nginx as a reverse proxy and a Let's Encrypt TLS certificate issued via Route53 DNS-01 challenge. It also creates an Elastic IP and a Route53 A record.

Run once from your dev machine (**must deploy to `us-east-1`**):

```bash
git clone https://github.com/0x1337c0d3/muteq.git && cd muteq

# Generate a shared HMAC secret (save this — you'll need it for the sensor too)
python3 -c "import secrets; print(secrets.token_hex(32))"

make aws-deploy \
  HostedZoneId=Z02362723Q3704KGHR8UQ \
  KeyPairName=my-key \
  HmacSecret=<secret-from-above> \
  AcmeEmail=you@example.com \
  GitRepoUrl=https://github.com/0x1337c0d3/muteq.git
```

This will:
- 🖥️ Launch a `t4g.micro` EC2 instance (Graviton ARM64, Amazon Linux 2023)
- 🔒 Issue a TLS certificate via Let's Encrypt (DNS-01 via Route53) — HTTPS only
- 🌐 Configure nginx to proxy `https://www.hoongram.com` → FastAPI on `127.0.0.1:8080`
- 🗺️ Create a Route53 A record pointing to the Elastic IP
- 🚀 Clone the repo and start the `muteq-dashboard` systemd service automatically

See [`cloudformation.yml`](cloudformation.yml) for the full stack definition.

### First-boot progress

```bash
make aws-status   # show stack status and outputs (ElasticIP, SSHCommand, etc.)

# SSH in and watch the setup log:
ssh ec2-user@<ElasticIP>
sudo tail -f /var/log/muteq-setup.log
```

### Updating the EC2 server

```bash
ssh ec2-user@<ElasticIP>
sudo git -C /opt/muteq/app pull && sudo systemctl restart muteq-dashboard
journalctl -u muteq-dashboard -n 100 -f
```

---

## 🎤 Step 2 — Install the Sensor on Your Pi

```bash
git clone https://github.com/0x1337c0d3/muteq.git && cd muteq
./sensor/install.sh
```

Then edit `/var/lib/muteq-sensor/config_client.json`:

```json
{
  "device_name": "My Sensor",
  "location": { "address": "123 Main St" },
  "server_url": "https://www.hoongram.com",
  "server_hmac_secret": "<same-secret-used-in-aws-deploy>"
}
```

Restart the service:

```bash
sudo systemctl restart muteq-sensor
journalctl -u muteq-sensor -f   # confirm "Flushed N readings to server"
```

The sensor requires **no AWS credentials** — it authenticates to the dashboard server with the shared HMAC secret only.

### Test without a USB meter

```bash
# Terminal 1 — simulated sensor writing to /tmp/muteq-test.db
uv run python sensor/test_runner.py

# Terminal 2 — dashboard server reading the same DB directly
MUTEQ_DB=/tmp/muteq-test.db uv run uvicorn dashboard.server:app --reload --port 8080
# open http://localhost:8080
```

---

## 🏠 Optional: Home Assistant Integration (MQTT)

Set the MQTT fields in `config_client.json`:

```json
{
  "mqtt_enabled": true,
  "mqtt_server": "192.168.1.100",
  "mqtt_port": 1883,
  "mqtt_user": "mqtt-user",
  "mqtt_pass": "mqtt-pass"
}
```

### MQTT Topics

| Topic | Description |
|-------|-------------|
| `muteq/<device_id>/noise/realtime` | Current noise level in dB |
| `muteq/<device_id>/noise/threshold` | Threshold alert events |
| `muteq/<device_id>/availability` | Online/offline status |

### Example Home Assistant Automation

```yaml
automation:
  - alias: "Alert when noise exceeds 85 dB"
    trigger:
      - platform: numeric_state
        entity_id: sensor.muteq_noise_level
        above: 85
    action:
      - service: notify.mobile_app
        data:
          message: "⚠️ Noise level is {{ states('sensor.muteq_noise_level') }} dB!"
```

---

## 🎤 Supported USB Sound Meters

| Vendor ID | Product ID | Description |
|-----------|------------|-------------|
| `0x16c0` | `0x05dc` | Van Ooijen Technische Informatica HID meters |
| `0x1a86` | `0x7523` | Generic USB volume meter (common in DIY builds) |

> **Have a different USB sound meter?** [Open an Issue](https://github.com/0x1337c0d3/muteq/issues) to request support! Please include the vendor/product IDs.

---

## 📊 Dashboard Features

The live dashboard at [www.hoongram.com](https://www.hoongram.com) updates every 30 seconds via browser polling:

- 📈 **Latest SPL** — Most recent smoothed reading with color coding (green/yellow/red)
- 🔴 **Peak (1 h)** — Highest reading in the last hour
- 📉 **Threshold events** — Count of raw peaks ≥ 70 dB
- 📊 **SPL chart** — TradingView Lightweight Charts with scroll-back history loaded on demand
- 🔴 **Live mode** — Rolling 30-minute window with real-time updates
- 📋 **Events table** — List of threshold breaches

---

## ⚙️ Configuration

### Sensor (`/var/lib/muteq-sensor/config_client.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `device_name` | `"MUTEq Sensor"` | Display name on the dashboard |
| `location.address` | `""` | Shown in dashboard header |
| `environment_profile` | `"traffic_roadside"` | Noise context label |
| `db_path` | `/var/lib/muteq-sensor/muteq.db` | Local SQLite path |
| `publish_interval_seconds` | `60` | How often to flush readings to the server |
| `server_url` | `null` | Dashboard server base URL (e.g. `https://www.hoongram.com`) |
| `server_hmac_secret` | `null` | Shared HMAC secret — must match `HmacSecret` on the server |
| `mqtt_enabled` | `false` | Enable optional Home Assistant MQTT |
| `mqtt_realtime_interval_seconds` | `5` | How often to publish the smoothed SPL to MQTT |

### Dashboard Server (environment variables on EC2)

| Variable | Description |
|----------|-------------|
| `MUTEQ_DB` | Path to SQLite DB (defaults to `/var/lib/muteq-dashboard/muteq.db`) |
| `MUTEQ_HMAC_SECRET` | Must match `server_hmac_secret` in sensor config |
| `MUTEQ_CONFIG` | Path to sensor config file (default: `sensor/client_config.json`) |

---

## 🛠️ Development

Install dev dependencies:

```bash
make install   # uv sync --group dev
```

All common tasks are available via `make`:

```
make fmt             # auto-format + sort imports with ruff
make lint            # lint check (no auto-fix)
make fmt-check       # CI-safe format check
make clean           # remove __pycache__, .pyc, .ruff_cache, etc.
make dashboard       # run dashboard server locally at http://localhost:8080

make aws-deploy HostedZoneId=ZXXX KeyPairName=my-key HmacSecret=... AcmeEmail=you@example.com
make aws-status      # show stack status and outputs
make aws-delete      # tear down the stack (prompts for confirmation)
```

---

## 🎯 Who Is It For?

<table>
<tr>
<td align="center" width="33%">
<h3>🏠 Citizens</h3>
<p>Monitor noise in your neighborhood. Document disturbances. Take back your peace.</p>
</td>
<td align="center" width="33%">
<h3>🏛️ Municipalities</h3>
<p>Deploy city-wide sensor networks. Make data-driven noise policies.</p>
</td>
<td align="center" width="33%">
<h3>🎉 Event Organizers</h3>
<p>Stay compliant with noise limits. Real-time monitoring during events.</p>
</td>
</tr>
<tr>
<td align="center">
<h3>👮 Police & Enforcement</h3>
<p>Evidence-based enforcement. Timestamped data for legal proceedings.</p>
</td>
<td align="center">
<h3>🏢 Property Owners</h3>
<p>Document noise issues for tenant disputes. Monitor construction.</p>
</td>
<td align="center">
<h3>📋 Acoustic Consultants</h3>
<p>Professional-grade data at a fraction of the cost.</p>
</td>
</tr>
</table>

---

## 🔒 Privacy First

| | |
|---|---|
| 🚫🎤 **No Audio Recordings** | Only dB levels are captured. Never actual sound content. |
| 👤 **No Personal Data** | No PII collected. Anonymous by design. |
| 🇪🇺 **GDPR Compliant** | Built in Europe with privacy at its core. |
| 🔐 **Local-first** | All raw data stays on the Pi. Only aggregated readings are sent to the server. |

---

## 🤝 Contributing

We love contributions from the community! Here's how you can help:

- 🐛 **Report bugs** — Found an issue? Let us know!
- 💡 **Suggest features** — Have an idea? We're all ears.
- 🎤 **Add USB devices** — Help us support more sound meters.
- 📖 **Improve docs** — Documentation PRs are always welcome.
- 🌍 **Translations** — Help make MUTEq accessible worldwide.

---

## 🙏 Acknowledgements

Based on an idea by **Raphaël Vael** with ❤️

This project is based on the original work by **silkyclouds**:
[github.com/silkyclouds/mute](https://github.com/silkyclouds/mute)

---

## 📜 License

<a href="https://creativecommons.org/licenses/by-nc/4.0/"><img src="https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg" alt="CC BY-NC 4.0"></a>

This project is licensed under the **Creative Commons Attribution-NonCommercial 4.0 International License** ([CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)).

**You are free to:**
- ✅ Share — copy and redistribute the material
- ✅ Adapt — remix, transform, and build upon the material

**Under the following terms:**
- 📛 **Attribution** — You must give appropriate credit
- 🚫 **NonCommercial** — Commercial use requires explicit approval from the author

---

<div align="center">

**MUTEq** — Acoustic intelligence for everyone.

Open-source · Community-powered · Privacy-first

<p>
  <a href="https://www.hoongram.com">Dashboard</a> •
  <a href="https://github.com/0x1337c0d3/muteq/issues">Issues</a>
</p>

</div>
