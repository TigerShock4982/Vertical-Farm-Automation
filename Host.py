# Host.py acts as both a Receiver and Application
#
# Responsibilities:
# - Receive ESP32 JSON packets over Wi-Fi (HTTP POST /ingest)
# - Validate basic structure
# - Store to SQLite via DB.py
# - Persist last snapshot across restarts
# - Serve a live dashboard for mobile devices (GET /)
# - Provide latest snapshot (GET /latest)
# - Push realtime updates (WebSocket /ws)
# - Create and store alerts + broadcast them (GET /alerts)

import json
import threading
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

import DB  # <--DB.py module

# ----------------------------
# CONFIG
# ----------------------------
APP_TITLE = "Vertical Farm Host"

# Showcase-friendly alert thresholds (tune later)
PH_LOW = 5.5
PH_HIGH = 6.8
EC_LOW = 0.8
EC_HIGH = 2.2
WATER_TEMP_HIGH_C = 26.0
ALERT_COOLDOWN_S = 10.0  # prevents spamming same alert every packet


# ----------------------------
# APP + SHARED STATE
# ----------------------------
app = FastAPI(title=APP_TITLE)

_state_lock = threading.Lock()
_latest_event: Optional[Dict[str, Any]] = None
_last_seq_per_device: Dict[str, int] = {}
# track last seen per device: { device: {"seq": int, "ts": iso str} }
_last_seen_per_device: Dict[str, Dict[str, Any]] = {}

_ws_lock = threading.Lock()
_ws_clients: Set[WebSocket] = set()

_alert_lock = threading.Lock()
_last_alert_time_by_key: Dict[str, float] = {}


# ----------------------------
# HELPERS
# ----------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def validate_sensor_event(event: Dict[str, Any]) -> Optional[str]:
    if not isinstance(event, dict):
        return "Event must be a JSON object"

    if event.get("type") != "sensor":
        return "Unsupported event type (expected type='sensor')"

    if "ts" not in event or "device" not in event or "seq" not in event:
        return "Missing required fields: ts, device, seq"

    try:
        int(event["seq"])
    except Exception:
        return "seq must be an integer"

    return None


def should_accept_event(device: str, seq: int, ts_iso: Optional[str]) -> bool:
    """Decide whether to accept an incoming event.

    Accept when:
    - We have not seen this device before.
    - Incoming seq is greater than last seq.
    - OR incoming timestamp is strictly newer than last seen timestamp (handles device reboot/seq reset).

    Reject when the event appears older-or-equal by both seq and timestamp.
    """
    from datetime import datetime
    with _state_lock:
        last = _last_seen_per_device.get(device)

        # No prior seen state: accept
        if last is None:
            _last_seen_per_device[device] = {"seq": seq, "ts": ts_iso}
            _last_seq_per_device[device] = seq
            return True

        last_seq = int(last.get("seq", -1))
        last_ts = last.get("ts")

        # Fast path: sequence increasing
        if seq > last_seq:
            _last_seen_per_device[device] = {"seq": seq, "ts": ts_iso}
            _last_seq_per_device[device] = seq
            return True

        # seq is <= last_seq; allow if timestamp is newer
        if ts_iso and last_ts:
            try:
                incoming_dt = datetime.fromisoformat(ts_iso)
                last_dt = datetime.fromisoformat(last_ts)
                if incoming_dt > last_dt:
                    # newer timestamp despite lower seq -> accept (likely reboot)
                    _last_seen_per_device[device] = {"seq": seq, "ts": ts_iso}
                    _last_seq_per_device[device] = seq
                    return True
            except Exception:
                # If timestamp parsing fails, fall through to reject
                pass

        # otherwise reject as duplicate/stale
        return False


async def ws_broadcast(payload: Dict[str, Any]) -> None:
    msg = json.dumps(payload, ensure_ascii=False)
    with _ws_lock:
        clients = list(_ws_clients)

    dead: list[WebSocket] = []
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)

    if dead:
        with _ws_lock:
            for ws in dead:
                _ws_clients.discard(ws)


async def evaluate_alerts(event: Dict[str, Any]) -> None:
    """Delegate alert evaluation to Alerts.py which persists via DB API.

    This function will call Alerts.evaluate_alerts and then broadcast any
    emitted alerts to websocket clients.
    """
    import Alerts
    alerts = await Alerts.evaluate_alerts(event)
    if not alerts:
        return
    for a in alerts:
        try:
            await ws_broadcast(a)
        except Exception:
            pass


