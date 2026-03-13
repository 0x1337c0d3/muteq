## 🔗 Quick Links

| | |
|---|---|
| 📊 **Dashboard** | [www.hoongram.com](https://www.hoongram.com) — Live noise monitoring & analytics |

---

## 🎯 What is mute?

**mute** is a self-hostable acoustic monitoring platform — no server required.

Connect a USB sound meter to a Raspberry Pi, and it writes SPL readings to a local SQLite database and publishes a static HTML dashboard to S3 every 60 seconds. No cloud backend, no server to maintain.

- 📈 **Noise level monitoring** — readings every 0.5 seconds, written to local SQLite
- 🌐 **Static dashboard on S3** — auto-published to `https://www.hoongram.com` via CloudFront
- 🏠 **Local Home Assistant integration** via optional MQTT
- ☁️ **One-command AWS deployment** — S3 + CloudFront + ACM + Route53 via CloudFormation
- 🔧 **Full Makefile** for formatting and cloud ops

---

## ⚡ How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  🎤 USB Sound   │ ──▶ │  sensor/        │ ──▶ │  SQLite (local) │     │  📊 Dashboard   │
│     Meter       │     │  Python / Pi    │     │  on Pi SD card  │     │  S3 + CloudFront│
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
                                │                        │  every 60s ──▶  s3://www.hoongram.com
                                │ (optional)             │                  /index.html
                                ▼
                        ┌─────────────────┐
                        │  📡 MQTT        │
                        │  Home Assistant │
                        └─────────────────┘
```

### Project Layout

```
mute/
├── sensor/
│   ├── app/                # core sensor logic
│   │   ├── main.py         # orchestration & measurement loop
│   │   ├── db.py           # SQLite: init, write_reading, write_event, prune
│   │   ├── html_generator.py  # static HTML dashboard generation
│   │   ├── s3_uploader.py  # boto3 upload to S3
│   │   ├── config_loader.py
│   │   ├── mqtt_client.py
│   │   └── usb_reader.py
│   ├── client.py           # entry point
│   ├── install.sh          # Pi setup: venv, udev, systemd service
│   ├── test_runner.py      # simulated sensor (no USB needed)
│   └── requirements.txt
├── cloudformation.yml      # S3 + CloudFront + ACM + Route53
└── Makefile                # fmt, lint, aws-deploy, aws-delete
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

---

## ☁️ Step 1 — Deploy the AWS infrastructure

This creates the S3 bucket, CloudFront distribution, ACM TLS certificate, and Route53 DNS record. Run once from your dev machine (must deploy to `us-east-1`):

```bash
git clone https://github.com/you/mute.git && cd mute
make aws-deploy HostedZoneId=Z02362723Q3704KGHR8UQ
```

This will:
- 🪣 Create an S3 bucket (`www.hoongram.com`) with static website hosting
- 🔒 Issue a TLS certificate via ACM with automatic DNS validation (takes ~5–30 min)
- 🌐 Create a CloudFront distribution serving `https://www.hoongram.com`
- 🗺️ Create a Route53 A record pointing to CloudFront

See [`cloudformation.yml`](cloudformation.yml) for the full stack definition.

---

## 🎤 Step 2 — Install the sensor on your Pi

```bash
git clonehttps://github.com/0x1337c0d3/muteq.git && cd mute
./sensor/install.sh
```

Then edit `/var/lib/muteq-sensor/config_client.json`:

```json
{
  "device_name": "My Sensor",
  "location": { "address": "123 Main St" },
  "s3_bucket": "www.hoongram.com",
  "aws_region": "us-east-1",
  "aws_access_key_id": "AKIA...",
  "aws_secret_access_key": "..."
}
```

Restart the service:

```bash
sudo systemctl restart muteq-sensor
journalctl -u muteq-sensor -f   # confirm "[S3] Dashboard uploaded"
```

The Pi IAM user only needs one permission:

```json
{ "Effect": "Allow", "Action": "s3:PutObject",
  "Resource": "arn:aws:s3:::www.hoongram.com/index.html" }
```

### Test without a USB meter

Writes simulated SPL readings to SQLite and generates HTML locally:

```bash
python sensor/test_runner.py
# open /tmp/muteq-dashboard.html in your browser
```

---

## 🏠 Optional: Home Assistant Integration (MQTT)

Set the MQTT fields in `config_client.json` (or pass as environment variables):

```json
{
  "mqtt_enabled": true,
  "mqtt_server": "192.168.1.100",
  "mqtt_port": 1883,
  "mqtt_user": "mqtt-user",
  "mqtt_pass": "mqtt-pass"
}
```

Or via environment variables (prefix `LOCAL_MQTT_`):

```bash
LOCAL_MQTT_ENABLED=true LOCAL_MQTT_SERVER=192.168.1.100 python sensor/client.py
```

> 💡 **Note:** MQTT is completely optional. Your Mute Box works perfectly fine without it — the S3 dashboard is always the primary output.

### Environment Variables (All Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCAL_MQTT_ENABLED` | `false` | Enable MQTT publishing for Home Assistant |
| `LOCAL_MQTT_SERVER` | — | MQTT broker IP address |
| `LOCAL_MQTT_PORT` | `1883` | MQTT broker port |
| `LOCAL_MQTT_USER` | — | MQTT username |
| `LOCAL_MQTT_PASS` | — | MQTT password |
| `LOCAL_MQTT_TLS` | `false` | Enable TLS for MQTT connection |

> 🚫 **No other configuration is needed.** There are no API keys, no tokens, no manual device IDs. Everything is automatic.

---

## 🏠 Home Assistant Integration

mute supports **MQTT auto-discovery** for seamless Home Assistant integration. When MQTT is configured, your sensor will automatically appear in Home Assistant!

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
        entity_id: sensor.mute_box_noise_level
        above: 85
    action:
      - service: notify.mobile_app
        data:
          message: "⚠️ Noise level is {{ states('sensor.mute_box_noise_level') }} dB!"
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

The static dashboard at [www.hoongram.com](https://www.hoongram.com) regenerates every 60 seconds:

- 📈 **Latest SPL** — Most recent reading with color coding (green/yellow/red)
- 🔴 **Peak (1 h)** — Highest reading in the last hour
- 📉 **Threshold events** — Count of readings ≥ 80 dB
- 📊 **SPL chart** — Chart.js line chart with 1h / 1d / 1w / 1m timeframes (instant switching, all data embedded)
- 📋 **Events table** — List of threshold breaches for the selected timeframe
- 🔄 **Auto-refresh** — Page reloads every 60 seconds via `<meta http-equiv="refresh">`

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

## 🔧 Build Your Own Mute Box

<div align="center">

### 100% Open Source · Self-Hostable · Works Instantly

Building your own Mute Box is easy and affordable. All you need is:

- A Raspberry Pi 3 or newer (or any Linux device)
- A supported USB sound meter
- Python 3.11+ or Docker

</div>

---

## 🛠️ Development

All common tasks are available via `make`:

```
make fmt             # auto-format + sort imports with ruff
make lint            # lint check (no auto-fix)
make fmt-check       # CI-safe format check
make clean           # remove __pycache__, .pyc, .ruff_cache, etc.
make aws-deploy HostedZoneId=ZXXX   # deploy / update CloudFormation stack (us-east-1)
make aws-status      # show stack status and outputs
make aws-delete      # tear down the stack (prompts for confirmation)
```

Local test (no USB, no AWS needed):

```bash
python sensor/test_runner.py
# reads: /tmp/muteq-test.db
# output: /tmp/muteq-dashboard.html
```

Install dev dependencies (just `ruff`):

```bash
pip install -r requirements-dev.txt
```

---

## 🔒 Privacy First

| | |
|---|---|
| 🚫🎤 **No Audio Recordings** | Only dB levels are captured. Never actual sound content. |
| 👤 **No Personal Data** | No PII collected. Anonymous by design. |
| 🇪🇺 **GDPR Compliant** | Built in Europe with privacy at its core. |
| 🔐 **Local-first** | All data stays on the Pi. Only the static HTML is uploaded to S3. |

---

## 🤝 Contributing

We love contributions from the community! Here's how you can help:

- 🐛 **Report bugs** — Found an issue? Let us know!
- 💡 **Suggest features** — Have an idea? We're all ears.
- 🎤 **Add USB devices** — Help us support more sound meters.
- 📖 **Improve docs** — Documentation PRs are always welcome.
- 🌍 **Translations** — Help make mute accessible worldwide.


---

## 🙏 Acknowledgements

Based on an idea by **Raphaël Vael** witih ❤️

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
</p>

</div>
