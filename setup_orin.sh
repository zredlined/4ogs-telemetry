#!/bin/bash
# Setup script for NVIDIA Orin Nano Super - Race overlay
# Run this ON the Orin: bash setup_orin.sh

set -e

echo "=== 4OGS Telemetry - Overlay Setup ==="

# Install system packages
echo "[1/3] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg gstreamer1.0-tools v4l-utils

# Install uv if not present
echo "[2/3] Checking uv..."
if ! command -v uv &> /dev/null && ! ~/.local/bin/uv --version &> /dev/null; then
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
echo "  uv $(uv --version)"

# Sync project
echo "[3/3] Syncing project..."
cd "$(dirname "$0")"
uv sync

# Verify GStreamer NVIDIA plugins
echo ""
echo "=== Verifying GStreamer HW acceleration ==="
gst-inspect-1.0 nvv4l2decoder > /dev/null 2>&1 && echo "  nvv4l2decoder: OK" || echo "  WARNING: nvv4l2decoder not found"
gst-inspect-1.0 nvvidconv > /dev/null 2>&1 && echo "  nvvidconv: OK" || echo "  WARNING: nvvidconv not found"
v4l2-ctl --list-devices || true

echo ""
ffmpeg -version 2>&1 | head -1
echo ""
echo "=== Setup complete ==="
echo "Run with: uv run race-overlay --source webcam --camera-device /dev/video0"
echo "Auto-start (optional): bash install_orin_service.sh"