# ----------------------------
# ROUTES
# ----------------------------
@app.on_event("startup")
def on_startup() -> None:
    DB.init_db()

    # Persistence: load last known sensor event
    latest = DB.get_latest_sensor_event()
    if latest is not None:
        global _latest_event
        _latest_event = latest

        # Restore last seq + timestamp per device (dedup still works after restart)
        try:
            dev = str(latest.get("device", ""))
            seq = int(latest.get("seq", 0))
            ts = str(latest.get("ts", ""))
            if dev:
                _last_seq_per_device[dev] = seq
                _last_seen_per_device[dev] = {"seq": seq, "ts": ts}
        except Exception:
            pass


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "host", "time": now_iso()}


@app.post("/ingest")
async def ingest(request: Request) -> JSONResponse:
    try:
        event = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON body"})

    err = validate_sensor_event(event)
    if err is not None:
        return JSONResponse(status_code=400, content={"ok": False, "error": err})

    device = str(event["device"])
    seq = int(event["seq"])

    if not should_accept_event(device, seq, event.get("ts")):
        return JSONResponse(status_code=200, content={"ok": True, "ignored": True})

    # Persist to DB
    DB.insert_sensor_event(event)

    # Update in-memory latest snapshot
    with _state_lock:
        global _latest_event
        _latest_event = event

    # Broadcast sensor event live
    await ws_broadcast(event)

    # Evaluate alerts (store + broadcast)
    await evaluate_alerts(event)

    return JSONResponse(status_code=200, content={"ok": True})


@app.get("/latest")
def latest() -> JSONResponse:
    with _state_lock:
        if _latest_event is None:
            return JSONResponse(status_code=200, content={"ok": False, "detail": "No data received yet"})
        return JSONResponse(status_code=200, content=_latest_event)


@app.get("/alerts")
def alerts() -> JSONResponse:
    # Recent alerts for UI / debug
    items = DB.get_recent_alerts(limit=50)
    return JSONResponse(status_code=200, content={"ok": True, "alerts": items})


@app.get("/api/history")
def api_history(limit: int = 500) -> JSONResponse:
    """Serve historical sensor data for charting."""
    events = DB.get_sensor_history(limit=limit)
    
    # Transform to time series format
    times = []
    air_temps = []
    air_humidity = []
    air_pressure = []
    water_temps = []
    water_ph = []
    water_ec = []
    light_lux = []
    
    for event in reversed(events):  # oldest first
        try:
            times.append(event.get("ts", ""))
            air = event.get("air", {})
            water = event.get("water", {})
            light = event.get("light", {})
            
            air_temps.append(air.get("t_c"))
            air_humidity.append(air.get("rh_pct"))
            air_pressure.append(air.get("p_hpa"))
            water_temps.append(water.get("t_c"))
            water_ph.append(water.get("ph"))
            water_ec.append(water.get("ec_ms_cm"))
            light_lux.append(light.get("lux"))
        except Exception:
            pass
    
    return JSONResponse(status_code=200, content={
        "ok": True,
        "times": times,
        "air_temp_c": air_temps,
        "air_humidity_pct": air_humidity,
        "air_pressure_hpa": air_pressure,
        "water_temp_c": water_temps,
        "water_ph": water_ph,
        "water_ec_ms_cm": water_ec,
        "light_lux": light_lux
    })


