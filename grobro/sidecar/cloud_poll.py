#!/usr/bin/env python3
"""Sidecar: zweiter Datenweg über die Growatt-Cloud, unabhängig vom Dongle im LAN.

Der lokale Weg (Dongle -> unser Broker -> GroBro) ist der schnelle und der genaue. Er hängt aber daran,
dass der Dongle uns als Broker erreicht. Fällt das weg – DNS-Eintrag entfernt, Dongle in einem anderen
Netz, Gerät neu eingerichtet – liefert dieser Sidecar dieselben Werte aus der Herstellercloud.

Er meldet sich wie die ShinePhone-App an (kein API-Schlüssel nötig) und fragt zwei Endpunkte ab:
    /noahDeviceApi/nexa/getSystemStatus   Leistung, SoC, Modus, Zählerwerte
    /noahDeviceApi/nexa/getBatteryData    SoC und Temperatur je Akkupack

Veröffentlicht nach <HA_BASE_TOPIC>/grolo/cloud/state (retained) mit denselben Feldnamen wie der lokale
Weg, dazu `local_age_s`: wie alt die zuletzt lokal empfangene Nachricht ist. Damit ist auf einen Blick zu
sehen, welcher Weg gerade trägt. Die Werte werden bewusst NICHT in das GroBro-Topic gespiegelt – beide
Quellen bleiben getrennt und vergleichbar.

Konfiguration über Umgebungsvariablen:
    GROWATT_USER, GROWATT_PASSWORD   Kontodaten (Pflicht)
    GROWATT_DEVICE                   Seriennummer des Datenloggers (Pflicht)
    GROWATT_SERVER                   Standard https://server-api.growatt.com
    CLOUD_POLL_S                     Abstand der Abfragen, Standard 300 s
"""
import hashlib, http.cookiejar, json, logging, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime

import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
USER = os.getenv("GROWATT_USER", ""); PASSWORD = os.getenv("GROWATT_PASSWORD", "")
DEVICE = os.getenv("GROWATT_DEVICE", "")
SERVER = os.getenv("GROWATT_SERVER", "https://server-api.growatt.com").rstrip("/")
POLL_S = float(os.getenv("CLOUD_POLL_S", "300"))
UA = "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 6 Build/TQ3A.230805.001)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("cloud-poll")

MODES = {"0": "Load First", "1": "Battery First", "2": "Smart"}
last_local = {"ts": 0.0}


def encrypt_password(password):
    """MD5andKL.encryptPassword der App: MD5-Hex, einstellige Bytes bekommen ein 'c' statt der '0'."""
    out = []
    for b in hashlib.md5(password.encode()).digest():
        h = format(b, "x")
        out.append(("c" + h) if len(h) == 1 else h)
    return "".join(out)


def validate_timestamp():
    ms = str(int(time.time() * 1000))
    return ms[:11] + "%02d" % (int(ms[1] + ms[3] + ms[5] + ms[7]) % 98)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Die Cloud antwortet auf eine abgelaufene Sitzung mit 302 auf die Anmeldeseite."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Cloud:
    def __init__(self):
        self.opener = None

    def _send(self, path, data):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(SERVER + path, data=body, method="POST",
                                     headers={"User-Agent": UA,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with self.opener.open(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def login(self):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect())
        res = self._send("/newTwoLoginAPIV2.do", {
            "userName": USER, "password": encrypt_password(PASSWORD), "language": "1",
            "appType": "ShinePhone", "phoneSn": "grolo", "phoneModel": "grolo", "phoneType": "android",
            "systemVersion": "13", "shinephoneVersion": "8.4.9.0",
            "loginTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": validate_timestamp(), "newLogin": "1"})
        if not res.get("back", {}).get("success"):
            raise RuntimeError("Anmeldung abgelehnt (Konto oder Passwort?)")
        LOG.info("An der Growatt-Cloud angemeldet als %s", USER)

    def post(self, path, data):
        if self.opener is None:
            self.login()
        try:
            return self._send(path, data)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307):
                raise
            self.login()                  # Sitzung war abgelaufen
            return self._send(path, data)

    def read(self):
        st = self.post("/noahDeviceApi/nexa/getSystemStatus", {"deviceSn": DEVICE}).get("obj") or {}
        bat = (self.post("/noahDeviceApi/nexa/getBatteryData", {"deviceSn": DEVICE}).get("obj") or {}).get("batter") or []
        return st, bat


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(st, bat):
    out = {
        "ts": int(time.time()), "source": "cloud", "device": DEVICE,
        "ppv": num(st.get("ppv")), "pac": num(st.get("pac")),
        "totalBatteryPackSoc": num(st.get("soc")),
        "totalBatteryPackChargingPower": num(st.get("chargePower")),
        "dischargePower": num(st.get("disChargePower")),
        "totalHouseholdLoad": num(st.get("loadPower")),
        "gridPower": num(st.get("gridPower")),
        "eacToday": num(st.get("eacToday")), "eacTotal": num(st.get("eacTotal")),
        "batteryPackageQuantity": num(st.get("batteryNum")),
        "workMode": MODES.get(str(st.get("workMode")), str(st.get("workMode"))),
        "statusCode": st.get("status"),
        "local_age_s": round(time.time() - last_local["ts"], 1) if last_local["ts"] else None,
    }
    for i, p in enumerate(bat[:4], start=1):
        out["battery%dSoc" % i] = num(p.get("soc"))
        out["battery%dTemp" % i] = num(p.get("temp"))
    return {k: v for k, v in out.items() if v is not None}


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s", HOST, PORT)
    client.subscribe("%s/grobro/%s/state" % (BASE, DEVICE))


def on_message(client, userdata, msg):
    last_local["ts"] = time.time()


def main():
    if not (USER and PASSWORD and DEVICE):
        LOG.error("GROWATT_USER, GROWATT_PASSWORD und GROWATT_DEVICE müssen gesetzt sein")
        return
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect; client.on_message = on_message
    client.connect(HOST, PORT, 60); client.loop_start()

    cloud = Cloud()
    while True:
        try:
            state = build(*cloud.read())
            client.publish("%s/grolo/cloud/state" % BASE, json.dumps(state), retain=True)
            client.publish("%s/grolo/cloud/status" % BASE,
                           json.dumps({"ok": True, "ts": state["ts"], "error": None}), retain=True)
            LOG.info("Cloud: SoC %s %%, PV %s W, Haushalt %s W, Modus %s (lokal vor %s s)",
                     state.get("totalBatteryPackSoc"), state.get("ppv"), state.get("totalHouseholdLoad"),
                     state.get("workMode"), state.get("local_age_s"))
        except Exception as e:
            LOG.warning("Abfrage fehlgeschlagen: %s", e)
            cloud.opener = None   # beim nächsten Versuch neu anmelden
            client.publish("%s/grolo/cloud/status" % BASE,
                           json.dumps({"ok": False, "ts": int(time.time()), "error": str(e)[:200]}), retain=True)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
