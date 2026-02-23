# 4ogs-telemetry

Real-time race telemetry overlay prototype for in-car camera + simulated telemetry.

## Features

- Browser-based motorsport HUD (speed, RPM, gear, g-meter, lap timing, track map)
- Live camera feed via USB webcam (`/dev/video0`) or other sources
- Simulated telemetry over SSE/JSON, ready to replace with real data inputs
- Orin boot auto-start via `systemd`

## Local Run

```bash
uv run race-overlay --source webcam --camera-device /dev/video0
```

Open:

```text
http://localhost:8080
```

## Orin Setup

```bash
bash setup_orin.sh
bash install_orin_service.sh
```

Service:

```bash
systemctl status race-overlay.service
```

