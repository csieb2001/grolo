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
INTERVAL = float(os.getenv("SB_INTERVAL", "0"))   # 0 = nach Tagesbudget (siehe BUDGET)
# Die API erlaubt 10 000 Aufrufe am Tag. Davon gehen ~150 an die Geräteliste; der Rest wird gleichmäßig auf die
# Stecker verteilt, mit Reserve: bei vier Steckern alle ~38 s, bei acht alle ~77 s. Schneller als 20 s nie.
BUDGET = 9000
STATE_DIR = os.getenv("STATE_DIR", "/state")
CRED_FILE = os.path.join(STATE_DIR, "switchbot.json")
API = "https://api.switch-bot.com/v1.1"
METERING = ("Plug Mini", "Plug", "Relay Switch 1PM")   # Gerätetypen mit Leistungsmessung
# Standby: Der Plug Mini meldet unter etwa 0,5 W „power": 0, obwohl Strom fließt (TV im Standby: 0 W bei 15 mA).
# Strom × Spannung allein wäre die Scheinleistung und bei Standby-Netzteilen ein Vielfaches der Wirkleistung.
# Deshalb wird je Stecker der Leistungsfaktor gelernt, wann immer er beides meldet, und im Standby damit
# hochgerechnet. Ohne Lernwert gilt PF_DEFAULT. Der war zuerst 0,15 (Kühlschrank-Stecker: 0,9 W bei 28 mA) und
# lag damit fünffach zu hoch: die SwitchBot-App zeigt für den Fernseher im Standby 0,1 W bei 15 mA, also ≈ 0,03
# (18.10.2026). Standby-Netzteile streuen so stark, dass der kleinere Wert die ehrlichere Vorgabe ist.
PF_DEFAULT = 0.03
PF_MIN_W = 0.5        # darunter meldet der Stecker 0 – die Schätzung bleibt deshalb unter dieser Grenze
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
    pf = {}               # deviceId -> gelernter Leistungsfaktor
    while True:
        t0 = time.time()
        try:
            # Geräteliste alle 10 min: sie trägt die Namen aus der App, und eine Umbenennung soll nicht eine
            # Stunde auf sich warten lassen. Kostet einen Aufruf, bei 10 000 am Tag kein Thema.
            if t0 - plugs_at > 600 or not plugs:
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
                w, v, ma = num(st.get("power")), num(st.get("voltage")), num(st.get("electricCurrent"))
                estimated = False
                # Nur aus kleinen Lasten lernen: im Betrieb hat ein Fernseher einen Leistungsfaktor um 0,9, im
                # Standby um 0,1–0,2. Ein im Betrieb gelernter Faktor würde den Standby um das Fünffache überschätzen.
                if w and v and ma and 0 < w < 3 and ma > 0:
                    f = max(0.05, min(1.0, w / (v * ma / 1000.0)))
                    pf[did] = f if did not in pf else 0.8 * pf[did] + 0.2 * f
                elif w == 0 and v and ma and ma > 0 and st.get("switchStatus") == 1:
                    w = round(min(PF_MIN_W, v * ma / 1000.0 * pf.get(did, PF_DEFAULT)), 2); estimated = True
                p = {"name": d.get("deviceName") or did, "kind": "switchbot", "w": w, "estimated": estimated,
                     # usedElectricity springt und steigt auch ohne Last – den Tagesverbrauch rechnet consumers.py
                     # aus der Leistung (integrate). Der Rohwert geht nur zur Kontrolle mit.
                     "integrate": True, "used_raw": used,
                     "voltage": num(st.get("voltage")), "current_ma": num(st.get("electricCurrent")),
                     "state": "on" if st.get("switchStatus") == 1 else "off", "ok": True}
                client.publish(f"{BASE}/grolo/consumers/ext/sb-{did}", json.dumps(p), retain=True)
        except urllib.error.HTTPError as e:
            LOG.warning("SwitchBot HTTP %s: %s", e.code, e.read()[:200])
        except Exception as e:
            LOG.warning("SwitchBot: %s", e)
        every = INTERVAL or max(20.0, len(plugs) * 86400.0 / BUDGET)
        time.sleep(max(5.0, every - (time.time() - t0)))


if __name__ == "__main__":
    main()
