# Metro ESP32-S3 Telemetry Firmware

This firmware streams telemetry at 25 Hz over USB serial (JSON lines).
It reads both sensors over I2C (`Wire`): u-blox GNSS + LSM6DSOX.

## LED meanings

- `green solid`: GPS lock (`gps.fix=true`)
- `blue`: GNSS detected, searching for lock
- `amber`: IMU detected, GNSS not detected
- `red`: no sensors detected
- built-in LED blinks as a heartbeat per sample

## Configure

No Wi-Fi or MQTT config is required for the current USB-serial mode.

## Flash + Monitor

```bash
cd firmware/metro-telemetry
pio run -t upload
pio device monitor --port /dev/tty.usbmodem1101 --baud 115200
```

## Serial payload shape

One JSON object per line, including:

- `seq`, `ms`, optional `t` (epoch when GPS time is valid)
- `speed_mph`, `rpm`, `gear`, `g_lat`, `g_long`, `throttle`, `brake`
- `gps.fix`, `gps.sats`, `gps.hdop`, `gps.lat`, `gps.lon`, `gps.alt_m`, `gps.time_utc`