@app.get("/history", response_class=HTMLResponse)
def history_page() -> str:
    """Database history viewer with charts."""
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>__APP_TITLE__ - History</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f7fa;
      color: #333;
    }
    
    .header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 24px;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .header h1 { font-size: 28px; margin-bottom: 8px; }
    
    .nav {
      display: flex;
      gap: 12px;
      margin-top: 16px;
    }
    
    .nav-btn {
      padding: 8px 16px;
      border-radius: 6px;
      border: none;
      background: rgba(255,255,255,0.2);
      color: white;
      cursor: pointer;
      font-size: 14px;
      transition: all 0.3s;
    }
    
    .nav-btn:hover {
      background: rgba(255,255,255,0.3);
    }
    
    .nav-btn.active {
      background: white;
      color: #667eea;
      font-weight: 600;
    }
    
    .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
    
    .status-bar {
      background: white;
      padding: 16px;
      border-radius: 8px;
      margin-bottom: 24px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.05);
      display: flex;
      gap: 24px;
      flex-wrap: wrap;
      align-items: center;
    }
    
    .stat-item {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    
    .stat-label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 18px; font-weight: 600; color: #333; }
    
    .controls {
      background: white;
      padding: 16px;
      border-radius: 8px;
      margin-bottom: 24px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.05);
      display: flex;
      gap: 12px;
      align-items: center;
    }
    
    .btn {
      padding: 8px 16px;
      border-radius: 6px;
      border: 1px solid #ddd;
      background: white;
      cursor: pointer;
      font-size: 14px;
      transition: all 0.3s;
    }
    
    .btn:hover {
      background: #f5f7fa;
      border-color: #667eea;
      color: #667eea;
    }
    
    .btn.primary {
      background: #667eea;
      color: white;
      border-color: #667eea;
    }
    
    .btn.primary:hover {
      background: #5568d3;
    }
    
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
      gap: 24px;
      margin-bottom: 24px;
    }
    
    .chart-card {
      background: white;
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    
    .chart-card h3 {
      font-size: 16px;
      margin-bottom: 16px;
      color: #333;
      border-bottom: 2px solid #667eea;
      padding-bottom: 12px;
    }
    
    .chart-container {
      position: relative;
      height: 300px;
    }
    
    .loading { text-align: center; padding: 48px; color: #666; }
    .loading::after {
      content: "...";
      animation: dots 1.5s steps(4, end) infinite;
    }
    @keyframes dots {
      0%, 20% { content: ""; }
      40% { content: "."; }
      60% { content: ".."; }
      80%, 100% { content: "..."; }
    }
    
    .error { 
      background: #fee;
      color: #c33;
      padding: 16px;
      border-radius: 8px;
      margin-bottom: 16px;
    }
  </style>
</head>
<body>

<div class="header">
  <h1>__APP_TITLE__</h1>
  <p style="font-size: 14px; opacity: 0.95;">Historical Data & Analytics</p>
  <div class="nav">
    <button class="nav-btn" onclick="window.location='/'">Live Dashboard</button>
    <button class="nav-btn active">History & Charts</button>
  </div>
</div>

<div class="container">
  <div class="status-bar">
    <div class="stat-item">
      <span class="stat-label">Total Events</span>
      <span class="stat-value" id="event-count">—</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Time Span</span>
      <span class="stat-value" id="time-span">—</span>
    </div>
  </div>
  
  <div class="controls">
    <label for="limit-select">Show last</label>
    <select id="limit-select" class="btn">
      <option value="100">100 readings</option>
      <option value="250" selected>250 readings</option>
      <option value="500">500 readings</option>
    </select>
    <button class="btn primary" onclick="loadHistory()">Refresh</button>
  </div>
  
  <div id="error-box"></div>
  
  <div id="charts-container" class="chart-grid">
    <div class="loading">Loading data</div>
  </div>
</div>

<script>
const COLORS = {
  blue: 'rgb(102, 126, 234)',
  purple: 'rgb(118, 75, 162)',
  pink: 'rgb(237, 100, 166)',
  teal: 'rgb(34, 197, 94)',
  orange: 'rgb(249, 115, 22)',
  red: 'rgb(239, 68, 68)'
};

let charts = {};

function formatTime(isoStr) {
  try {
    const dt = new Date(isoStr);
    return dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return isoStr;
  }
}

function createChart(containerId, chartLabel, data, color) {
  const ctx = document.getElementById(containerId).getContext('2d');
  
  if (charts[containerId]) {
    charts[containerId].destroy();
  }
  
  charts[containerId] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.times.map(t => formatTime(t)),
      datasets: [{
        label: chartLabel,
        data: data.values,
        borderColor: color,
        backgroundColor: color.replace('rgb', 'rgba').replace(')', ', 0.1)'),
        borderWidth: 2,
        tension: 0.3,
        fill: true,
        pointRadius: 3,
        pointBackgroundColor: color,
        pointBorderColor: 'white',
        pointBorderWidth: 1,
        pointHoverRadius: 5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top' },
        filler: { propagate: true }
      },
      scales: {
        y: {
          beginAtZero: false,
          grid: { color: 'rgba(0,0,0,0.05)' }
        },
        x: {
          grid: { display: false }
        }
      }
    }
  });
}

