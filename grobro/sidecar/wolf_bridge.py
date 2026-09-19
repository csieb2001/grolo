#!/usr/bin/env python3
"""wolf-bridge: übersetzt zwischen ism7mqtt (Wolf Link, lokal) und dem GroLo-Stack.

ism7mqtt spricht direkt mit dem WOLF Link auf Port 9092 und veröffentlicht je Busteilnehmer ein Topic
    Wolf/<ip>/<Gerät>_<Busadresse>
mit JSON-Teilaktualisierungen: nur die Parameter, die gerade gelesen wurden, benannt wie im Wolf-Menü
(„Erzeugte Wärmemenge Vortag"), Auswahlparameter als {"value":…, "text":…}, mehrfach vergebene Namen als
{"<PTID>": …}. Dieses Sidecar hält daraus den vollständigen Zustand je Gerät und veröffentlicht:

  <BASE>/grolo/wolf/<gerät>/state   flache Zahlenfelder (Feldnamen aus wolf/catalog.json) für Telegraf,
                                    Klartext von Auswahlparametern zusätzlich als „<feld>_text"
  <BASE>/grolo/wolf/derived         berechnete Kennzahlen, die die Wolf selbst nicht liefert: momentaner
                                    COP, Spreizung, Taktung, Abtauzyklen, Laufzeitanteile Heizen/Warmwasser,
                                    Arbeitszahlen für Tag/Monat/Jahr
  <BASE>/grolo/wolf/catalog         der Parameterkatalog mit den zur Laufzeit gefundenen Geräten (retained,
                                    Grundlage für die Bedienseite wolf.html)
  <BASE>/grolo/wolf/status          Verbindungszustand und gefundene Busteilnehmer (retained)

Schreiben: eine Nachricht auf <BASE>/grolo/wolf/set
    {"device": "heatpump", "key": "warmwassersolltemperatur", "value": 52}
    {"device": "circuit",  "key": "programmwahl_360051",      "text": "Auto"}
wird gegen den Katalog geprüft (schreibbar, Grenzwerte, erlaubte Auswahl) und als
    Wolf/<ip>/<Gerät>_<Bus>/set   {"Warmwassersolltemperatur": 52}
weitergereicht. Das Ergebnis kommt auf <BASE>/grolo/wolf/set/result.

Die Tageszähler (Takte, Laufzeiten, Abtauungen) und der zuletzt bekannte Wert jedes Parameters liegen in
/state/wolf.json und überleben einen Neustart. Das ist nötig, weil ism7mqtt nach dem ersten vollständigen
Lesedurchlauf nur noch geänderte Werte schickt: ohne gespeicherten Zustand hätte die Bridge nach einem
Neustart nur noch die paar Parameter, die sich seitdem bewegt haben.

Parameter der Fachmann-Ebene (in der Smartset-App hinter dem Fachmann-Code) verlangen zusätzlich
    {"device": "heatpump", "key": "bivalenzpunkt_e_heizung", "value": -7, "pin": "1111"}
sofern WOLF_EXPERT_PIN gesetzt ist. Der Code lässt sich vorab prüfen: <BASE>/grolo/wolf/unlock mit
{"pin": "…"} antwortet auf <BASE>/grolo/wolf/unlock/result.

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, WOLF_CATALOG (Standard /wolf/catalog.json),
          WOLF_STATE (Standard /state/wolf.json), WOLF_PUBLISH_INTERVAL (s, Standard 10),
          WOLF_EXPERT_PIN (leer = Fachmann-Ebene ohne Code), TZ
"""
import json, logging, os, threading, time
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
CATALOG = os.getenv("WOLF_CATALOG", "/wolf/catalog.json")
STATE_FILE = os.getenv("WOLF_STATE", "/state/wolf.json")
INTERVAL = float(os.getenv("WOLF_PUBLISH_INTERVAL", "10"))
EXPERT_PIN = os.getenv("WOLF_EXPERT_PIN", "").strip()   # leer = Fachmann-Ebene ohne Code schreibbar
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("wolf-bridge")

# Verdichterstatus (PTID 270051) und Betriebsart Heizgerät (270050), soweit wir sie für Kennzahlen brauchen
ST_BETRIEB, ST_ABTAU = 5, 6
ST_SPERRE = {8, 9, 10}
MODE_WW = {7, 8, 9}          # Warmwasser, WW-Nachlauf, Antilegionellenfunktion
MODE_HZ = {10, 11}           # Heizbetrieb, HZ-Nachlauf
MODE_KUEHL = {12, 17}        # Aktive Kühlung, Nachlauf K


