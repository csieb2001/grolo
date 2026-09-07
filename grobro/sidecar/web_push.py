#!/usr/bin/env python3
"""web-push: schickt die bereinigten Messwerte alle 30 s an die GroLo-Website (Vercel).

Bildet aus dem GroBro-State dieselben Größen wie das Grafana-Dashboard:
  pv_w  = Summe Spannung × Strom der Strings          out_w = (Register 116 − 30000) / 10, Register zählt in 0,1 W
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
weather = {"current": None, "forecast": None, "dirty": False}


def pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def iso(v):
    """Zeitstempel als ISO-8601 (UTC); Unix-Sekunden werden umgerechnet, "HH:MM" wird auf das heutige lokale Datum gesetzt."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
    v = str(v)
    if len(v) == 5 and v[2] == ":":
        # lokale Uhrzeit (TZ des Containers) -> heutiges Datum, in UTC ausgeben
        lt = time.localtime(); h, m = int(v[:2]), int(v[3:])
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    return v


def weather_payload():
    """Wetter im Vertrag der Website: akzeptiert Open-Meteo-Namen und die Kurzformen des weather-Sidecars."""
    c = weather["current"] or {}
    cur = {
        "temperature": pick(c, "temperature", "temperature_2m"), "cloud_cover": pick(c, "cloud_cover"),
        "shortwave_radiation": pick(c, "shortwave_radiation", "irradiance"), "direct_radiation": pick(c, "direct_radiation"),
        "diffuse_radiation": pick(c, "diffuse_radiation"), "wind_speed": pick(c, "wind_speed", "wind_speed_10m"),
        "weather_code": pick(c, "weather_code"), "is_day": pick(c, "is_day"),
        "condition_en": pick(c, "condition_en", "condition"), "condition_de": pick(c, "condition_de"),
        "sunrise": iso(pick(c, "sunrise")), "sunset": iso(pick(c, "sunset")),
        "sunshine_duration_today": pick(c, "sunshine_duration_today", "sunshine_duration",
                                        default=(pick(c, "sunshine_hours_today") or 0) * 3600 if pick(c, "sunshine_hours_today") is not None else None),
        "radiation_sum_today": pick(c, "radiation_sum_today", "shortwave_radiation_sum", "radiation_sum_mj_today", "radiation_today_mj"),
    }
    fc = weather["forecast"]
    if isinstance(fc, dict):
        fc = pick(fc, "hourly", "forecast", default=[])
    fcl = []
    for h in (fc or [])[:48]:
        fcl.append({"t": iso(pick(h, "t", "time")), "shortwave_radiation": pick(h, "shortwave_radiation"), "cloud_cover": pick(h, "cloud_cover"),
                    "temperature": pick(h, "temperature", "temperature_2m"), "weather_code": pick(h, "weather_code")})
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "current": cur, "forecast": fcl}


def derive(st):
    pv = sum(float(st.get(f"pv{i}Voltage", 0) or 0) * float(st.get(f"pv{i}Current", 0) or 0) for i in range(1, 5))
    out = (float(st.get("onGridPower", 30000) or 30000) - 30000.0) / 10.0  # 0,1-W-Schritte, 38000 = 800 W
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
        elif parts[-2:] == ["weather", "current"]:
            with lock:
                weather["current"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["weather", "forecast"]:
            with lock:
                weather["forecast"] = json.loads(msg.payload); weather["dirty"] = True
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
        wx = weather_payload() if (weather["dirty"] and weather["current"]) else None
        weather["dirty"] = False
    if not queue and not info and not wx:
        return
    batch = list(queue)[:200]
    body = {"samples": batch}
    if info:
        body["info"] = next(iter(info.values()))
    if wx:
        body["weather"] = wx
    req = urllib.request.Request(f"{URL}/api/ingest", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read() or b"{}")
        for _ in batch:
            queue.popleft()
        LOG.info("gesendet: %d Samples (Antwort %s, Wetter %s), Puffer %d", len(batch), res.get("inserted"), res.get("weather"), len(queue))
    except urllib.error.HTTPError as e:
        LOG.warning("HTTP %s von %s: %s", e.code, URL, e.read()[:200])
        with lock:
            info_pending.update(info); weather["dirty"] = weather["dirty"] or bool(wx)
    except Exception as e:
        LOG.warning("Senden fehlgeschlagen (%s), Puffer %d", e, len(queue))
        with lock:
            info_pending.update(info); weather["dirty"] = weather["dirty"] or bool(wx)


def main():
    if not URL or not TOKEN:
        LOG.error("WEB_URL oder WEB_TOKEN fehlt"); time.sleep(3600); return
    client = mqtt.Client(client_id="grolo-web-push", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (LOG.info("MQTT verbunden %s:%s, Ziel %s", HOST, PORT, URL),
                                                     c.subscribe([(f"{BASE}/grobro/+/state", 0), (f"{BASE}/grobro/+/dongle", 0), (f"{BASE}/grolo/weather/current", 0), (f"{BASE}/grolo/weather/forecast", 0)]))
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