async function loadHistory() {
  const limit = document.getElementById('limit-select').value;
  const container = document.getElementById('charts-container');
  const errorBox = document.getElementById('error-box');
  
  errorBox.innerHTML = '';
  container.innerHTML = '<div class="loading">Loading data</div>';
  
  try {
    const resp = await fetch('/api/history?limit=' + limit);
    const data = await resp.json();
    
    if (!data.ok) {
      errorBox.innerHTML = '<div class="error">Failed to load data: ' + (data.detail || 'Unknown error') + '</div>';
      container.innerHTML = '';
      return;
    }
    
    const times = data.times;
    if (!times || times.length === 0) {
      container.innerHTML = '<div class="error">No data available yet</div>';
      return;
    }
    
    // Update statistics
    document.getElementById('event-count').textContent = times.length;
    const firstTime = new Date(times[0]).toLocaleString();
    const lastTime = new Date(times[times.length - 1]).toLocaleString();
    document.getElementById('time-span').textContent = firstTime + ' to ' + lastTime;
    
    // Create chart cards
    const chartSpecs = [
      { title: 'Air Temperature (°C)', dataKey: 'air_temp_c', color: COLORS.orange, id: 'air-temp-chart' },
      { title: 'Air Humidity (%)', dataKey: 'air_humidity_pct', color: COLORS.blue, id: 'air-humidity-chart' },
      { title: 'Air Pressure (hPa)', dataKey: 'air_pressure_hpa', color: COLORS.purple, id: 'air-pressure-chart' },
      { title: 'Water Temperature (°C)', dataKey: 'water_temp_c', color: COLORS.teal, id: 'water-temp-chart' },
      { title: 'Water pH', dataKey: 'water_ph', color: COLORS.pink, id: 'water-ph-chart' },
      { title: 'Water EC (mS/cm)', dataKey: 'water_ec_ms_cm', color: COLORS.red, id: 'water-ec-chart' },
      { title: 'Light Intensity (lux)', dataKey: 'light_lux', color: COLORS.orange, id: 'light-lux-chart' }
    ];
    
    container.innerHTML = chartSpecs.map(spec => 
      '<div class="chart-card"><h3>' + spec.title + '</h3><div class="chart-container"><canvas id="' + spec.id + '"></canvas></div></div>'
    ).join('');
    
    // Create all charts
    chartSpecs.forEach(spec => {
      setTimeout(() => {
        createChart(spec.id, spec.title, { times, values: data[spec.dataKey] }, spec.color);
      }, 100);
    });
    
  } catch (err) {
    errorBox.innerHTML = '<div class="error">Error: ' + err.message + '</div>';
    container.innerHTML = '';
  }
}

// Load on page load
window.addEventListener('load', loadHistory);

// Refresh every 10 seconds
setInterval(loadHistory, 10000);
</script>

