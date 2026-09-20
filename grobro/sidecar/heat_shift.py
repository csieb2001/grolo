#!/usr/bin/env python3
"""heat-shift: verschiebt Heizwärme in die Sonnenstunden, indem es die Solltemperatur kurz anhebt.

Der Gedanke ist einfach: Wärme lässt sich im Gebäude speichern. Läuft die PV, darf es in den Räumen ein
halbes Grad wärmer werden; ist die Sonne weg, geht der Sollwert auf seinen Ausgangswert zurück. Die
Wärmepumpe läuft dann eher mittags als nachts, und der Strom dafür kommt eher vom Dach als aus dem Netz.

Ehrlich zur Größenordnung: bei rund 1,3 kWp und 800 W Abgabegrenze des NEXA ist der Überschuss klein, und
im Winter – wenn geheizt wird – ist er am kleinsten. Das hier holt Cent, nicht Euro. Es schadet aber auch
nicht, solange die Anhebung klein bleibt und zuverlässig zurückgenommen wird; darum geht es im Folgenden.

Sicherungen, alle absichtlich eng:
  - Standardmäßig **aus**. Ohne ausdrückliches enabled passiert nichts.
  - Die Anhebung ist auf BOOST_MAX_K gedeckelt, der absolute Sollwert zusätzlich auf CEILING_C.
  - Gesenkt wird nie. Der Ausgangswert ist die Obergrenze für das Zurücknehmen, nicht der Startpunkt.
  - Der Ausgangswert je Raum liegt in /state/heatshift.json und übersteht einen Neustart. Wer von Hand
    am Thermostat dreht, während die Anhebung läuft, gewinnt: der neue Wert wird zum Ausgangswert.
  - Beim Abschalten, beim Beenden und bei jedem Fehler wird zurückgenommen, nicht liegengelassen.
  - Nachts (QUIET_FROM..QUIET_TO) wird nie angehoben.

Konfiguration (retained <BASE>/grolo/config/heatshift), von der Einstellungsseite:
    {"enabled": true, "boost_k": 0.5, "min_surplus_w": 150, "rooms": ["wohnzimmer", "küche"]}
  rooms leer oder fehlend = alle Räume mit Thermostat.

Zustand (retained <BASE>/grolo/heatshift/state): was gerade passiert und warum – dasselbe reason-Feld wie
bei der Shelly-Regelung, damit man nicht raten muss.

Umgebung: MQTT_HOST/PORT, HA_BASE_TOPIC, HEATSHIFT_STATE (Standard /state/heatshift.json),
          HEATSHIFT_INTERVAL (s, Standard 120), TZ
"""
import json, logging, os, threading, time
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
STATE_FILE = os.getenv("HEATSHIFT_STATE", "/state/heatshift.json")
INTERVAL = float(os.getenv("HEATSHIFT_INTERVAL", "120"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("heat-shift")

BOOST_MAX_K = 1.5      # mehr als anderthalb Grad ist keine Speicherung mehr, sondern Verschwendung
CEILING_C = 23.0       # absolute Obergrenze, unabhängig vom Ausgangswert
MIN_SURPLUS_W = 150.0  # darunter lohnt das Anheben nicht
QUIET_FROM, QUIET_TO = 21, 6      # nachts nie anheben
RELEASE_C = 16.0       # über dieser Außentemperatur wird ohnehin nicht geheizt


class Shift:
    def __init__(self):
        self.cfg = {}
        self.rooms = {}
        self.pv_w = None
        self.house_w = None
        self.outside_c = None
        self.base = {}          # raum -> Sollwert ohne Anhebung
        self.boosted = {}       # raum -> aktuell aufgeschlagene Kelvin
        self.lock = threading.Lock()
        self.load()
        self.client = mqtt.Client(client_id=f"heat-shift-{os.getpid()}",
                                  callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ---------------------------------------------------------------- Zustand
    def load(self):
        try:
            with open(STATE_FILE) as fh:
                saved = json.load(fh)
            self.base = saved.get("base", {})
            self.boosted = saved.get("boosted", {})
            if self.boosted:
                LOG.info("Aus dem letzten Lauf noch angehoben: %s – wird zurückgenommen",
                         ", ".join(self.boosted))
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOG.warning("Zustand nicht lesbar (%s), fange leer an", exc)

    def save(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"base": self.base, "boosted": self.boosted}, fh, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception as exc:
            LOG.warning("Zustand nicht schreibbar: %s", exc)

    # ---------------------------------------------------------------- MQTT
    def on_connect(self, client, userdata, flags, rc, props=None):
        LOG.info("MQTT verbunden %s:%s", HOST, PORT)
        client.subscribe([(f"{BASE}/grolo/config/heatshift", 0), (f"{BASE}/grolo/tado/rooms", 0),
                          (f"{BASE}/grolo/shelly/state", 0), (f"{BASE}/grobro/+/state", 0),
                          (f"{BASE}/grolo/wolf/heatpump/state", 0)])

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except Exception:
            return
        with self.lock:
            if msg.topic.endswith("/config/heatshift"):
                self.cfg = payload if isinstance(payload, dict) else {}
                LOG.info("Konfiguration: %s", "an" if self.cfg.get("enabled") else "aus")
            elif msg.topic.endswith("/tado/rooms"):
                self.rooms = payload if isinstance(payload, dict) else {}
            elif msg.topic.endswith("/shelly/state"):
                if payload.get("household_w") is not None:
                    self.house_w = float(payload["household_w"])
            elif msg.topic.endswith("/wolf/heatpump/state"):
                if payload.get("aussentemperatur") is not None:
                    self.outside_c = float(payload["aussentemperatur"])
            elif "/grobro/" in msg.topic:
                pv = sum(float(payload.get(f"pv{i}Voltage", 0) or 0) * float(payload.get(f"pv{i}Current", 0) or 0)
                         for i in range(1, 5))
                self.pv_w = pv

    def set_room(self, room, value):
        self.client.publish(f"{BASE}/grolo/tado/set",
                            json.dumps({"room": room, "setpoint": round(value, 1)}, ensure_ascii=False))

    # ---------------------------------------------------------------- Entscheidung
    def surplus(self):
        """Was die PV gerade mehr liefert, als das Haus ohnehin zieht."""
        if self.pv_w is None:
            return None
        if self.house_w is None:
            return self.pv_w
        return self.pv_w - self.house_w

    def decide(self):
        """Anheben oder zurücknehmen – und in einem Satz, warum."""
        if not self.cfg.get("enabled"):
            return 0.0, "aus" if not self.boosted else "aus, nehme zurück"
        hour = time.localtime().tm_hour
        if QUIET_FROM <= hour or hour < QUIET_TO:
            return 0.0, f"Nachtruhe ({QUIET_FROM}–{QUIET_TO} Uhr)"
        if self.outside_c is not None and self.outside_c > RELEASE_C:
            return 0.0, f"{self.outside_c:.1f} °C draußen, es wird nicht geheizt"
        s = self.surplus()
        if s is None:
            return 0.0, "kein PV-Messwert"
        floor = float(self.cfg.get("min_surplus_w") or MIN_SURPLUS_W)
        if s < floor:
            return 0.0, f"Überschuss {s:.0f} W unter {floor:.0f} W"
        boost = min(float(self.cfg.get("boost_k") or 0.5), BOOST_MAX_K)
        return boost, f"Überschuss {s:.0f} W, hebe um {boost:.1f} K an"

    def apply(self, boost, reason):
        wanted = self.cfg.get("rooms") or list(self.rooms)
        changed = []
        for room in list(set(wanted) | set(self.boosted)):
            data = self.rooms.get(room)
            if not data or data.get("setpoint_c") is None or not data.get("thermostats"):
                continue
            now = float(data["setpoint_c"])
            active = self.boosted.get(room, 0.0)
            # Der Ausgangswert ist der Sollwert ohne unseren Aufschlag. Hat jemand von Hand gedreht,
            # weicht die Rechnung ab – dann gilt der neue Wert, der Mensch hat Vorrang.
            expected = self.base.get(room, now) + active
            if abs(now - expected) > 0.05:
                self.base[room] = now - (active if abs(now - expected) < 0.5 else 0.0)
                LOG.info("%s: von Hand auf %.1f °C gestellt, das ist jetzt der Ausgangswert", room, self.base[room])
            base = self.base.get(room, now)
            want_boost = boost if room in wanted else 0.0
            target = min(base + want_boost, CEILING_C)
            if want_boost and target <= base + 0.05:
                continue                                   # die Decke lässt keine Anhebung mehr zu
            if abs(target - now) < 0.05:
                self.boosted[room] = want_boost if want_boost else 0.0
                if not want_boost:
                    self.boosted.pop(room, None)
                continue
            self.set_room(room, target)
            if want_boost:
                self.base.setdefault(room, base)
                self.boosted[room] = want_boost
            else:
                self.boosted.pop(room, None)
            changed.append(f"{room} {now:.1f}→{target:.1f}")
        if changed:
            LOG.info("%s | %s", reason, ", ".join(changed))
        self.save()
        return changed

    def publish_state(self, boost, reason, changed):
        self.client.publish(f"{BASE}/grolo/heatshift/state", json.dumps({
            "ts": int(time.time()), "enabled": bool(self.cfg.get("enabled")), "boost_k": boost,
            "reason": reason, "surplus_w": None if self.surplus() is None else round(self.surplus()),
            "pv_w": None if self.pv_w is None else round(self.pv_w),
            "house_w": None if self.house_w is None else round(self.house_w),
            "outside_c": self.outside_c, "rooms": len(self.rooms),
            "boosted": {k: v for k, v in self.boosted.items()}, "changed": changed,
        }, ensure_ascii=False), retain=True)

    def run(self):
        while True:
            try:
                self.client.connect(HOST, PORT, 60); break
            except Exception as exc:
                LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch in 10 s", exc); time.sleep(10)
        self.client.loop_start()
        time.sleep(20)          # den retained Topics Zeit geben
        while True:
            try:
                with self.lock:
                    boost, reason = self.decide()
                    changed = self.apply(boost, reason)
                    self.publish_state(boost, reason, changed)
            except Exception:
                LOG.exception("Durchlauf fehlgeschlagen – nehme sicherheitshalber zurück")
                try:
                    with self.lock:
                        self.apply(0.0, "Fehler, zurückgenommen")
                except Exception:
                    pass
            time.sleep(INTERVAL)


if __name__ == "__main__":
    Shift().run()
