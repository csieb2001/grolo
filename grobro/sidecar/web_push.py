#!/usr/bin/env python3
"""web-push: schickt die bereinigten Messwerte alle 30 s an die GroLo-Website (Vercel).

Bildet aus dem GroBro-State dieselben Größen wie das Grafana-Dashboard:
  pv_w  = Summe Spannung × Strom der Strings          out_w = Register 116 − 30000
  bat_w = pv_w − out_w (positiv = laden)              soc, Packs, Temperaturen, Modus, Status
Mittelt über das Intervall, puffert bei Ausfall (bis 24 h) und schickt nach.

Umgebung: WEB_URL (z. B. https://grolo.vercel.app), WEB_TOKEN, PUSH_INTERVAL (s, Standard 30), MQTT_HOST/MQTT_PORT, HA_BASE_TOPIC.
"""
import json, logging, os, threading, time, urllib.request, urllib.error
from collections import deque
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
URL = os.getenv("WEB_URL", "").rstrip("/"); TOKEN = os.getenv("WEB_TOKEN", "")
INTERVAL = int(os.getenv("PUSH_INTERVAL", "30"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("web-push")

lock = threading.Lock()
acc = {}            # device -> {"n": int, sums: {...}, last: state}
queue = deque(maxlen=2880)   # gepufferte Samples (24 h bei 30 s)
info_pending = {}   # device -> info dict


def derive(st):
    pv = sum(float(st.get(f"pv{i}Voltage", 0) or 0) * float(st.get(f"pv{i}Current", 0) or 0) for i in range(1, 5))
    out = float(st.get("onGridPower", 30000) or 30000) - 30000.0
    return {
        "pv_w": pv, "out_w": out, "bat_w": pv - out, "soc": float(st.get("totalBatteryPackSoc", 0) or 0),
        "soc1": st.get("battery1Soc"), "soc2": st.get("battery2Soc"), "soc3": st.get("battery3Soc"), "soc4": st.get("battery4Soc"),
        "temp_sys": st.get("systemTemp"), "temp_bat1": st.get("battery1Temp"), "temp_bat2": st.get("battery2Temp"),
        "pv_v": [st.get(f"pv{i}Voltage") for i in range(1, 5)], "pv_a": [st.get(f"pv{i}Current") for i in range(1, 5)],
        "packs": st.get("batteryPackageQuantity"), "status": st.get("totalBatteryPackChargingStatus"), "mode": st.get("workMode"),
    }


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        if parts[-1] == "state":
            st = json.loads(msg.payload); device = parts[-2]; d = derive(st)
            with lock:
                a = acc.setdefault(device, {"n": 0, "pv": 0.0, "out": 0.0, "bat": 0.0, "soc": 0.0, "last": None})
                a["n"] += 1; a["pv"] += d["pv_w"]; a["out"] += d["out_w"]; a["bat"] += d["bat_w"]; a["soc"] += d["soc"]; a["last"] = d
        elif parts[-1] == "dongle":
            d = json.loads(msg.payload); device = parts[-2]
            with lock:
                info_pending[device] = {"device": device, "model": "Growatt NEXA 2000", "dongle_model": d.get("model_id"), "dongle_sw": d.get("sw_version"),
                                        "dongle_hw": d.get("hw_version"), "wifi_dbm": d.get("wifi_signal")}
    except Exception as e:
        LOG.debug("message %s: %s", msg.topic, e)


def flush():
    with lock:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for device, a in acc.items():
            if a["n"] == 0 or not a["last"]:
                continue
            s = dict(a["last"]); n = a["n"]
            s.update({"ts": now, "device": device, "pv_w": round(a["pv"] / n, 2), "out_w": round(a["out"] / n, 2), "bat_w": round(a["bat"] / n, 2), "soc": round(a["soc"] / n, 2)})
            queue.append(s); a.update({"n": 0, "pv": 0.0, "out": 0.0, "bat": 0.0, "soc": 0.0})
        info = dict(info_pending); info_pending.clear()
    if not queue and not info:
        return
    batch = list(queue)[:200]
    body = {"samples": batch}
    if info:
        body["info"] = next(iter(info.values()))
    req = urllib.request.Request(f"{URL}/api/ingest", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read() or b"{}")
        for _ in batch:
            queue.popleft()
        LOG.info("gesendet: %d Samples (Antwort %s), Puffer %d", len(batch), res.get("inserted"), len(queue))
    except urllib.error.HTTPError as e:
        LOG.warning("HTTP %s von %s: %s", e.code, URL, e.read()[:200])
        if info:
            with lock:
                info_pending.update(info)
    except Exception as e:
        LOG.warning("Senden fehlgeschlagen (%s), Puffer %d", e, len(queue))
        if info:
            with lock:
                info_pending.update(info)


def main():
    if not URL or not TOKEN:
        LOG.error("WEB_URL oder WEB_TOKEN fehlt"); time.sleep(3600); return
    client = mqtt.Client(client_id="grolo-web-push", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (LOG.info("MQTT verbunden %s:%s, Ziel %s", HOST, PORT, URL), c.subscribe([f"{BASE}/grobro/+/state", f"{BASE}/grobro/+/dongle"]))
    client.on_message = on_message
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    while True:
        time.sleep(INTERVAL)
        try:
            flush()
        except Exception as e:
            LOG.warning("flush: %s", e)


main()
