import time
from typing import Any, Dict, List
import DB

# Alert thresholds (tweakable)
PH_LOW = 5.5
PH_HIGH = 6.8
EC_LOW = 0.8
EC_HIGH = 2.2
WATER_TEMP_HIGH_C = 26.0
ALERT_COOLDOWN_S = 10.0

_last_alert_time_by_key: Dict[str, float] = {}


def _cooldown_ok(key: str) -> bool:
    t = time.time()
    last = _last_alert_time_by_key.get(key)
    if last is not None and (t - last) < ALERT_COOLDOWN_S:
        return False
    _last_alert_time_by_key[key] = t
    return True


async def evaluate_alerts(event: Dict[str, Any]) -> None:
    """Evaluate alert rules for a sensor event, store and broadcast via DB/WS.

    This module owns the rules + dedupe (cooldown). It uses DB's public API
    to persist alerts. WebSocket broadcasting is done by the caller (Host)
    by observing DB rows or by passing in a broadcaster; for simplicity the
    Host will call the DB insert then call its ws_broadcast as before.
    """
    device = str(event.get("device", ""))
    ts = str(event.get("ts", ""))

    water = event.get("water") or {}
    level = event.get("level") or {}

    water_ph = water.get("ph")
    water_ec = water.get("ec_ms_cm")
    water_t = water.get("t_c")
    float_state = level.get("float")

    alerts_to_emit: List[Dict[str, Any]] = []

    # CRITICAL: water low
    if float_state == 0:
        code = "WATER_LOW"
        if _cooldown_ok(f"{device}:{code}"):
            alerts_to_emit.append({
                "type": "alert",
                "ts": ts,
                "device": device,
                "severity": "CRIT",
                "code": code,
                "message": "Reservoir level is LOW (float=0).",
            })

    # WARN: pH out of range
    if isinstance(water_ph, (int, float)):
        if water_ph < PH_LOW:
            code = "PH_LOW"
            if _cooldown_ok(f"{device}:{code}"):
                alerts_to_emit.append({
                    "type": "alert",
                    "ts": ts,
                    "device": device,
                    "severity": "WARN",
                    "code": code,
                    "message": f"pH is low: {water_ph:.2f} (< {PH_LOW}).",
                })
        if water_ph > PH_HIGH:
            code = "PH_HIGH"
            if _cooldown_ok(f"{device}:{code}"):
                alerts_to_emit.append({
                    "type": "alert",
                    "ts": ts,
                    "device": device,
                    "severity": "WARN",
                    "code": code,
                    "message": f"pH is high: {water_ph:.2f} (> {PH_HIGH}).",
                })

    # WARN: EC out of range
    if isinstance(water_ec, (int, float)):
        if water_ec < EC_LOW:
            code = "EC_LOW"
            if _cooldown_ok(f"{device}:{code}"):
                alerts_to_emit.append({
                    "type": "alert",
                    "ts": ts,
                    "device": device,
                    "severity": "WARN",
                    "code": code,
                    "message": f"EC is low: {water_ec:.2f} mS/cm (< {EC_LOW}).",
                })
        if water_ec > EC_HIGH:
            code = "EC_HIGH"
            if _cooldown_ok(f"{device}:{code}"):
                alerts_to_emit.append({
                    "type": "alert",
                    "ts": ts,
                    "device": device,
                    "severity": "WARN",
                    "code": code,
                    "message": f"EC is high: {water_ec:.2f} mS/cm (> {EC_HIGH}).",
                })

    # WARN: Water temp too high
    if isinstance(water_t, (int, float)) and water_t > WATER_TEMP_HIGH_C:
        code = "WATER_TEMP_HIGH"
        if _cooldown_ok(f"{device}:{code}"):
            alerts_to_emit.append({
                "type": "alert",
                "ts": ts,
                "device": device,
                "severity": "WARN",
                "code": code,
                "message": f"Water temp is high: {water_t:.2f}°C (> {WATER_TEMP_HIGH_C}).",
            })

    # Persist + (caller may broadcast)
    for a in alerts_to_emit:
        DB.insert_alert(
            ts=a["ts"],
            device=a.get("device"),
            severity=a["severity"],
            code=a["code"],
            message=a["message"],
            raw={"event": event, "alert": a},
        )

    # Return the alerts so caller can broadcast them if needed
    return alerts_to_emit
