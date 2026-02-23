const state = {
  connected: false,
  source: "unknown",
  cameraConnected: false,
  latest: null,
  smooth: {
    speed: 0,
    rpm: 0,
    gLat: 0,
    gLong: 0,
    throttle: 0,
    brake: 0,
  },
  fps: 0,
  frameCount: 0,
  lastFpsTs: performance.now(),
};

const dom = {
  sourceImg: document.getElementById("cameraSource"),
  mainFeed: document.getElementById("mainFeed"),
  insetFeed: document.getElementById("insetFeed"),
  speed: document.getElementById("speedValue"),
  rpmNumeric: document.getElementById("rpmNumeric"),
  gear: document.getElementById("gearValue"),
  lapTime: document.getElementById("lapTimeValue"),
  bestLap: document.getElementById("bestLapValue"),
  lastLap: document.getElementById("lastLapValue"),
  lapNumber: document.getElementById("lapNumberValue"),
  delta: document.getElementById("deltaValue"),
  throttleBar: document.getElementById("throttleBar"),
  brakeBar: document.getElementById("brakeBar"),
  trackDot: document.getElementById("trackDot"),
  gMeter: document.getElementById("gMeter"),
  rpmBars: document.getElementById("rpmBars"),
  cpu: document.getElementById("cpuValue"),
  gpu: document.getElementById("gpuValue"),
  temp: document.getElementById("tempValue"),
  mem: document.getElementById("memValue"),
  source: document.getElementById("sourceValue"),
  status: document.getElementById("statusValue"),
};

const mainCtx = dom.mainFeed.getContext("2d", { alpha: false });
const insetCtx = dom.insetFeed.getContext("2d", { alpha: false });
const gCtx = dom.gMeter.getContext("2d");

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function lerp(from, to, alpha) {
  return from + (to - from) * alpha;
}

