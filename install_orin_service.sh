#!/bin/bash
# Install and enable race-overlay systemd service on Orin.
# Run on Orin: bash install_orin_service.sh

set -euo pipefail

SERVICE_NAME="race-overlay.service"
SRC_FILE="$(cd "$(dirname "$0")" && pwd)/systemd/${SERVICE_NAME}"
DST_FILE="/etc/systemd/system/${SERVICE_NAME}"

if [ ! -f "$SRC_FILE" ]; then
  echo "Service file not found: $SRC_FILE" >&2
  exit 1
fi

echo "Installing ${SERVICE_NAME}..."
sudo cp "$SRC_FILE" "$DST_FILE"
sudo chmod 0644 "$DST_FILE"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo ""
echo "Service status:"
systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,25p'

