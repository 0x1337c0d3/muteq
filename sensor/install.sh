#!/usr/bin/env bash
# install.sh — install MUTEq sensor on a Raspberry Pi (or any Linux)
#
# Usage:
#   ./sensor/install.sh
#
# Optional environment variables:
#   S3_BUCKET    — S3 bucket name (default: www.hoongram.com)
#   AWS_REGION   — AWS region for S3 (default: us-east-1)
#
# After install, edit /var/lib/muteq-sensor/config_client.json to set:
#   aws_access_key_id, aws_secret_access_key  (or use ~/.aws/credentials)
#   s3_bucket, device_name, location, etc.
#
# Re-run to update after a git pull — safe to run multiple times.
# Config is preserved across updates (stored in MUTEQ_CONFIG_DIR).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SENSOR_DIR="$REPO_DIR/sensor"
VENV_DIR="/opt/muteq-sensor/venv"
SERVICE_FILE="/etc/systemd/system/muteq-sensor.service"
ENV_FILE="/etc/muteq/sensor.env"
CONFIG_DIR="/var/lib/muteq-sensor"

S3_BUCKET="${S3_BUCKET:-www.hoongram.com}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# ── System packages ───────────────────────────────────────────────────────────
echo "▶ Installing system packages..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y python3 python3-pip python3-venv libusb-1.0-0
elif command -v dnf &>/dev/null; then
  sudo dnf install -y python3 python3-pip libusb
fi

# ── Python venv ───────────────────────────────────────────────────────────────
echo "▶ Setting up Python venv at $VENV_DIR..."
sudo mkdir -p "$(dirname "$VENV_DIR")"
sudo python3 -m venv "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo "$VENV_DIR/bin/pip" install --quiet -r "$SENSOR_DIR/requirements.txt"

# ── Config directory ──────────────────────────────────────────────────────────
echo "▶ Creating config directory $CONFIG_DIR..."
sudo mkdir -p "$CONFIG_DIR"

# ── udev rule for USB sound meter ─────────────────────────────────────────────
UDEV_RULE="/etc/udev/rules.d/99-muteq-usb.rules"
if [ ! -f "$UDEV_RULE" ]; then
  echo "▶ Installing udev rule for USB sound meter..."
  sudo tee "$UDEV_RULE" > /dev/null <<'EOF'
# MUTEq USB sound meter (default VID=0x16C0 PID=0x05DC)
SUBSYSTEM=="usb", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05dc", MODE="0666"
EOF
  sudo udevadm control --reload-rules
  sudo udevadm trigger
fi

# ── Environment file ──────────────────────────────────────────────────────────
echo "▶ Writing environment file $ENV_FILE..."
sudo mkdir -p /etc/muteq
sudo tee "$ENV_FILE" > /dev/null <<EOF
MUTEQ_CONFIG_DIR=${CONFIG_DIR}
EOF
sudo chmod 600 "$ENV_FILE"

# ── systemd service ───────────────────────────────────────────────────────────
echo "▶ Writing systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MUTEq Sensor Client
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$VENV_DIR/bin/python $SENSOR_DIR/client.py
EnvironmentFile=$ENV_FILE
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable muteq-sensor
sudo systemctl restart muteq-sensor

echo ""
echo "✅ Done. MUTEq sensor is running."
echo "   Logs:   journalctl -u muteq-sensor -f"
echo "   Config: $CONFIG_DIR/config_client.json"
echo ""
echo "⚠️  Next steps — edit $CONFIG_DIR/config_client.json to configure:"
echo "   • device_name, location.address"
echo "   • s3_bucket: \"${S3_BUCKET}\""
echo "   • aws_region: \"${AWS_REGION}\""
echo "   • aws_access_key_id / aws_secret_access_key"
echo "     (or configure ~/.aws/credentials instead)"
echo ""
echo "   Then restart: sudo systemctl restart muteq-sensor"
echo ""
echo "   IAM policy required for the AWS user:"
echo "   { \"Effect\": \"Allow\", \"Action\": \"s3:PutObject\","
echo "     \"Resource\": ["
echo "       \"arn:aws:s3:::${S3_BUCKET}/index.html\","
echo "       \"arn:aws:s3:::${S3_BUCKET}/backup/*\""
echo "     ] }"