function formatLap(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(3).padStart(6, "0")}`;
}

function setupRpmBars() {
  for (let i = 0; i < 30; i += 1) {
    const seg = document.createElement("i");
    if (i > 19) seg.classList.add("hot");
    dom.rpmBars.appendChild(seg);
  }
}

function resizeCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.max(1, Math.floor(width * dpr));
  canvas.height = Math.max(1, Math.floor(height * dpr));
}

function resizeAll() {
  resizeCanvas(dom.mainFeed);
  resizeCanvas(dom.insetFeed);
}

function drawVideoFrame(ctx, canvas, inset = false) {
  if (!dom.sourceImg.naturalWidth || !dom.sourceImg.naturalHeight) {
    ctx.fillStyle = "#05070a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ff6a23";
    ctx.font = `${Math.floor(canvas.height * 0.08)}px Orbitron`;
    ctx.fillText("NO SIGNAL", canvas.width * 0.06, canvas.height * 0.2);
    return;
  }

  const sw = dom.sourceImg.naturalWidth;
  const sh = dom.sourceImg.naturalHeight;
  const targetRatio = canvas.width / canvas.height;
  const sourceRatio = sw / sh;
  let sx = 0;
  let sy = 0;
  let sWidth = sw;
  let sHeight = sh;

  if (sourceRatio > targetRatio) {
    sWidth = sh * targetRatio;
    sx = (sw - sWidth) * 0.5;
  } else {
    sHeight = sw / targetRatio;
    sy = (sh - sHeight) * 0.5;
  }

  ctx.drawImage(dom.sourceImg, sx, sy, sWidth, sHeight, 0, 0, canvas.width, canvas.height);

  const shade = ctx.createLinearGradient(0, 0, 0, canvas.height);
  shade.addColorStop(0, "rgba(4,6,8,0.22)");
  shade.addColorStop(1, inset ? "rgba(4,6,8,0.52)" : "rgba(4,6,8,0.32)");
  ctx.fillStyle = shade;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawGMeter(gLat, gLong) {
  const width = dom.gMeter.width;
  const height = dom.gMeter.height;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.36;
  const maxG = 2.0;

  gCtx.clearRect(0, 0, width, height);
  gCtx.strokeStyle = "rgba(255, 111, 44, 0.7)";
  gCtx.lineWidth = 2;
  for (let i = 1; i <= 3; i += 1) {
    gCtx.beginPath();
    gCtx.arc(cx, cy, (radius * i) / 3, 0, Math.PI * 2);
    gCtx.stroke();
  }
  gCtx.strokeStyle = "rgba(255, 111, 44, 0.45)";
  gCtx.beginPath();
  gCtx.moveTo(cx - radius, cy);
  gCtx.lineTo(cx + radius, cy);
  gCtx.moveTo(cx, cy - radius);
  gCtx.lineTo(cx, cy + radius);
  gCtx.stroke();

  const px = cx + (clamp(gLat, -maxG, maxG) / maxG) * radius;
  const py = cy - (clamp(gLong, -maxG, maxG) / maxG) * radius;
  gCtx.fillStyle = "#1ee6d5";
  gCtx.beginPath();
  gCtx.arc(px, py, radius * 0.12, 0, Math.PI * 2);
  gCtx.fill();
  gCtx.strokeStyle = "rgba(30, 230, 213, 0.8)";
  gCtx.lineWidth = 1.5;
  gCtx.beginPath();
  gCtx.arc(px, py, radius * 0.22, 0, Math.PI * 2);
  gCtx.stroke();
}

function renderHud() {
  if (!state.latest) return;
  const t = state.latest;

  state.smooth.speed = lerp(state.smooth.speed, t.speed_mph || 0, 0.2);
  state.smooth.rpm = lerp(state.smooth.rpm, t.rpm || 0, 0.22);
  state.smooth.gLat = lerp(state.smooth.gLat, t.g_lat || 0, 0.18);
  state.smooth.gLong = lerp(state.smooth.gLong, t.g_long || 0, 0.18);
  state.smooth.throttle = lerp(state.smooth.throttle, t.throttle || 0, 0.24);
  state.smooth.brake = lerp(state.smooth.brake, t.brake || 0, 0.24);

  dom.speed.textContent = Math.round(state.smooth.speed).toString().padStart(3, "0");
  dom.rpmNumeric.textContent = (state.smooth.rpm / 1000).toFixed(1);
  dom.gear.textContent = t.gear || "N";

  const lap = t.lap || {};
  dom.lapTime.textContent = formatLap(lap.current_time_s || 0);
  dom.bestLap.textContent = formatLap(lap.best_time_s || 0);
  dom.lastLap.textContent = formatLap(lap.last_time_s || 0);
  dom.lapNumber.textContent = String(lap.number || 1);
  const delta = lap.predicted_delta_s || 0;
  dom.delta.textContent = `${delta >= 0 ? "+" : ""}${delta.toFixed(3)}`;
  dom.delta.classList.toggle("plus", delta >= 0);
  dom.delta.classList.toggle("minus", delta < 0);

  const rpmRatio = clamp(state.smooth.rpm / 9000, 0, 1);
  const activeBars = Math.round(rpmRatio * 30);
  [...dom.rpmBars.children].forEach((node, i) => {
    node.classList.toggle("active", i < activeBars);
  });

  dom.throttleBar.style.height = `${Math.round(state.smooth.throttle * 100)}%`;
  dom.brakeBar.style.height = `${Math.round(state.smooth.brake * 100)}%`;

  const track = t.track || {};
  const x = 24 + clamp(track.x || 0, 0, 1) * 192;
  const y = 14 + clamp(track.y || 0, 0, 1) * 192;
  dom.trackDot.setAttribute("cx", x.toFixed(1));
  dom.trackDot.setAttribute("cy", y.toFixed(1));

  drawGMeter(state.smooth.gLat, state.smooth.gLong);

  const system = t.system || {};
  dom.cpu.textContent = system.cpu_percent == null ? "--" : `${system.cpu_percent.toFixed(0)}%`;
  dom.gpu.textContent = system.gpu_percent == null ? "--" : `${system.gpu_percent.toFixed(0)}%`;
  dom.temp.textContent = system.temp_c == null ? "--" : `${system.temp_c.toFixed(1)}C`;
  dom.mem.textContent = system.mem_used_percent == null ? "--" : `${system.mem_used_percent.toFixed(0)}%`;
}

function renderFrame(now) {
  drawVideoFrame(mainCtx, dom.mainFeed, false);
  drawVideoFrame(insetCtx, dom.insetFeed, true);
  renderHud();

  state.frameCount += 1;
  if (now - state.lastFpsTs >= 1000) {
    state.fps = state.frameCount;
    state.frameCount = 0;
    state.lastFpsTs = now;
    dom.status.textContent = state.connected ? `live ${state.fps} fps` : "reconnecting";
  }
  requestAnimationFrame(renderFrame);
}

function connectTelemetry(url) {
  const stream = new EventSource(url);
  stream.addEventListener("telemetry", (event) => {
    state.connected = true;
    state.latest = JSON.parse(event.data);
  });
  stream.onerror = () => {
    state.connected = false;
  };
}

function attachCamera(url) {
  const reconnect = () => {
    state.cameraConnected = false;
    setTimeout(() => {
      dom.sourceImg.src = `${url}?t=${Date.now()}`;
    }, 600);
  };

  dom.sourceImg.onload = () => {
    state.cameraConnected = true;
  };
  dom.sourceImg.onerror = reconnect;
  dom.sourceImg.src = `${url}?t=${Date.now()}`;
}

async function init() {
  setupRpmBars();
  resizeAll();
  window.addEventListener("resize", resizeAll);

  const configRes = await fetch("/api/config");
  const config = await configRes.json();
  state.source = config.source || "unknown";
  dom.source.textContent = `${state.source.toUpperCase()} @ ${config.target_fps}FPS`;

  attachCamera(config.camera_url);
  connectTelemetry(config.telemetry_url);
  requestAnimationFrame(renderFrame);
}

init().catch((err) => {
  console.error(err);
  dom.status.textContent = "init failed";
});
