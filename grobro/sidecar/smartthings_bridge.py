#!/usr/bin/env python3
"""smartthings-bridge: Samsung-Waschmaschine und -Trockner (SmartThings-Cloud) als Verbraucher im Flussbild.

Lokal bieten die Geräte nichts Brauchbares an (nur verschlüsseltes CoAP/OCF); der einzige Weg ist die
SmartThings-REST-API. Sie meldet über powerConsumptionReport einen Energiezähler in Wh. Das Feld „power"
steht bei Waschmaschinen und Trocknern fast immer auf 0 – die Leistung wird deshalb, wo nötig, aus dem
Anstieg des Zählers zwischen zwei Änderungen geschätzt und als solche gekennzeichnet (estimated).

Veröffentlicht je Gerät retained <BASE>/grolo/consumers/ext/st-<id> (Form siehe consumers.py):
  name, icon (washer/dryer), kind "smartthings", w, estimated, kwh_today, state (run/pause/stop), remaining_min, ok

Anmeldung: Persönliche Tokens laufen seit Ende 2024 nach 24 h ab. Die Brücke nutzt deshalb eine eigene
OAuth-App (angelegt mit `smartthings apps:create`) und erneuert ihr Token selbst. Jedes Refresh-Token gilt
nur EINMAL – das neue wird sofort und atomar gesichert, bevor irgendetwas anderes passiert; geht es
verloren, ist eine neue Anmeldung im Browser nötig. Ungenutzt verfällt es nach etwa 30 Tagen.

Einrichten (einmalig, im Container mit dem Volume):
  python smartthings_bridge.py --setup <client_id> <client_secret> <redirect_uri> <code oder ganze Rücksprung-URL>

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, ST_INTERVAL (s, Standard 60), STATE_DIR (Standard /state).
"""
import base64, calendar, json, logging, os, sys, time, urllib.parse, urllib.request, urllib.error
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INTERVAL = float(os.getenv("ST_INTERVAL", "60"))
STATE_DIR = os.getenv("STATE_DIR", "/state")
TOKEN_FILE = os.path.join(STATE_DIR, "smartthings.json")
TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
API = "https://api.smartthings.com/v1"
IDLE_S = 20 * 60      # so lange ohne Zähleranstieg (und Gerät nicht in Betrieb) gilt die Leistung als 0
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("smartthings")


def load():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save(tok):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(TOKEN_FILE + ".tmp", "w") as f:
        json.dump(tok, f)
    os.replace(TOKEN_FILE + ".tmp", TOKEN_FILE)