</body>
</html>
"""
    return html.replace("__APP_TITLE__", APP_TITLE)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    # IMPORTANT: do not use f-strings here because CSS/JS contain many { } braces.
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>__APP_TITLE__</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f7fa;
      color: #333;
    }
    
    .header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 24px;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .header h1 { font-size: 28px; margin-bottom: 8px; }
    
    .nav {
      display: flex;
      gap: 12px;
      margin-top: 16px;
    }
    
    .nav-btn {
      padding: 8px 16px;
      border-radius: 6px;
      border: none;
      background: rgba(255,255,255,0.2);
      color: white;
      cursor: pointer;
      font-size: 14px;
      transition: all 0.3s;
    }
    
    .nav-btn:hover {
      background: rgba(255,255,255,0.3);
    }
    
    .nav-btn.active {
      background: white;
      color: #667eea;
      font-weight: 600;
    }
    
    .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
    
    .status-bar {
      background: white;
      padding: 16px;
      border-radius: 8px;
      margin-bottom: 24px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.05);
      display: flex;
      gap: 24px;
      flex-wrap: wrap;
      align-items: center;
    }
    
    .stat-item {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    
    .stat-label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 18px; font-weight: 600; color: #333; }
    
    .stat-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      background: #f0f4ff;
      border-radius: 6px;
      font-size: 13px;
      color: #667eea;
    }
    
    .stat-badge.ok { background: #dcfce7; color: #166534; }
    .stat-badge.err { background: #fee2e2; color: #991b1b; }
    
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 20px;
      margin-bottom: 24px;
    }
    
    .card {
      background: white;
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.05);
      border-top: 4px solid #667eea;
    }
    
    .card h3 { font-size: 16px; margin-bottom: 16px; color: #333; }
    
    .sensor-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 16px;
    }
    
    .sensor-item {
      background: #f9fafb;
      padding: 12px;
      border-radius: 6px;
      border-left: 3px solid #667eea;
    }
    
    .sensor-label { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
    .sensor-value { font-size: 20px; font-weight: 600; color: #333; }
    .sensor-unit { font-size: 12px; color: #999; margin-left: 2px; }
    
    .alerts-section {
      grid-column: 1 / -1;
    }
    
    .alert-item {
      background: #fff8f0;
      border-left: 4px solid #f97316;
      padding: 12px;
      border-radius: 4px;
      margin-bottom: 8px;
      font-size: 13px;
    }
    
    .alert-item.warn { background: #fef3c7; border-left-color: #eab308; }
    .alert-item.crit { background: #fee2e2; border-left-color: #ef4444; }
    .alert-item.info { background: #dbeafe; border-left-color: #3b82f6; }
    
    pre { 
      background: #1f2937; 
      color: #e5e7eb;
      padding: 12px; 
      border-radius: 6px; 
      overflow: auto;
      font-size: 12px;
      line-height: 1.4;
    }
  </style>
</head>
<body>

<div class="header">
  <h1>__APP_TITLE__</h1>
  <p style="font-size: 14px; opacity: 0.95;">Live Sensor Monitoring</p>
  <div class="nav">
    <button class="nav-btn active" onclick="window.location='/'">Live Dashboard</button>
    <button class="nav-btn" onclick="window.location='/history'">History & Charts</button>
  </div>
</div>

<div class="container">
  <div class="status-bar">
    <div class="stat-item">
      <span class="stat-label">WebSocket</span>
      <span class="stat-badge" id="ws_status">disconnected</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Data Status</span>
      <span class="stat-badge" id="data_status">waiting</span>
      <span id="data_age" style="color:#666;font-size:12px;"></span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Device</span>
      <span class="stat-value" id="device">—</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Sequence</span>
      <span class="stat-value" id="seq">—</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Last Update</span>
      <span class="stat-value" id="ts" style="font-size:13px;">—</span>
    </div>
  </div>

  <div class="grid">
    <div class="card" style="border-top-color: #f97316;">
      <h3>Air Quality</h3>
      <div class="sensor-grid">
        <div class="sensor-item" style="border-left-color: #f97316;">
          <div class="sensor-label">Temperature</div>
          <div><span class="sensor-value" id="air_t">—</span><span class="sensor-unit">°C</span></div>
        </div>
        <div class="sensor-item" style="border-left-color: #3b82f6;">
          <div class="sensor-label">Humidity</div>
          <div><span class="sensor-value" id="air_rh">—</span><span class="sensor-unit">%</span></div>
        </div>
        <div class="sensor-item" style="border-left-color: #8b5cf6;">
          <div class="sensor-label">Pressure</div>
          <div><span class="sensor-value" id="air_p">—</span><span class="sensor-unit">hPa</span></div>
        </div>
      </div>
    </div>

    <div class="card" style="border-top-color: #06b6d4;">
      <h3>Water Systems</h3>
      <div class="sensor-grid">
        <div class="sensor-item" style="border-left-color: #06b6d4;">
          <div class="sensor-label">Temperature</div>
          <div><span class="sensor-value" id="water_t">—</span><span class="sensor-unit">°C</span></div>
        </div>
        <div class="sensor-item" style="border-left-color: #ec4899;">
          <div class="sensor-label">pH Level</div>
          <div><span class="sensor-value" id="water_ph">—</span></div>
        </div>
        <div class="sensor-item" style="border-left-color: #10b981;">
          <div class="sensor-label">EC</div>
          <div><span class="sensor-value" id="water_ec">—</span><span class="sensor-unit">mS/cm</span></div>
        </div>
      </div>
    </div>

    <div class="card" style="border-top-color: #eab308;">
      <h3>Light & Level</h3>
      <div class="sensor-grid">
        <div class="sensor-item" style="border-left-color: #eab308;">
          <div class="sensor-label">Illuminance</div>
          <div><span class="sensor-value" id="lux">—</span><span class="sensor-unit">lux</span></div>
        </div>
        <div class="sensor-item" style="border-left-color: #06b6d4;">
          <div class="sensor-label">Water Level</div>
          <div><span class="sensor-value" id="float">—</span></div>
        </div>
      </div>
    </div>

    <div class="card alerts-section" style="border-top-color: #ef4444;">
      <h3>Recent Alerts</h3>
      <div id="alerts_box" style="max-height: 250px; overflow-y: auto;">
        <p style="color: #999; text-align: center; padding: 24px;">No alerts yet</p>
      </div>
    </div>

    <div class="card alerts-section">
      <h3>Raw Event (JSON)</h3>
      <pre id="raw">waiting for data…</pre>
    </div>
  </div>
</div>

<script>
function setText(id, v) {
  document.getElementById(id).textContent = (v === undefined || v === null) ? "—" : v;
}

let lastUpdateMs = 0;

function markFresh() {
  lastUpdateMs = Date.now();
}

function updateDataFreshnessUI() {
  const statusEl = document.getElementById("data_status");
  const ageEl = document.getElementById("data_age");

  if (lastUpdateMs === 0) {
    statusEl.textContent = "waiting";
    statusEl.className = "stat-badge err";
    ageEl.textContent = "";
    return;
  }

  const ageMs = Date.now() - lastUpdateMs;
  const ageS = (ageMs / 1000).toFixed(1);
  const LIVE_MS = 5000;

  if (ageMs < LIVE_MS) {
    statusEl.textContent = "live";
    statusEl.className = "stat-badge ok";
  } else {
    statusEl.textContent = "stale";
    statusEl.className = "stat-badge err";
  }

  ageEl.textContent = ageS + "s ago";
}

setInterval(updateDataFreshnessUI, 500);

function setWsConnected(isConnected) {
  const el = document.getElementById("ws_status");
  if (isConnected) {
    el.textContent = "connected";
    el.className = "stat-badge ok";
  } else {
    el.textContent = "disconnected";
    el.className = "stat-badge err";
  }
}

function showEvent(e) {
  markFresh();

  setText("device", e.device);
  setText("seq", e.seq);
  setText("ts", e.ts);

  setText("air_t", e.air?.t_c);
  setText("air_rh", e.air?.rh_pct);
  setText("air_p", e.air?.p_hpa);

  setText("water_t", e.water?.t_c);
  setText("water_ph", e.water?.ph);
  setText("water_ec", e.water?.ec_ms_cm);

  setText("lux", e.light?.lux);
  setText("float", e.level?.float === 1 ? "HIGH" : "LOW");

  document.getElementById("raw").textContent = JSON.stringify(e, null, 2);
}

async function refreshAlertsBox() {
  try {
    const r = await fetch("/alerts");
    const j = await r.json();
    if (j && j.ok && j.alerts) {
      const alertsBox = document.getElementById("alerts_box");
      if (j.alerts.length === 0) {
        alertsBox.innerHTML = '<p style="color: #999; text-align: center; padding: 24px;">No alerts yet</p>';
      } else {
        alertsBox.innerHTML = j.alerts.slice(0, 10).map(a => {
          const severityClass = a.severity.toLowerCase();
          return '<div class="alert-item ' + severityClass + '"><strong>' + a.code + '</strong> [' + a.severity + ']<br/>' + a.message + '<br/><span style="color:#666;font-size:11px;">' + a.ts + '</span></div>';
        }).join('');
      }
    }
  } catch (e) {}
}

setInterval(refreshAlertsBox, 2000);

(async function() {
  // Load latest immediately
  try {
    const r = await fetch("/latest");
    const j = await r.json();
    if (j && j.type === "sensor") {
      showEvent(j);
    }
  } catch (err) {}

  // initial alerts
  refreshAlertsBox();

  // WebSocket
  const proto = (location.protocol === "https:") ? "wss" : "ws";
  const wsUrl = proto + "://" + location.host + "/ws";
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => { setWsConnected(true); };
  ws.onclose = () => { setWsConnected(false); };
  ws.onerror = () => { setWsConnected(false); };

  ws.onmessage = (msg) => {
    try {
      const e = JSON.parse(msg.data);
      if (e && e.type === "sensor") {
        showEvent(e);
      } else if (e && e.type === "alert") {
        // pull alerts on new alert event
        refreshAlertsBox();
      }
    } catch (err) {}
  };
})();
</script>

</body>
</html>
"""
    return html.replace("__APP_TITLE__", APP_TITLE)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    with _ws_lock:
        _ws_clients.add(ws)

    # Send latest immediately if we have it
    with _state_lock:
        snap = _latest_event
    if snap is not None:
        try:
            await ws.send_text(json.dumps(snap, ensure_ascii=False))
        except Exception:
            pass

    try:
        # Keep connection open; server pushes updates via ws_broadcast()
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)