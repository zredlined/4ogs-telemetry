#!/bin/bash
# Deploy the project to the Orin and optionally run setup.
# Usage: ./deploy.sh [--setup]

set -euo pipefail

ORIN="orin@orin.local"
ORIN_DIR="/home/orin/4ogs-telemetry"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying to Orin ==="

# Create directory structure on Orin
sshpass -p "fatwoman" ssh -o StrictHostKeyChecking=no "$ORIN" "mkdir -p $ORIN_DIR/src $ORIN_DIR/systemd"

# Copy project files
echo "Copying files..."
sshpass -p "fatwoman" scp -o StrictHostKeyChecking=no \
    "$SCRIPT_DIR/pyproject.toml" \
    "$SCRIPT_DIR/setup_orin.sh" \
    "$SCRIPT_DIR/install_orin_service.sh" \
    "$ORIN:$ORIN_DIR/"

sshpass -p "fatwoman" scp -o StrictHostKeyChecking=no -r \
    "$SCRIPT_DIR/src/telemetry" \
    "$ORIN:$ORIN_DIR/src/"

sshpass -p "fatwoman" scp -o StrictHostKeyChecking=no \
    "$SCRIPT_DIR/systemd/race-overlay.service" \
    "$ORIN:$ORIN_DIR/systemd/"

echo "Files deployed to $ORIN:$ORIN_DIR/"

# Run setup if requested
if [ "${1:-}" = "--setup" ]; then
    echo ""
    echo "Running setup on Orin..."
    sshpass -p "fatwoman" ssh -t -o StrictHostKeyChecking=no "$ORIN" \
        "export PATH=\$HOME/.local/bin:\$PATH && cd $ORIN_DIR && bash setup_orin.sh"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. SSH to Orin:  sshpass -p fatwoman ssh orin@orin.local"
echo "  2. Run setup:    cd ~/4ogs-telemetry && bash setup_orin.sh"
echo "  3. Run overlay:  cd ~/4ogs-telemetry && uv run race-overlay --source webcam --camera-device /dev/video0"
echo "  4. Open browser: http://orin.local:8080"
