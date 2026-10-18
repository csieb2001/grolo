#!/usr/bin/env python3
"""switchbot-bridge: SwitchBot-Messstecker (Plug Mini EU u. a.) als Verbraucher im Flussbild.

Der Plug Mini (EU) misst Leistung, Spannung und Strom, gibt sie aber nur über die SwitchBot-Cloud heraus –
über Matter kommt nur Ein/Aus. Diese Brücke fragt deshalb die SwitchBot-OpenAPI v1.1 ab (Token + Secret
aus den Entwickleroptionen der App, Anfragen per HMAC-SHA256 signiert). Grenze der API: 10 000 Aufrufe am
Tag; mit einer Abfrage je Stecker und Minute reicht das für sechs Stecker, ST_INTERVAL entsprechend wählen.

Statusfelder des Plug Mini (EU), geprüft am 18.10.2026: switchStatus (1/0), power (W), voltage (V),
electricCurrent (mA), usedElectricity (Wh seit Mitternacht).

Veröffentlicht je Stecker retained <BASE>/grolo/consumers/ext/sb-<deviceId> (Form siehe consumers.py):
  name (aus der App), kind "switchbot", w, kwh_today, state (on/off), ok

Einrichten (einmalig, im Container mit dem Volume):
  docker compose run --rm --no-deps -T switchbot --setup <token> <secret>

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, SB_INTERVAL (s, Standard 60), STATE_DIR (Standard /state).
"""
import base64, hashlib, hmac, json, logging, os, sys, time, uuid, urllib.request, urllib.error
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INTERVAL = float(os.getenv("SB_INTERVAL", "60"))
STATE_DIR = os.getenv("STATE_DIR", "/state")
CRED_FILE = os.path.join(STATE_DIR, "switchbot.json")
API = "https://api.switch-bot.com/v1.1"
METERING = ("Plug Mini", "Plug", "Relay Switch 1PM")   # Gerätetypen mit Leistungsmessung
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("switchbot")


def api(cred, path):
    t = str(int(time.time() * 1000)); nonce = str(uuid.uuid4())
    sign = base64.b64encode(hmac.new(cred["secret"].encode(), (cred["token"] + t + nonce).encode(), hashlib.sha256).digest()).decode()
    req = urllib.request.Request(API + path, headers={"Authorization": cred["token"], "sign": sign, "t": t, "nonce": nonce})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    if d.get("statusCode") != 100:
        raise RuntimeError(f"SwitchBot {d.get('statusCode')}: {d.get('message')}")
    return d.get("body") or {}


def setup(token, secret):
    cred = {"token": token.strip(), "secret": secret.strip()}
    devs = api(cred, "/devices").get("deviceList", [])
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CRED_FILE + ".tmp", "w") as f:
        json.dump(cred, f)
    os.chmod(CRED_FILE + ".tmp", 0o600)
    os.replace(CRED_FILE + ".tmp", CRED_FILE)
    print("eingerichtet, Geräte:", ", ".join(f"{d.get('deviceName')} ({d.get('deviceType')})" for d in devs))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--setup":
        setup(sys.argv[2], sys.argv[3]); return
    try:
        with open(CRED_FILE) as f:
            cred = json.load(f)
    except FileNotFoundError:
        LOG.error("Noch nicht eingerichtet (%s fehlt) – siehe --setup", CRED_FILE); time.sleep(3600); return
    client = mqtt.Client(client_id="grolo-switchbot", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    plugs, plugs_at = [], 0.0
    while True:
        t0 = time.time()
        try:
            if t0 - plugs_at > 3600 or not plugs:
                plugs = [d for d in api(cred, "/devices").get("deviceList", [])
                         if any(m in (d.get("deviceType") or "") for m in METERING)]
                plugs_at = t0
                LOG.info("Stecker: %s", ", ".join(d.get("deviceName") or d["deviceId"] for d in plugs) or "keine")
            for d in plugs:
                did = d["deviceId"]
                try:
                    st = api(cred, f"/devices/{did}/status")
                except Exception as e:
                    LOG.warning("%s: %s", d.get("deviceName"), e)
                    client.publish(f"{BASE}/grolo/consumers/ext/sb-{did}", json.dumps({"name": d.get("deviceName"), "kind": "switchbot", "ok": False}), retain=True)
                    continue
                used = num(st.get("usedElectricity"))
                p = {"name": d.get("deviceName") or did, "kind": "switchbot", "w": num(st.get("power")),
                     "kwh_today": None if used is None else round(used / 1000.0, 3),
                     "voltage": num(st.get("voltage")), "current_ma": num(st.get("electricCurrent")),
                     "state": "on" if st.get("switchStatus") == 1 else "off", "ok": True}
                client.publish(f"{BASE}/grolo/consumers/ext/sb-{did}", json.dumps(p), retain=True)
        except urllib.error.HTTPError as e:
            LOG.warning("SwitchBot HTTP %s: %s", e.code, e.read()[:200])
        except Exception as e:
            LOG.warning("SwitchBot: %s", e)
        time.sleep(max(5.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
