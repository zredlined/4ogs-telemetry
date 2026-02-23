#!/usr/bin/env python3
"""
Real-time race overlay prototype.

Features:
- Captures a USB camera (or test pattern) via ffmpeg as an MJPEG stream.
- Serves a browser HUD inspired by motorsport broadcast overlays.
- Publishes simulated telemetry via JSON + SSE so real sensors can drop in later.
"""

from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import random
import signal
import socket
import subprocess
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

WEB_DIR = Path(__file__).resolve().parent / "web"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_secs(value: float) -> str:
    minutes = int(value // 60)
    seconds = value - (minutes * 60)
    return f"{minutes:02d}:{seconds:06.3f}"


class CpuUsageSampler:
    def __init__(self) -> None:
        self._prev_total = 0
        self._prev_idle = 0

    def sample(self) -> float | None:
        try:
            with open("/proc/stat", "r", encoding="utf-8") as handle:
                parts = handle.readline().split()
            values = [int(x) for x in parts[1:]]
            idle = values[3] + values[4]
            total = sum(values)
            if self._prev_total == 0:
                self._prev_total = total
                self._prev_idle = idle
                return None
            total_delta = total - self._prev_total
            idle_delta = idle - self._prev_idle
            self._prev_total = total
            self._prev_idle = idle
            if total_delta <= 0:
                return None
            usage = 100.0 * (1.0 - (idle_delta / total_delta))
            return clamp(usage, 0.0, 100.0)
        except (OSError, ValueError, IndexError):
            return None


class SystemStatsSampler:
    GPU_PATHS = (
        "/sys/devices/platform/17000000.ga10b/devfreq/17000000.ga10b/load",
        "/sys/devices/gpu.0/load",
    )
    TEMP_PATHS = (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    )

    def __init__(self) -> None:
        self._cpu_sampler = CpuUsageSampler()
        self._cpu_cores = os.cpu_count() or 1

    def _read_float(self, path: str, scale: float = 1.0) -> float | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
            return float(raw) / scale
        except (OSError, ValueError):
            return None

    def _read_gpu_percent(self) -> float | None:
        for candidate in self.GPU_PATHS:
            value = self._read_float(candidate, scale=1.0)
            if value is None:
                continue
            if value > 100.0:
                value /= 10.0
            return clamp(value, 0.0, 100.0)
        return None

    def _read_temp_c(self) -> float | None:
        for candidate in self.TEMP_PATHS:
            value = self._read_float(candidate, scale=1000.0)
            if value is not None:
                return value
        return None

    def _read_mem_used_percent(self) -> float | None:
        try:
            info: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, rest = line.split(":", maxsplit=1)
                    info[key] = int(rest.strip().split()[0])
            total = float(info.get("MemTotal", 0))
            available = float(info.get("MemAvailable", 0))
            if total <= 0:
                return None
            used_pct = 100.0 * ((total - available) / total)
            return clamp(used_pct, 0.0, 100.0)
        except (OSError, ValueError):
            return None

    def sample(self) -> dict[str, Any]:
        cpu_percent = self._cpu_sampler.sample()
        load_1m = os.getloadavg()[0]
        return {
            "cpu_percent": None if cpu_percent is None else round(cpu_percent, 1),
            "cpu_load_1m": round(load_1m, 2),
            "cpu_cores": self._cpu_cores,
            "gpu_percent": self._read_gpu_percent(),
            "temp_c": self._read_temp_c(),
            "mem_used_percent": self._read_mem_used_percent(),
        }


class TelemetrySimulator:
    def __init__(self) -> None:
        self._started = time.monotonic()
        self._lap = 2
        self._lap_started = self._started
        self._lap_target_s = 96.2
        self._best_lap_s = 95.612
        self._last_lap_s = 96.401
        self._rng = random.Random(24)
        self._stats = SystemStatsSampler()

    def _advance_lap_if_needed(self, now: float) -> None:
        elapsed = now - self._lap_started
        if elapsed < self._lap_target_s:
            return
        self._last_lap_s = elapsed
        self._best_lap_s = min(self._best_lap_s, self._last_lap_s)
        self._lap += 1
        self._lap_started = now
        self._lap_target_s = 95.0 + self._rng.uniform(-2.4, 2.8)

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        self._advance_lap_if_needed(now)
        since_start = now - self._started
        lap_time = now - self._lap_started
        progress = clamp(lap_time / self._lap_target_s, 0.0, 1.0)
        theta = progress * math.tau

        speed = 82.0 + 26.0 * math.sin(theta * 1.9 + 0.6)
        speed += 13.0 * math.sin(theta * 5.4 - 0.8)
        speed += 4.0 * math.sin(since_start * 0.9)
        speed_mph = clamp(speed, 24.0, 132.0)

        throttle = clamp(0.63 + 0.33 * math.sin(theta * 2.2 + 1.1), 0.0, 1.0)
        brake = clamp(0.18 + 0.25 * math.sin(theta * 2.2 - 1.2), 0.0, 1.0)
        if throttle > 0.7:
            brake *= 0.25

        rpm = 1700.0 + (speed_mph * 61.0) + (throttle * 1300.0) - (brake * 900.0)
        rpm += 240.0 * math.sin(since_start * 8.8)
        rpm = clamp(rpm, 1200.0, 8900.0)

        if speed_mph < 18:
            gear = "N"
        elif speed_mph < 31:
            gear = "1"
        elif speed_mph < 47:
            gear = "2"
        elif speed_mph < 70:
            gear = "3"
        elif speed_mph < 93:
            gear = "4"
        elif speed_mph < 114:
            gear = "5"
        else:
            gear = "6"

        g_lat = clamp(1.55 * math.sin(theta * 3.0 + 0.4), -2.0, 2.0)
        g_long = clamp(1.00 * math.sin(theta * 2.1 - 0.8) + (throttle - brake) * 0.35, -1.6, 1.6)

        track_x = clamp(0.50 + 0.34 * math.sin(theta) + 0.08 * math.sin(theta * 3.0 + 0.5), 0.02, 0.98)
        track_y = clamp(0.50 + 0.28 * math.cos(theta) - 0.10 * math.sin(theta * 2.0 + 1.3), 0.02, 0.98)

        predicted_delta = -0.38 + 0.28 * math.sin(since_start * 0.23) + self._rng.uniform(-0.02, 0.02)

        return {
            "ts_epoch_s": time.time(),
            "speed_mph": round(speed_mph, 1),
            "rpm": int(rpm),
            "gear": gear,
            "g_lat": round(g_lat, 2),
            "g_long": round(g_long, 2),
            "throttle": round(throttle, 3),
            "brake": round(brake, 3),
            "lap": {
                "number": self._lap,
                "current_time_s": round(lap_time, 3),
                "last_time_s": round(self._last_lap_s, 3),
                "best_time_s": round(self._best_lap_s, 3),
                "predicted_delta_s": round(predicted_delta, 3),
                "progress": round(progress, 4),
            },
            "track": {"x": round(track_x, 4), "y": round(track_y, 4)},
            "system": self._stats.sample(),
            "meta": {"source": "simulated", "updated_at": format_secs(since_start)},
        }


class CameraStreamer:
    def __init__(
        self,
        running: threading.Event,
        source: str,
        mjpeg_url: str,
        camera_device: str,
        video_file: str,
        width: int,
        height: int,
        fps: int,
        camera_port: int,
        jpeg_quality: int,
    ) -> None:
        self._running = running
        self._source = source
        self._mjpeg_url = mjpeg_url
        self._camera_device = camera_device
        self._video_file = video_file
        self._width = width
        self._height = height
        self._fps = fps
        self._camera_port = camera_port
        self._jpeg_quality = jpeg_quality
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "running": False,
            "restarts": 0,
            "last_error": "",
            "last_exit_code": None,
        }

    def _build_cmd(self) -> list[str]:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "+nobuffer",
            "-flags",
            "low_delay",
            "-thread_queue_size",
            "1024",
        ]
        if self._source == "webcam":
            cmd += [
                "-f",
                "v4l2",
                "-framerate",
                str(self._fps),
                "-video_size",
                f"{self._width}x{self._height}",
                "-i",
                self._camera_device,
            ]
        elif self._source == "file":
            cmd += [
                "-stream_loop",
                "-1",
                "-re",
                "-i",
                self._video_file,
            ]
        else:
            cmd += [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={self._width}x{self._height}:rate={self._fps}",
            ]

        cmd += [
            "-vf",
            f"scale={self._width}:{self._height}:flags=lanczos,fps={self._fps}",
            "-an",
            "-c:v",
            "mjpeg",
            "-q:v",
            str(self._jpeg_quality),
            "-f",
            "mpjpeg",
            "-listen",
            "1",
            f"http://127.0.0.1:{self._camera_port}/live.mjpg",
        ]
        return cmd

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass

    def _run_loop(self) -> None:
        if self._source == "mjpeg":
            with self._lock:
                self._status["running"] = bool(self._mjpeg_url)
                self._status["last_error"] = ""
                self._status["last_exit_code"] = None
            while self._running.is_set():
                time.sleep(0.5)
            with self._lock:
                self._status["running"] = False
            return

        while self._running.is_set():
            cmd = self._build_cmd()
            with self._lock:
                self._status["running"] = True
                self._status["last_error"] = ""
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                proc = self._proc

            last_stderr = ""
            if proc.stderr is not None:
                for line in proc.stderr:
                    if not self._running.is_set():
                        break
                    clean = line.strip()
                    if clean:
                        last_stderr = clean

            exit_code = proc.wait()
            with self._lock:
                self._status["running"] = False
                self._status["last_exit_code"] = exit_code
                if last_stderr:
                    self._status["last_error"] = last_stderr
                if self._running.is_set():
                    self._status["restarts"] += 1
                self._proc = None

            if not self._running.is_set():
                break
            time.sleep(0.8)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)


class OverlayApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.running = threading.Event()
        self.running.set()
        self._telemetry_lock = threading.Lock()
        self._telemetry: dict[str, Any] = {}
        self._sim = TelemetrySimulator()
        self._camera = CameraStreamer(
            running=self.running,
            source=args.source,
            mjpeg_url=args.mjpeg_url,
            camera_device=args.camera_device,
            video_file=args.video_file,
            width=args.width,
            height=args.height,
            fps=args.fps,
            camera_port=args.camera_port,
            jpeg_quality=args.jpeg_quality,
        )
        self._telemetry_thread: threading.Thread | None = None

    def start(self) -> None:
        self._camera.start()
        self._telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._telemetry_thread.start()

    def stop(self) -> None:
        self.running.clear()
        self._camera.stop()

    def _telemetry_loop(self) -> None:
        delay_s = 1.0 / float(self.args.telemetry_hz)
        while self.running.is_set():
            sample = self._sim.sample()
            with self._telemetry_lock:
                self._telemetry = sample
            time.sleep(delay_s)

    def latest_telemetry(self) -> dict[str, Any]:
        with self._telemetry_lock:
            return dict(self._telemetry)

    def status(self) -> dict[str, Any]:
        return {
            "camera": self._camera.status(),
            "source": self.args.source,
            "fps": self.args.fps,
            "resolution": f"{self.args.width}x{self.args.height}",
            "telemetry_hz": self.args.telemetry_hz,
        }

    def camera_upstream_url(self) -> str:
        if self.args.source == "mjpeg":
            return self.args.mjpeg_url
        return f"http://127.0.0.1:{self.args.camera_port}/live.mjpg"

    def camera_proxy(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        upstream = self.camera_upstream_url()
        req = Request(upstream, headers={"Connection": "close"})
        try:
            with urlopen(req, timeout=8) as response:
                ctype = response.headers.get("Content-Type", "")
                if "multipart/x-mixed-replace" not in ctype.lower():
                    ctype = "multipart/x-mixed-replace; boundary=ffmpeg"
                handler.send_response(200)
                handler.send_header("Content-Type", ctype)
                handler.send_header("Cache-Control", "no-cache")
                handler.send_header("Pragma", "no-cache")
                handler.end_headers()
                while self.running.is_set():
                    chunk = response.read(16384)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, URLError):
            handler.send_error(503, "Camera stream unavailable")


class OverlayHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[http.server.BaseHTTPRequestHandler],
        app: OverlayApp,
    ) -> None:
        super().__init__(server_address, request_handler)
        self.app = app


class OverlayRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    @property
    def app(self) -> OverlayApp:
        server = self.server
        assert isinstance(server, OverlayHTTPServer)
        return server.app

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        interval_s = 1.0 / max(1.0, float(self.app.args.sse_hz))
        try:
            while self.app.running.is_set():
                payload = self.app.latest_telemetry()
                msg = f"event: telemetry\ndata: {json.dumps(payload)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                time.sleep(interval_s)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/config":
            self._json(
                {
                    "camera_url": "/camera/live.mjpg",
                    "telemetry_url": "/api/telemetry/stream",
                    "source": self.app.args.source,
                    "target_fps": self.app.args.fps,
                }
            )
            return
        if path == "/api/status":
            self._json(self.app.status())
            return
        if path == "/api/telemetry":
            self._json(self.app.latest_telemetry())
            return
        if path == "/api/telemetry/stream":
            self._handle_sse()
            return
        if path == "/camera/live.mjpg":
            self.app.camera_proxy(self)
            return

        return super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        return


def get_lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Race overlay prototype")
    parser.add_argument("--port", type=int, default=8080, help="HTTP dashboard port")
    parser.add_argument("--camera-port", type=int, default=8090, help="Local MJPEG camera port")
    parser.add_argument(
        "--source",
        choices=("webcam", "testsrc", "file", "mjpeg"),
        default="webcam",
        help="Video input source",
    )
    parser.add_argument(
        "--mjpeg-url",
        default="",
        help="MJPEG URL when --source mjpeg",
    )
    parser.add_argument("--camera-device", default="/dev/video0", help="V4L2 camera path")
    parser.add_argument("--video-file", default="", help="Video file when --source file")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--jpeg-quality", type=int, default=5, help="ffmpeg MJPEG q:v (2-31)")
    parser.add_argument("--telemetry-hz", type=int, default=30, help="Telemetry simulation rate")
    parser.add_argument("--sse-hz", type=int, default=20, help="SSE publish rate")
    args = parser.parse_args()

    if args.source == "file" and not args.video_file:
        parser.error("--video-file is required when --source file")
    if args.source == "file" and not Path(args.video_file).exists():
        parser.error(f"Video file not found: {args.video_file}")
    if args.source == "mjpeg" and not args.mjpeg_url:
        parser.error("--mjpeg-url is required when --source mjpeg")
    return args


def main() -> None:
    args = parse_args()
    if not WEB_DIR.exists():
        raise RuntimeError(f"Web assets missing: {WEB_DIR}")

    app = OverlayApp(args)
    handler = partial(OverlayRequestHandler)
    server = OverlayHTTPServer(("0.0.0.0", args.port), handler, app)

    stop_once = threading.Event()

    def request_shutdown(*_: Any) -> None:
        if stop_once.is_set():
            return
        stop_once.set()
        app.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    app.start()
    lan_ip = get_lan_ip()
    print("=" * 64, flush=True)
    print("4OGS TELEMETRY OVERLAY", flush=True)
    print(f"Open: http://{lan_ip}:{args.port}", flush=True)
    print(f"Source: {args.source} @ {args.width}x{args.height} {args.fps}fps", flush=True)
    print("=" * 64, flush=True)

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        app.stop()
        server.server_close()


if __name__ == "__main__":
    main()
