"""
Conveyor WebHMI Bridge — pyads + aiohttp WebSocket server.

Reads GVL_Conveyor.stHMI.* from TwinCAT PLC via ADS, broadcasts state
over WebSocket at ~100 ms interval, and writes operator commands back.

Usage:
    python bridge.py              # connect to PLC
    python bridge.py --demo       # simulated data (no PLC needed)
"""

import argparse
import asyncio
import json
import logging
import math
import random
import time
from pathlib import Path

from aiohttp import web

try:
    import pyads
except ImportError:
    pyads = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bridge")

CONFIG_PATH = Path(__file__).parent / "config.json"
STATIC_DIR = Path(__file__).parent


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ADS Manager — real PLC communication
# ---------------------------------------------------------------------------

READ_SYMBOLS = [
    ("eSystemStatus", pyads.PLCTYPE_UINT if pyads else None),
    ("fBeltSpeed", pyads.PLCTYPE_LREAL if pyads else None),
    ("fMotorLoad", pyads.PLCTYPE_LREAL if pyads else None),
    ("nTodayCount", pyads.PLCTYPE_UDINT if pyads else None),
    ("bS1_Entry", pyads.PLCTYPE_BOOL if pyads else None),
    ("bS2_Mid", pyads.PLCTYPE_BOOL if pyads else None),
    ("bS3_Exit", pyads.PLCTYPE_BOOL if pyads else None),
    ("bStopperActive", pyads.PLCTYPE_BOOL if pyads else None),
    ("bEStop", pyads.PLCTYPE_BOOL if pyads else None),
    ("bDirectionFwd", pyads.PLCTYPE_BOOL if pyads else None),
    ("fWorkPosition", pyads.PLCTYPE_LREAL if pyads else None),
    ("fSpeedSetpoint", pyads.PLCTYPE_LREAL if pyads else None),
    ("eMode", pyads.PLCTYPE_UINT if pyads else None),
    ("nAlarmWriteIndex", pyads.PLCTYPE_UINT if pyads else None),
]

ALARM_FIELDS = [
    ("sTimestamp", pyads.PLCTYPE_STRING if pyads else None),
    ("eSeverity", pyads.PLCTYPE_UINT if pyads else None),
    ("sMessage", pyads.PLCTYPE_STRING if pyads else None),
    ("bActive", pyads.PLCTYPE_BOOL if pyads else None),
]

WRITE_RULES = {
    "bStart": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bStop": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bReset": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "fSpeedSetpoint": (pyads.PLCTYPE_LREAL if pyads else None, None),  # clamped at write time
    "eMode": (pyads.PLCTYPE_UINT if pyads else None, lambda v: int(v) if int(v) in (0, 1) else None),
    "bEStopCmd": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bEStopConfirmed": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bDirectionCmd": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bStopperManualCmd": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
    "bJog": (pyads.PLCTYPE_BOOL if pyads else None, lambda v: bool(v)),
}


class AdsManager:
    def __init__(self, cfg):
        self._ams = cfg["ams_net_id"]
        self._port = cfg["ads_port"]
        self._prefix = cfg["hmi_symbol_prefix"]
        self._speed_min = cfg["speed_setpoint_min"]
        self._speed_max = cfg["speed_setpoint_max"]
        self._plc = None
        self._last_alarm_idx = -1
        self._alarm_cache = [None] * 20

    def connect(self):
        if self._plc is not None:
            return
        self._plc = pyads.Connection(self._ams, self._port)
        self._plc.open()
        log.info("ADS connected to %s:%d", self._ams, self._port)

    def disconnect(self):
        if self._plc:
            try:
                self._plc.close()
            except Exception:
                pass
            self._plc = None

    def _sym(self, name):
        return f"{self._prefix}.{name}"

    def read_state(self):
        state = {}
        for name, plc_type in READ_SYMBOLS:
            state[name] = self._plc.read_by_name(self._sym(name), plc_type)

        # Read alarms only when write index changes
        idx = state["nAlarmWriteIndex"]
        if idx != self._last_alarm_idx:
            self._last_alarm_idx = idx
            for i in range(20):
                alarm = {}
                for field, ftype in ALARM_FIELDS:
                    alarm[field] = self._plc.read_by_name(
                        self._sym(f"aAlarms[{i}].{field}"), ftype
                    )
                self._alarm_cache[i] = alarm

        state["alarms"] = [a for a in self._alarm_cache if a is not None]
        return state

    def write_command(self, symbol, value):
        if symbol not in WRITE_RULES:
            log.warning("Rejected unknown symbol: %s", symbol)
            return False
        plc_type, validator = WRITE_RULES[symbol]
        if symbol == "fSpeedSetpoint":
            value = max(self._speed_min, min(self._speed_max, float(value)))
        elif validator:
            value = validator(value)
            if value is None:
                log.warning("Rejected invalid value for %s", symbol)
                return False
        self._plc.write_by_name(self._sym(symbol), value, plc_type)
        log.info("Wrote %s = %s", symbol, value)
        return True