class Device:
    """Ein Busteilnehmer: Katalogbeschreibung plus der zuletzt gesehene Wert je Parameter."""

    def __init__(self, entry, ident):
        self.ident = ident                       # unser stabiler Name, z. B. "heatpump"
        self.dtid = entry["dtid"]
        self.bus = entry["read"]
        self.role = entry["role"]
        self.label_de, self.label_en = entry["de"], entry["en"]
        self.params = entry["params"]
        self.topic = None                        # Wolf/<ip>/<Gerät>_<Bus>, sobald erkannt
        self.values = {}                         # key -> Zahl
        self.texts = {}                          # key -> Klartext
        self.seen = 0
        self.by_name = {}                        # Wolf-Name -> [Eintrag, …]
        self.by_key = {}
        for p in self.params:
            self.by_name.setdefault(p["de"], []).append(p)
            self.by_key[p["key"]] = p

    def match(self, names):
        """Wie gut passen die Parameternamen einer eingehenden Nachricht zu diesem Gerät?"""
        return sum(1 for n in names if n in self.by_name)

    def entry_for(self, name, ptid):
        candidates = self.by_name.get(name)
        if not candidates:
            return None
        if ptid is None:
            return candidates[0] if len(candidates) == 1 else None
        for c in candidates:
            if c["ptid"] == ptid:
                return c
        return None

    def apply(self, payload):
        """Teilaktualisierung von ism7mqtt einarbeiten."""
        changed = 0
        for name, node in payload.items():
            for ptid, value, text in unpack(node):
                entry = self.entry_for(name, ptid)
                if entry is None:
                    continue
                key = entry["key"]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.values[key] = value
                if text is not None:
                    self.texts[key] = text
                elif isinstance(value, str):
                    self.texts[key] = value
                if entry.get("options") and key in self.values:
                    want = str(int(self.values[key])) if float(self.values[key]).is_integer() else str(self.values[key])
                    for o in entry["options"]:
                        if o["v"] == want:
                            self.texts[key] = o["de"]
                            break
                changed += 1
        if changed:
            self.seen = time.time()
        return changed

    def state_payload(self):
        out = dict(self.values)
        for key, text in self.texts.items():
            out[f"{key}_text"] = text
        return out

    def restore(self, saved):
        """Zuletzt bekannte Werte übernehmen, aber nur für Parameter, die es noch gibt."""
        self.values = {k: v for k, v in saved.get("values", {}).items() if k in self.by_key}
        self.texts = {k: v for k, v in saved.get("texts", {}).items() if k in self.by_key}
        return len(self.values)


def unpack(node):
    """Ein JSON-Knoten von ism7mqtt -> Liste aus (PTID oder None, Zahl/Text, Klartext oder None).

    Möglich sind: 5.1  ·  {"value":1,"text":"Ein"}  ·  {"350009":55,"350014":55}
                  ·  {"360051":{"value":1,"text":"Auto"},"360058":1}
    """
    if isinstance(node, dict):
        if "value" in node or "text" in node:
            return [(None, node.get("value"), node.get("text"))]
        out = []
        for k, sub in node.items():
            if not k.isdigit():
                continue
            ptid = int(k)
            if isinstance(sub, dict):
                out.append((ptid, sub.get("value"), sub.get("text")))
            else:
                out.append((ptid, sub, None))
        return out
    return [(None, node, None)]


