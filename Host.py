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
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 16px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 12px; margin-bottom: 12px; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .k { color: #666; font-size: 12px; }
    .v { font-size: 22px; font-weight: 650; }
    pre { background: #f6f8fa; padding: 12px; border-radius: 12px; overflow:auto; }
    .ok { color: #0a7; font-weight: 700; }
    .bad { color: #c22; font-weight: 700; }
    .row { display:flex; gap:12px; flex-wrap:wrap; }
    .pill { padding: 2px 8px; border-radius: 999px; border: 1px solid #ddd; font-size: 12px; }
  </style>
</head>
<body>
  <h2>__APP_TITLE__</h2>

  <div class="card">
    <div class="row">
      <div>WebSocket: <span id="ws_status" class="bad">disconnected</span></div>
      <div>Data: <span id="data_status" class="bad">waiting</span> <span id="data_age" style="color:#666;font-size:12px;"></span></div>
      <div class="pill">Device: <span id="device">—</span></div>
      <div class="pill">Seq: <span id="seq">—</span></div>
      <div class="pill">Last: <span id="ts">—</span></div>
    </div>
  </div>

  <div class="card">
    <h3>Air</h3>
    <div class="grid">
      <div><div class="k">Temp (°C)</div><div class="v" id="air_t">—</div></div>
      <div><div class="k">Humidity (%)</div><div class="v" id="air_rh">—</div></div>
      <div><div class="k">Pressure (hPa)</div><div class="v" id="air_p">—</div></div>
    </div>
  </div>

  <div class="card">
    <h3>Water</h3>
    <div class="grid">
      <div><div class="k">Temp (°C)</div><div class="v" id="water_t">—</div></div>
      <div><div class="k">pH</div><div class="v" id="water_ph">—</div></div>
      <div><div class="k">EC (mS/cm)</div><div class="v" id="water_ec">—</div></div>
    </div>
  </div>

  <div class="card">
    <h3>Light / Level</h3>
    <div class="grid">
      <div><div class="k">Lux</div><div class="v" id="lux">—</div></div>
      <div><div class="k">Float</div><div class="v" id="float">—</div></div>
    </div>
  </div>

  <div class="card">
    <h3>Alerts (most recent)</h3>
    <pre id="alerts_box">loading…</pre>
  </div>

  <div class="card">
    <h3>Raw Event</h3>
    <pre id="raw">waiting for data…</pre>
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
    statusEl.className = "bad";
    ageEl.textContent = "";
    return;
  }

  const ageMs = Date.now() - lastUpdateMs;
  const ageS = (ageMs / 1000).toFixed(1);
  const LIVE_MS = 5000;

  if (ageMs < LIVE_MS) {
    statusEl.textContent = "live";
    statusEl.className = "ok";
  } else {
    statusEl.textContent = "stale";
    statusEl.className = "bad";
  }

  ageEl.textContent = `(${ageS}s ago)`;
}

setInterval(updateDataFreshnessUI, 500);

function setWsConnected(isConnected) {
  const el = document.getElementById("ws_status");
  if (isConnected) {
    el.textContent = "connected";
    el.className = "ok";
  } else {
    el.textContent = "disconnected";
    el.className = "bad";
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
  setText("float", e.level?.float);

  document.getElementById("raw").textContent = JSON.stringify(e, null, 2);
}

async function refreshAlertsBox() {
  try {
    const r = await fetch("/alerts");
    const j = await r.json();
    if (j && j.ok && j.alerts) {
      document.getElementById("alerts_box").textContent = JSON.stringify(j.alerts.slice(0, 10), null, 2);
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