# ---------------------------------------------------------------------------
# Demo Manager — simulated data for development without PLC
# ---------------------------------------------------------------------------

class DemoManager:
    def __init__(self):
        self._status = 0  # Stopped
        self._speed = 0.0
        self._setpoint = 1.0
        self._mode = 0
        self._count = 0
        self._alarms = []
        self._alarm_idx = 0
        self._t0 = time.time()
        self._s3_cooldown = 0
        self._add_alarm(0, "System initialized")

    def connect(self):
        log.info("Demo mode — no PLC connection")

    def disconnect(self):
        pass

    def _add_alarm(self, severity, message):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {"sTimestamp": ts, "eSeverity": severity, "sMessage": message, "bActive": True}
        if len(self._alarms) < 20:
            self._alarms.append(entry)
        else:
            self._alarms[self._alarm_idx % 20] = entry
        self._alarm_idx += 1

    def read_state(self):
        t = time.time() - self._t0

        # Simulate belt speed approaching setpoint
        if self._status == 2:  # Running
            self._speed += (self._setpoint - self._speed) * 0.1
        elif self._status in (0, 4, 5):
            self._speed *= 0.9
            if self._speed < 0.01:
                self._speed = 0.0

        # Simulate sensors
        running = self._status == 2
        s1 = running and (math.sin(t * 2.0) > 0.7)
        s2 = running and (math.sin(t * 2.0 - 1.0) > 0.7)
        s3 = running and (math.sin(t * 2.0 - 2.0) > 0.7)

        # Count pieces on S3 rising edge
        if s3 and self._s3_cooldown <= 0:
            self._count += 1
            self._s3_cooldown = 20  # ~2s at 100ms
        self._s3_cooldown = max(0, self._s3_cooldown - 1)

        load = 40 + (self._speed / 2.0) * 40 + (10 if (s1 or s2 or s3) else 0) if running else 0
        stopper = self._mode == 0 and s2 and running

        # Random alarm every ~30s
        if running and random.random() < 0.003:
            sev = random.choice([0, 0, 1])
            msgs = ["Vibration detected", "Temperature high", "Belt slip warning", "Sensor check"]
            self._add_alarm(sev, random.choice(msgs))

        return {
            "eSystemStatus": self._status,
            "fBeltSpeed": round(self._speed, 3),
            "fMotorLoad": round(load, 1),
            "nTodayCount": self._count,
            "bS1_Entry": s1,
            "bS2_Mid": s2,
            "bS3_Exit": s3,
            "bStopperActive": stopper,
            "bEStop": False,
            "bDirectionFwd": True,
            "fWorkPosition": 0.0,
            "fSpeedSetpoint": self._setpoint,
            "eMode": self._mode,
            "nAlarmWriteIndex": self._alarm_idx,
            "alarms": list(self._alarms),
        }

    def write_command(self, symbol, value):
        if symbol == "bStart" and value:
            if self._status == 0:
                self._status = 1  # Starting
                self._add_alarm(0, "System starting")
                asyncio.get_event_loop().call_later(1.5, self._set_running)
        elif symbol == "bStop" and value:
            if self._status == 2:
                self._status = 3  # Stopping
                self._add_alarm(0, "System stopping")
                asyncio.get_event_loop().call_later(1.0, self._set_stopped)
        elif symbol == "bReset" and value:
            if self._status in (4, 5):
                self._status = 0
                self._add_alarm(0, "System reset")
        elif symbol == "fSpeedSetpoint":
            self._setpoint = max(0.1, min(2.0, float(value)))
        elif symbol == "eMode":
            self._mode = int(value) if int(value) in (0, 1) else self._mode
        log.info("Demo cmd: %s = %s", symbol, value)
        return True

    def _set_running(self):
        if self._status == 1:
            self._status = 2
            self._add_alarm(0, "System running")

    def _set_stopped(self):
        if self._status == 3:
            self._status = 0
            self._add_alarm(0, "System stopped")