class Counters:
    """Tageszähler, die die Wolf nicht führt: Takte, Laufzeiten, Abtauungen. Reset um Mitternacht."""

    FIELDS = ("takte", "laufzeit_min", "abtauungen", "abtau_min", "ww_min", "hz_min",
              "kuehl_min", "eheiz_min", "sperr_min", "standby_min")

    def __init__(self, path):
        self.path = path
        self.day = time.strftime("%Y-%m-%d")
        self.data = {f: 0.0 for f in self.FIELDS}
        self.prev = {}
        self.last_tick = None
        self.load()

    def load(self):
        try:
            with open(self.path) as fh:
                saved = json.load(fh)
            if saved.get("day") == self.day:
                self.data.update({k: v for k, v in saved.get("data", {}).items() if k in self.FIELDS})
                LOG.info("Tageszähler übernommen: %s Takte, %.0f min Laufzeit", int(self.data["takte"]), self.data["laufzeit_min"])
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOG.warning("Zählerstand nicht lesbar (%s), fange bei null an", exc)

    def tick(self, hp):
        """Einen Zeitschritt verbuchen. hp sind die aktuellen Werte der Wärmepumpe."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.data = {f: 0.0 for f in self.FIELDS}
            LOG.info("Neuer Tag, Tageszähler zurückgesetzt")
        minutes = 0.0 if self.last_tick is None else min((now - self.last_tick) / 60.0, 5.0)
        self.last_tick = now
        if not hp:
            return

        verdichter = hp.get("verdichter")
        status = hp.get("verdichterstatus")
        modus = hp.get("betriebsart_heizgeraet")
        eheiz = hp.get("e_heizung")

        if verdichter == 1 and self.prev.get("verdichter") == 0:
            self.data["takte"] += 1
        if status == ST_ABTAU and self.prev.get("verdichterstatus") != ST_ABTAU:
            self.data["abtauungen"] += 1

        if verdichter == 1:
            self.data["laufzeit_min"] += minutes
        if status == ST_ABTAU:
            self.data["abtau_min"] += minutes
        if status in ST_SPERRE:
            self.data["sperr_min"] += minutes
        if eheiz == 1:
            self.data["eheiz_min"] += minutes
        if modus in MODE_WW:
            self.data["ww_min"] += minutes
        elif modus in MODE_HZ:
            self.data["hz_min"] += minutes
        elif modus in MODE_KUEHL:
            self.data["kuehl_min"] += minutes
        elif modus == 15:
            self.data["standby_min"] += minutes

        self.prev = {"verdichter": verdichter, "verdichterstatus": status}


class Bridge:
    def __init__(self):
        with open(CATALOG) as fh:
            self.catalog = json.load(fh)
        self.devices = []
        used = {}
        for entry in self.catalog["devices"]:
            role = entry["role"]
            used[role] = used.get(role, 0) + 1
            ident = role if used[role] == 1 else f"{role}{used[role]}"
            self.devices.append(Device(entry, ident))
        self.by_ident = {d.ident: d for d in self.devices}
        self.ip = None
        self.counters = Counters(STATE_FILE)
        self.restore()
        self.lock = threading.Lock()
        self.client = mqtt.Client(client_id=f"wolf-bridge-{os.getpid()}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        LOG.info("Katalog: %d Geräte, %d Parameter",
                 len(self.devices), sum(len(d.params) for d in self.devices))

    def restore(self):
        """Den letzten bekannten Zustand aus /state/wolf.json holen."""
        try:
            with open(STATE_FILE) as fh:
                saved = json.load(fh)
        except FileNotFoundError:
            return
        except Exception as exc:
            LOG.warning("Zustand nicht lesbar (%s), fange leer an", exc)
            return
        self.ip = saved.get("ip") or self.ip
        total = 0
        for d in self.devices:
            entry = saved.get("devices", {}).get(d.ident)
            if not entry:
                continue
            d.topic = entry.get("topic")
            total += d.restore(entry)
        if total:
            LOG.info("Zustand übernommen: %d Werte aus %s", total, STATE_FILE)

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            payload = {"day": self.counters.day, "data": self.counters.data, "ip": self.ip,
                       "devices": {d.ident: {"topic": d.topic, "values": d.values, "texts": d.texts}
                                   for d in self.devices}}
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception as exc:
            LOG.warning("Zustand nicht schreibbar: %s", exc)

    # ---------------------------------------------------------------- MQTT
    def on_connect(self, client, userdata, flags, rc):
        LOG.info("MQTT verbunden (rc=%s)", rc)
        client.subscribe("Wolf/+/+")
        client.subscribe(f"{BASE}/grolo/wolf/set")
        client.subscribe(f"{BASE}/grolo/wolf/unlock")
        self.publish_catalog()

    def on_message(self, client, userdata, msg):
        try:
            if msg.topic.startswith("Wolf/"):
                self.on_wolf(msg)
            elif msg.topic.endswith("/wolf/set"):
                self.on_set(msg)
            elif msg.topic.endswith("/wolf/unlock"):
                self.on_unlock(msg)
        except Exception:
            LOG.exception("Nachricht auf %s nicht verarbeitet", msg.topic)

    def on_wolf(self, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[2].endswith("set"):
            return
        payload = json.loads(msg.payload.decode())
        if not isinstance(payload, dict):
            return
        with self.lock:
            self.ip = parts[1]
            device = self.bind(msg.topic, payload)
            if device is None:
                LOG.warning("Kein Katalogeintrag für %s", msg.topic)
                return
            device.apply(payload)

    def bind(self, topic, payload):
        """Wolf-Topic einem Katalogeintrag zuordnen: gleiche Busadresse, meiste passende Parameternamen."""
        for d in self.devices:
            if d.topic == topic:
                return d
        bus = topic.rsplit("_", 1)[-1].lower()
        names = list(payload)
        best, score = None, 0
        for d in self.devices:
            if d.topic is not None or int(d.bus, 16) != int(bus, 16):
                continue
            s = d.match(names)
            if s > score:
                best, score = d, s
        if best is None or score == 0:
            return None
        best.topic = topic
        LOG.info("%s -> %s (%s, %d passende Namen)", topic, best.ident, best.label_de, score)
        self.publish_catalog()
        return best

    # ---------------------------------------------------------------- schreiben
    def on_unlock(self, msg):
        """Fachmann-Code vorab prüfen, damit die Bedienseite den Bereich erst nach dem Code aufklappt."""
        try:
            pin = str(json.loads(msg.payload.decode()).get("pin", ""))
        except Exception:
            pin = ""
        ok = not EXPERT_PIN or pin == EXPERT_PIN
        if not ok:
            LOG.warning("Fachmann-Code abgelehnt")
        self.client.publish(f"{BASE}/grolo/wolf/unlock/result",
                            json.dumps({"ok": ok, "required": bool(EXPERT_PIN)}))

    def on_set(self, msg):
        req = json.loads(msg.payload.decode())
        ident, key = req.get("device"), req.get("key")
        result = {"device": ident, "key": key, "ok": False}
        device = self.by_ident.get(ident)
        if device is None:
            result["error"] = f"unbekanntes Gerät {ident}"
        elif device.topic is None:
            result["error"] = f"{ident} noch nicht auf dem Bus gesehen"
        else:
            entry = device.by_key.get(key)
            if entry is None:
                result["error"] = f"unbekannter Parameter {key}"
            elif not entry["rw"]:
                result["error"] = f"{entry['de']} ist nur lesbar"
            elif entry["menu"] == "expert" and EXPERT_PIN and str(req.get("pin", "")) != EXPERT_PIN:
                result["error"] = f"{entry['de']} gehört zur Fachmann-Ebene, der Code fehlt oder stimmt nicht"
            else:
                value, error = self.resolve(entry, req)
                if error:
                    result["error"] = error
                else:
                    node = {"value": value} if entry.get("options") else value
                    if entry["dup"]:
                        node = {str(entry["ptid"]): node}
                    topic = f"{device.topic}/set"
                    self.client.publish(topic, json.dumps({entry["de"]: node}, ensure_ascii=False))
                    LOG.info("schreibe %s.%s = %s (%s)", ident, key, value, entry["de"])
                    result.update(ok=True, value=value, wrote={entry["de"]: node}, topic=topic)
        if not result["ok"]:
            LOG.warning("Schreibauftrag abgelehnt: %s", result.get("error"))
        self.client.publish(f"{BASE}/grolo/wolf/set/result", json.dumps(result, ensure_ascii=False))

    @staticmethod
    def resolve(entry, req):
        """Wunschwert gegen den Katalog prüfen und in die Form bringen, die ism7mqtt erwartet."""
        if "text" in req and entry.get("options"):
            want = str(req["text"]).strip().lower()
            for o in entry["options"]:
                if want in (o["de"].lower(), o["en"].lower()):
                    return o["v"], None
            return None, f"'{req['text']}' ist keine Auswahl von {entry['de']}"
        if "value" not in req:
            return None, "weder value noch text angegeben"
        value = req["value"]
        if entry.get("options"):
            allowed = {o["v"] for o in entry["options"]}
            if str(value) not in allowed:
                return None, f"{value} ist keine Auswahl von {entry['de']}"
            return str(value), None
        if entry["type"] in ("time", "date", "text"):
            return str(value), None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None, f"{value} ist keine Zahl"
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and number < lo:
            return None, f"{number} liegt unter dem Minimum {lo}"
        if hi is not None and number > hi:
            return None, f"{number} liegt über dem Maximum {hi}"
        return (int(number) if float(number).is_integer() else number), None

    # ---------------------------------------------------------------- veröffentlichen
    def publish_catalog(self):
        payload = {
            "generated": self.catalog.get("generated"), "ip": self.ip, "expert_pin": bool(EXPERT_PIN),
            "devices": [{"id": d.ident, "role": d.role, "dtid": d.dtid, "bus": d.bus,
                         "de": d.label_de, "en": d.label_en, "online": d.topic is not None,
                         "params": d.params} for d in self.devices],
        }
        self.client.publish(f"{BASE}/grolo/wolf/catalog", json.dumps(payload, ensure_ascii=False), retain=True)

    def derived(self, hp):
        """Kennzahlen, die die Wolf selbst nicht ausgibt."""
        v = hp.values
        out = {}
        get = v.get
        p_th, p_el = get("aktuelle_sekundaerleistung"), get("leistungsaufnahme_wp_ehz")
        if p_th is not None:
            out["waerme_kw"] = p_th
        if p_el is not None:
            out["strom_kw"] = p_el
            out["strom_w"] = round(p_el * 1000)
        if p_th and p_el and p_el > 0.2 and get("verdichter") == 1:
            out["cop"] = round(p_th / p_el, 2)
        kessel, ruecklauf = get("kesseltemperatur"), get("ruecklauftemperatur")
        if kessel is not None and ruecklauf is not None:
            out["spreizung"] = round(kessel - ruecklauf, 2)
        if get("soll_spreizung") is not None:
            out["spreizung_soll"] = get("soll_spreizung")
        for key in ("verdichter", "verdichterfrequenz", "aktuelle_leistungsvorgabe_verdichter",
                    "verdichterstarts", "betriebsstunden_verdichter", "betriebsstunden_e_heizung",
                    "jaz_aktuelles_jahr", "taz_vortag", "aussentemperatur", "warmwassertemperatur",
                    "heizkreisdurchfluss", "anlagendruck"):
            if get(key) is not None:
                out[key] = get(key)

        c = self.counters.data
        out["takte_heute"] = int(c["takte"])
        for field in ("laufzeit_min", "abtau_min", "ww_min", "hz_min", "kuehl_min", "eheiz_min", "sperr_min"):
            out[f"{field}_heute"] = round(c[field], 1)
        out["abtauungen_heute"] = int(c["abtauungen"])
        if c["takte"] >= 1:
            out["laufzeit_je_takt_min"] = round(c["laufzeit_min"] / c["takte"], 1)

        # Arbeitszahlen aus den Wolf-Statistikregistern: erzeugte Wärme je verbrauchtem Strom
        for span, warm, strom in (("tag", "erzeugte_waermemenge_aktueller_tag", "verbrauch_aktueller_tag"),
                                  ("monat", "erzeugte_waermemenge_aktueller_monat", "verbrauch_aktueller_monat"),
                                  ("jahr", "erzeugte_waermemenge_aktuelles_jahr", "verbrauch_aktuelles_jahr")):
            w, s = get(warm), get(strom)
            if w is not None:
                out[f"waerme_{span}"] = w
            if s is not None:
                out[f"strom_{span}"] = s
            if w and s and s > 0:
                out[f"az_{span}"] = round(w / s, 2)
        return out

    def publish(self):
        with self.lock:
            hp = next((d for d in self.devices if d.role == "heatpump" and d.topic), None)
            self.counters.tick(hp.values if hp else None)
            for d in self.devices:
                if d.topic is None or not d.values:
                    continue
                self.client.publish(f"{BASE}/grolo/wolf/{d.ident}/state",
                                    json.dumps(d.state_payload(), ensure_ascii=False), retain=True)
            if hp:
                self.client.publish(f"{BASE}/grolo/wolf/derived",
                                    json.dumps(self.derived(hp), ensure_ascii=False), retain=True)
            status = {
                "ip": self.ip, "ts": int(time.time()),
                "devices": [{"id": d.ident, "de": d.label_de, "en": d.label_en, "bus": d.bus,
                             "topic": d.topic, "values": len(d.values),
                             "age": round(time.time() - d.seen) if d.seen else None} for d in self.devices],
            }
            self.client.publish(f"{BASE}/grolo/wolf/status", json.dumps(status, ensure_ascii=False), retain=True)
        self.save_state()

    def run(self):
        self.client.connect(HOST, PORT, 60)
        self.client.loop_start()
        while True:
            time.sleep(INTERVAL)
            try:
                self.publish()
            except Exception:
                LOG.exception("Veröffentlichen fehlgeschlagen")


if __name__ == "__main__":
    Bridge().run()