def token_request(tok, form):
    auth = base64.b64encode(f"{tok['client_id']}:{tok['client_secret']}".encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({**form, "client_id": tok["client_id"]}).encode(), method="POST",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    tok.update({"access_token": d["access_token"], "refresh_token": d["refresh_token"],
                "expires_at": time.time() + float(d.get("expires_in", 86400)) - 300})
    save(tok)          # sofort: das alte Refresh-Token ist ab jetzt verbraucht
    return tok


def access(tok):
    if time.time() > tok.get("expires_at", 0):
        LOG.info("Token erneuern")
        token_request(tok, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    return tok["access_token"]


def api(tok, path):
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {access(tok)}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:                       # Token vorzeitig ungültig: einmal erneuern und wiederholen
            tok["expires_at"] = 0
            req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {access(tok)}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        raise


def setup(client_id, secret, redirect, code):
    if "code=" in code:                          # die ganze Rücksprung-URL aus der Adresszeile geht auch
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
    tok = {"client_id": client_id, "client_secret": secret, "redirect_uri": redirect}
    token_request(tok, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
    devs = api(tok, "/devices").get("items", [])
    print("angemeldet, Geräte:", ", ".join(d.get("label") or d.get("name") or d["deviceId"] for d in devs))


def val(st, cap, attr):
    v = (((st.get("components") or {}).get("main") or {}).get(cap) or {}).get(attr) or {}
    return v.get("value")


track = {}    # deviceId -> {"e": letzter Zählerstand, "t": Zeit der letzten Änderung, "w": geschätzte Leistung, "day", "e0"}


def device_payload(dev, st, now):
    caps = {c["id"] for comp in dev.get("components", []) for c in comp.get("capabilities", [])}
    # Nach der Betriebsfähigkeit, nicht nach „dryer" im Namen irgendeiner Fähigkeit: Waschmaschinen tragen
    # Trocknungs-Optionen (custom.dryerDryLevel u. a.) und wurden sonst als Trockner gelesen.
    kind = "washer" if "washerOperatingState" in caps else "dryer"
    rep = val(st, "powerConsumptionReport", "powerConsumption") or {}
    energy = rep.get("energy")
    power = rep.get("power")
    machine = val(st, f"{kind}OperatingState", "machineState")
    job = val(st, f"{kind}OperatingState", f"{kind}JobState")
    done = val(st, f"{kind}OperatingState", "completionTime")
    remaining = None
    # Restzeit: das Samsung-eigene remainingTime (Minuten) ist genauer als completionTime und steht auch während
    # einer Pause richtig; completionTime bleibt der Rückfall für Geräte ohne samsungce-Fähigkeit.
    rt = val(st, f"samsungce.{kind}OperatingState", "remainingTime")
    if machine in ("run", "pause") and rt is not None:
        remaining = float(rt)
    elif machine == "run" and done:
        try:
            ts = calendar.timegm(time.strptime(done[:19], "%Y-%m-%dT%H:%M:%S"))   # completionTime ist UTC
            remaining = max(0.0, (ts - now) / 60.0)
        except Exception:
            pass

    tr = track.setdefault(dev["deviceId"], {"e": None, "t": now, "w": 0.0, "day": None, "e0": None})
    today = time.strftime("%Y-%m-%d")
    if energy is not None:
        energy = float(energy)
        if tr["day"] != today or tr["e0"] is None or energy < tr["e0"]:
            tr["day"], tr["e0"] = today, energy
        if tr["e"] is not None and energy > tr["e"]:
            dt_h = max(1 / 60, (now - tr["t"]) / 3600.0)
            tr["w"] = (energy - tr["e"]) / dt_h
            tr["t"] = now
        elif tr["e"] is None:
            tr["t"] = now
        tr["e"] = energy
    if machine != "run" and now - tr["t"] > IDLE_S:
        tr["w"] = 0.0
    live = power is not None and float(power) > 0
    # Samsung setzt weiche Trennstriche in die Namen („Wasch\u00admaschine"), die im Bild als Strich stehen könnten.
    # Der Zähler springt in 100-Wh-Schritten und wird nur alle paar Minuten bis Stunden nachgetragen – die
    # geschätzte Leistung ist deshalb ein grobes Mittel, kein Messwert.
    name = (dev.get("label") or dev.get("name") or "").replace("\u00ad", "")
    return {"name": name, "icon": kind, "kind": "smartthings",
            "w": round(float(power), 1) if live else (round(tr["w"], 1) if machine == "run" or tr["w"] else 0.0),
            "estimated": not live,
            "kwh_today": None if tr["e0"] is None or energy is None else round((energy - tr["e0"]) / 1000.0, 3),
            "state": machine, "job": job, "remaining_min": None if remaining is None else round(remaining),
            "ok": True}


def main():
    if len(sys.argv) >= 6 and sys.argv[1] == "--setup":
        setup(*sys.argv[2:6]); return
    try:
        tok = load()
    except FileNotFoundError:
        LOG.error("Noch nicht eingerichtet (%s fehlt) – siehe --setup", TOKEN_FILE); time.sleep(3600); return
    client = mqtt.Client(client_id="grolo-smartthings", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    devs, devs_at = [], 0.0
    while True:
        t0 = time.time()
        try:
            if t0 - devs_at > 3600 or not devs:
                items = api(tok, "/devices").get("items", [])
                devs = [d for d in items if any(c["id"] in ("washerOperatingState", "dryerOperatingState")
                                                for comp in d.get("components", []) for c in comp.get("capabilities", []))]
                devs_at = t0
                LOG.info("Geräte: %s", ", ".join(d.get("label") or d["deviceId"] for d in devs) or "keine")
            for d in devs:
                try:
                    p = device_payload(d, api(tok, f"/devices/{d['deviceId']}/status"), t0)
                except Exception as e:
                    LOG.warning("%s: %s", d.get("label"), e); continue
                client.publish(f"{BASE}/grolo/consumers/ext/st-{d['deviceId'][:8]}", json.dumps(p), retain=True)
        except urllib.error.HTTPError as e:
            LOG.warning("SmartThings HTTP %s: %s", e.code, e.read()[:200])
        except Exception as e:
            LOG.warning("SmartThings: %s", e)
        time.sleep(max(5.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