# ---------------------------------------------------------------------------
# WebSocket broadcast loop
# ---------------------------------------------------------------------------

clients: set[web.WebSocketResponse] = set()


async def poll_loop(manager, interval_s):
    while True:
        try:
            state = manager.read_state()
            msg = json.dumps({
                "type": "state",
                "ts": time.time(),
                "status": {
                    "eSystemStatus": state["eSystemStatus"],
                    "fBeltSpeed": state["fBeltSpeed"],
                    "fMotorLoad": state["fMotorLoad"],
                    "nTodayCount": state["nTodayCount"],
                },
                "sensors": {
                    "bS1_Entry": state["bS1_Entry"],
                    "bS2_Mid": state["bS2_Mid"],
                    "bS3_Exit": state["bS3_Exit"],
                },
                "actuators": {
                    "bStopperActive": state["bStopperActive"],
                    "bEStop": state["bEStop"],
                    "bDirectionFwd": state.get("bDirectionFwd", True),
                    "fWorkPosition": state.get("fWorkPosition", 0.0),
                },
                "commands": {
                    "fSpeedSetpoint": state["fSpeedSetpoint"],
                    "eMode": state["eMode"],
                },
                "alarms": state["alarms"],
                "nAlarmWriteIndex": state["nAlarmWriteIndex"],
            })
            dead = set()
            for ws in clients:
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead.add(ws)
            clients.difference_update(dead)
        except Exception as exc:
            log.error("Poll error: %s", exc)
            manager.disconnect()
            await asyncio.sleep(5)
            try:
                manager.connect()
            except Exception:
                pass
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------------
# HTTP + WebSocket handlers
# ---------------------------------------------------------------------------

async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients.add(ws)
    log.info("Client connected (%d total)", len(clients))
    manager = request.app["manager"]
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "cmd":
                        manager.write_command(data["symbol"], data["value"])
                except Exception as exc:
                    log.warning("Bad command: %s", exc)
    finally:
        clients.discard(ws)
        log.info("Client disconnected (%d remaining)", len(clients))
    return ws


async def index_handler(request):
    return web.FileResponse(STATIC_DIR / "index.html")


async def on_startup(app):
    manager = app["manager"]
    try:
        manager.connect()
    except Exception as exc:
        log.error("Initial ADS connect failed: %s (will retry)", exc)
    cfg = app["config"]
    interval = cfg["poll_interval_ms"] / 1000.0
    app["poll_task"] = asyncio.create_task(poll_loop(manager, interval))


async def on_cleanup(app):
    app["poll_task"].cancel()
    app["manager"].disconnect()


def main():
    parser = argparse.ArgumentParser(description="Conveyor WebHMI Bridge")
    parser.add_argument("--demo", action="store_true", help="Run with simulated data")
    parser.add_argument("--port", type=int, default=None, help="Override HTTP port")
    args = parser.parse_args()

    cfg = load_config()
    http_port = args.port or cfg["http_port"]

    manager = DemoManager() if args.demo else AdsManager(cfg)

    app = web.Application()
    app["manager"] = manager
    app["config"] = cfg
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)

    log.info("Starting bridge on http://localhost:%d %s", http_port, "(DEMO)" if args.demo else "")
    web.run_app(app, host="0.0.0.0", port=http_port, print=None)


if __name__ == "__main__":
    main()
