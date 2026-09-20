#!/usr/bin/env python3
"""tado-bridge: übersetzt zwischen dem Matter-Controller und dem GroLo-Stack – vollständig lokal.

tado bietet für die X-Reihe kein lokales HTTP-API; ihre eigene Aussage lautet „tado provides no local API —
neither on their classic devices nor on the new Tado X line". Der lokale Weg ist Matter: die X-Geräte sind
Matter-over-Thread, und tado unterstützt Multi-Admin. In der tado-App gibt es je Gerät unter
„Einstellungen → Räume und Geräte → Matter-Geräteverknüpfung" einen Kopplungscode, mit dem sich dasselbe
Gerät einer zweiten Fabric hinzufügen lässt, ohne es aus tado zu entfernen.

Dieses Sidecar spricht die WebSocket-API des Matter-Servers (Open Home Foundation, auf matter.js, Port 5580)
und veröffentlicht:

  <BASE>/grolo/tado/rooms         je Raum Ist, Soll, Betriebsart, Feuchte, Batteriestufe und die Geräte
                                  darin (retained). Mehrere Geräte mit demselben Namen bilden einen Raum;
                                  die Raumtemperatur kommt dann vom Funkfühler, nicht vom Thermostat.
  <BASE>/grolo/tado/<raum>/state  flache Zahlenfelder je Raum für Telegraf -> Measurement "tado"
  <BASE>/grolo/tado/status        Verbindungszustand, gefundene Knoten, Fabric (retained)

Schreiben – eine Nachricht auf <BASE>/grolo/tado/set:
    {"room": "wohnzimmer", "setpoint": 21.5}
    {"room": "bad", "mode": "off"}
    {"room": "node-1", "name": "Wohnzimmer"}     setzt den Raumnamen im Gerät (NodeLabel)
wird gegen die Grenzwerte geprüft, die das Gerät selbst meldet, und als Matter-Attributschreibung
weitergereicht. Das Ergebnis kommt auf <BASE>/grolo/tado/set/result.

Koppeln – eine Nachricht auf <BASE>/grolo/tado/commission:
    {"code": "12345678901"}
fügt ein Gerät unserer Fabric hinzu (network_only: das Gerät ist schon im Thread-Netz, wir brauchen keine
Thread-Zugangsdaten, nur die IPv6-Route dorthin). Ergebnis auf <BASE>/grolo/tado/commission/result.

Ein WebSocket-Client steckt hier mit drin, weil das Basis-Image keine WebSocket-Bibliothek hat – wie beim
Rest des Stacks lieber hundert Zeilen eigener Code als eine weitere Abhängigkeit.

Umgebung: MQTT_HOST/PORT, HA_BASE_TOPIC, MATTER_URL (Standard ws://127.0.0.1:5580/ws),
          TADO_INTERVAL (s, Standard 30), TZ
"""
import base64, json, logging, os, socket, struct, threading, time, urllib.parse
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
MATTER_URL = os.getenv("MATTER_URL", "ws://127.0.0.1:5580/ws")
INTERVAL = float(os.getenv("TADO_INTERVAL", "30"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("tado-bridge")

# ---------------------------------------------------------------------------- Matter-Cluster
# (Cluster, Attribut, Faktor) – die Zahlen sind die der Matter-Spezifikation, hier einmal benannt,
# damit weiter unten niemand mehr Hexadezimalzahlen lesen muss.
# Was ein tado X Smart Radiator Thermostat (Firmware 1.4.289) tatsächlich anbietet, nachgesehen am Gerät.
# Die Ventilstellung (PIHeatingDemand, 0x0008) gehört nicht dazu – die behält tado in ihrer Cloud. Und die
# Batterie meldet nur eine Stufe, keine Prozent: BatPercentRemaining fehlt, BatChargeLevel ist da.
FIELDS = {
    "temp_c":        (513, 0x0000, 0.01),  # Thermostat: LocalTemperature, Hundertstelgrad
    "setpoint_c":    (513, 0x0012, 0.01),  # OccupiedHeatingSetpoint
    "mode":          (513, 0x001C, 1),     # SystemMode: 0 aus, 4 heizen
    "sequence":      (513, 0x001B, 1),     # ControlSequenceOfOperation: 2 = nur Heizen
    "valve_pct":     (513, 0x0008, 1),     # PIHeatingDemand, falls ein Gerät es doch liefert
    "min_c":         (513, 0x0015, 0.01),  # MinHeatSetpointLimit, falls gesetzt
    "max_c":         (513, 0x0016, 0.01),  # MaxHeatSetpointLimit, falls gesetzt
    "abs_min_c":     (513, 0x0003, 0.01),  # AbsMinHeatSetpointLimit
    "abs_max_c":     (513, 0x0004, 0.01),  # AbsMaxHeatSetpointLimit
    "temp_sensor_c": (1026, 0x0000, 0.01), # TemperatureMeasurement: der frei hängende Funkfühler
    "humidity_pct":  (1029, 0x0000, 0.01), # RelativeHumidityMeasurement
    "battery_level": (47, 0x000E, 1),      # BatChargeLevel: 0 ok, 1 niedrig, 2 kritisch
    "battery_replace": (47, 0x000F, 1),    # BatReplacementNeeded
}
INFO = {                                    # Gerätedaten aus BasicInformation, für die Hardware-Tabelle
    "vendor": (40, 0x0001), "product": (40, 0x0003), "serial": (40, 0x000F),
    "firmware": (40, 0x000A), "label": (40, 0x0005),
}
LABEL = (40, 0x0005)                        # NodeLabel: beschreibbar, hier der Raumname
SENSOR = FIELDS["temp_sensor_c"]            # ein Gerät ohne Thermostat, aber mit diesem Cluster, ist ein Fühler
# Nur Heizen: welche Betriebsarten das Gerät laut ControlSequenceOfOperation zulässt
SEQUENCE_MODES = {0: ("off", "cool"), 1: ("off", "cool"), 2: ("off", "heat"), 3: ("off", "heat"),
                  4: ("off", "heat", "cool", "auto"), 5: ("off", "heat", "cool", "auto")}
SETPOINT = FIELDS["setpoint_c"]
MODE = FIELDS["mode"]
MODES = {0: "off", 1: "auto", 3: "cool", 4: "heat", 5: "emergency_heat", 7: "fan_only"}
MODE_VALUES = {v: k for k, v in MODES.items()}
# Name des Geräts: erst der selbst vergebene (NodeLabel), sonst der Produktname
LABEL_ATTRS = [(57, 0x0005), (40, 0x0005), (57, 0x0003), (40, 0x0003)]


# ---------------------------------------------------------------------------- WebSocket
class WebSocket:
    """Das Nötigste aus RFC 6455: Handshake, Textrahmen senden und empfangen, Ping beantworten."""

    def __init__(self, url, timeout=20):
        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        if u.scheme == "wss":
            raise ValueError("TLS wird hier nicht gebraucht – der Matter-Server läuft lokal")
        self.sock = socket.create_connection((u.hostname, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("Verbindung beim Handshake abgerissen")
            head += chunk
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(f"kein Upgrade: {head.split(chr(13).encode())[0][:80]!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        # Nach dem Handshake ohne Zeitgrenze lesen: zwischen zwei Ereignissen kann es lange still sein,
        # und ein Timeout würde den Lese-Thread beenden statt zu warten.
        self.sock.settimeout(None)
        self.lock = threading.Lock()

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("Verbindung geschlossen")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode, payload):
        """Ein Rahmen vom Client ist immer maskiert – das schreibt RFC 6455 so vor."""
        head = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127); head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        return bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send(self, text):
        with self.lock:
            self.sock.sendall(self._frame(0x1, text.encode()))

    def recv(self):
        """Eine vollständige Textnachricht; Steuerrahmen werden unterwegs beantwortet."""
        data = b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            payload = self._read(n) if n else b""
            if opcode == 0x9:                      # Ping -> Pong mit demselben Inhalt
                with self.lock:
                    self.sock.sendall(self._frame(0xA, payload))
                continue
            if opcode == 0xA:                      # Pong
                continue
            if opcode == 0x8:
                raise ConnectionError("Gegenstelle hat geschlossen")
            data += payload
            if fin:
                return data.decode()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------- Matter-Client
class Matter:
    """Befehle an den Matter-Server, Antworten nach message_id zugeordnet, Ereignisse per Rückruf."""

    def __init__(self, url, on_event):
        self.ws = WebSocket(url)
        self.on_event = on_event
        self.n = 0
        self.pending = {}
        self.lock = threading.Lock()
        self.info = json.loads(self.ws.recv())          # der Server stellt sich zuerst vor
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception as exc:
                LOG.warning("Verbindung zum Matter-Server verloren: %s", exc)
                with self.lock:
                    for ev in self.pending.values():
                        ev["error"] = str(exc); ev["done"].set()
                    self.pending.clear()
                return
            mid = msg.get("message_id")
            if mid is not None:
                with self.lock:
                    slot = self.pending.pop(str(mid), None)
                if slot:
                    slot["msg"] = msg; slot["done"].set()
            elif msg.get("event"):
                try:
                    self.on_event(msg)
                except Exception:
                    LOG.exception("Ereignis nicht verarbeitet")

    def cmd(self, command, timeout=90, **args):
        with self.lock:
            self.n += 1
            mid = str(self.n)
            slot = {"done": threading.Event()}
            self.pending[mid] = slot
        self.ws.send(json.dumps({"message_id": mid, "command": command, "args": args}))
        if not slot["done"].wait(timeout):
            with self.lock:
                self.pending.pop(mid, None)
            raise TimeoutError(f"{command} ohne Antwort")
        if slot.get("error"):
            raise ConnectionError(slot["error"])
        msg = slot["msg"]
        if "error_code" in msg:
            raise RuntimeError(msg.get("details") or f"Fehler {msg['error_code']} bei {command}")
        return msg.get("result")


# ---------------------------------------------------------------------------- Räume
def pick(attrs, cluster, attribute):
    """Wert eines Attributs, egal auf welchem Endpunkt er liegt – und der niedrigste Endpunkt gewinnt."""
    best = None
    for key, value in attrs.items():
        parts = key.split("/")
        if len(parts) != 3:
            continue
        ep, cl, at = (int(p) for p in parts)
        if cl == cluster and at == attribute and (best is None or ep < best[0]):
            best = (ep, value)
    return best


def endpoint_of(attrs, cluster, attribute):
    hit = pick(attrs, cluster, attribute)
    return hit[0] if hit else None


def temperature_of(dev):
    """Die Temperatur eines Geräts – beim Fühler aus dem Messcluster, beim Thermostat aus dem Thermostat."""
    return dev.get("temp_sensor_c") if dev["kind"] == "sensor" else dev.get("temp_c")


def lead_device(devs):
    """Welches Gerät die Raumtemperatur stellt.

    Am liebsten ein erreichbarer Funkfühler: der hängt frei im Raum. Ist er nicht erreichbar oder meldet
    er nichts, zählt ein erreichbares Thermostat – lieber ein Wert mit Wärmestau als gar keiner. Erst
    danach kommen Geräte, die zwar einen alten Wert tragen, aber als nicht erreichbar gemeldet sind.
    """
    def usable(kind, need_available):
        return [d for d in devs if d["kind"] == kind and temperature_of(d) is not None
                and (d.get("available") or not need_available)]

    for group in (usable("sensor", True), usable("thermostat", True),
                  usable("sensor", False), usable("thermostat", False), devs):
        if group:
            return group[0]


def slug(text, fallback):
    out = "".join(c.lower() if c.isalnum() else "-" for c in (text or "")).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or fallback


class Rooms:
    """Der Stand aller gekoppelten Geräte, nach Raum benannt."""

    def __init__(self):
        self.nodes = {}          # node_id -> {"attributes": {...}, "available": bool}
        self.lock = threading.Lock()

    def load(self, nodes):
        with self.lock:
            self.nodes = {int(n["node_id"]): n for n in nodes}

    def update(self, node_id, path, value):
        with self.lock:
            node = self.nodes.get(int(node_id))
            if node is not None:
                node.setdefault("attributes", {})[path] = value

    def set_available(self, node_id, available):
        with self.lock:
            node = self.nodes.get(int(node_id))
            if node is not None:
                node["available"] = available

    def devices(self):
        """Ein Eintrag je Gerät, mit Rohwerten und Gerätedaten. Alles, was weder Thermostat noch
        Temperaturfühler ist, bleibt draußen - etwa eine Bridge."""
        out = []
        with self.lock:
            nodes = {k: dict(v) for k, v in self.nodes.items()}
        for node_id, node in nodes.items():
            attrs = node.get("attributes") or {}
            is_thermostat = endpoint_of(attrs, *SETPOINT[:2]) is not None
            is_sensor = endpoint_of(attrs, *SENSOR[:2]) is not None
            if not is_thermostat and not is_sensor:
                continue
            label = None
            for cl, at in LABEL_ATTRS:
                hit = pick(attrs, cl, at)
                if hit and isinstance(hit[1], str) and hit[1].strip():
                    label = hit[1].strip(); break
            entry = {"node": node_id, "name": label or f"Node {node_id}",
                     "kind": "thermostat" if is_thermostat else "sensor",
                     "available": bool(node.get("available", True))}
            for key, (cl, at) in INFO.items():
                hit = pick(attrs, cl, at)
                if hit and hit[1] not in (None, ""):
                    entry[key] = hit[1]
            for key, (cl, at, factor) in FIELDS.items():
                hit = pick(attrs, cl, at)
                if hit is None or hit[1] is None:
                    continue
                entry[key] = round(hit[1] * factor, 2) if factor != 1 else hit[1]
            if "mode" in entry:
                entry["mode_text"] = MODES.get(entry["mode"], str(entry["mode"]))
            if is_thermostat:
                entry["modes"] = list(SEQUENCE_MODES.get(entry.get("sequence"), ("off", "heat")))
            if "battery_level" in entry:
                entry["battery_text"] = {0: "ok", 1: "niedrig", 2: "kritisch"}.get(entry["battery_level"], "?")
            entry["limit_min_c"] = entry.get("min_c", entry.get("abs_min_c", 5.0))
            entry["limit_max_c"] = entry.get("max_c", entry.get("abs_max_c", 30.0))
            out.append(entry)
        return out

    def view(self):
        """Ein Eintrag je *Raum*, nicht je Gerät.

        In einem Raum können mehrere Geräte hängen: zwei Heizkörper, oder ein Thermostat plus ein
        Funk-Temperaturfühler. Geräte mit demselben Namen bilden einen Raum. Für die Raumtemperatur
        zählt dann der Fühler und nicht das Thermostat: das sitzt am Heizkörper und misst dessen
        Wärmestau mit, der Fühler hängt frei im Raum. Sollwert und Betriebsart kommen von den
        Thermostaten, denn nur die können heizen.
        """
        rooms = {}
        for dev in self.devices():
            rooms.setdefault(slug(dev["name"], f"node-{dev['node']}"), []).append(dev)
        out = {}
        for room, devs in rooms.items():
            thermostats = [d for d in devs if d["kind"] == "thermostat"]
            sensors = [d for d in devs if d["kind"] == "sensor"]
            lead = lead_device(devs)
            control = thermostats[0] if thermostats else None
            entry = {"room": room, "name": devs[0]["name"],
                     "available": any(d.get("available") for d in devs),
                     "nodes": sorted(d["node"] for d in devs),
                     "thermostats": sorted(d["node"] for d in thermostats),
                     "sensors": sorted(d["node"] for d in sensors),
                     "temp_source": lead["kind"]}
            # Fällt der Fühler aus – leere Batterie, außer Funkreichweite –, springt die Raumtemperatur
            # nicht auf null, sondern auf das Thermostat zurück. Der Wechsel wird ausgewiesen, damit später
            # niemand rätselt, warum der Wert einen Sprung gemacht hat.
            if sensors and lead["kind"] != "sensor":
                entry["temp_fallback"] = True
            temp = temperature_of(lead)
            if temp is not None:
                entry["temp_c"] = temp
            # Feuchte ebenfalls bevorzugt vom Fühler, sonst vom ersten Gerät, das eine meldet
            for d in ([lead] + devs):
                if d.get("humidity_pct") is not None:
                    entry["humidity_pct"] = d["humidity_pct"]; break
            if control:
                for key in ("setpoint_c", "mode", "mode_text", "modes", "limit_min_c", "limit_max_c"):
                    if key in control:
                        entry[key] = control[key]
                # Weichen mehrere Thermostate im Raum voneinander ab, fällt das hier auf
                setpoints = {d.get("setpoint_c") for d in thermostats if d.get("setpoint_c") is not None}
                if len(setpoints) > 1:
                    entry["setpoint_split"] = sorted(setpoints)
            worst = [d["battery_level"] for d in devs if d.get("battery_level") is not None]
            if worst:
                entry["battery_level"] = max(worst)
                entry["battery_text"] = {0: "ok", 1: "niedrig", 2: "kritisch"}.get(entry["battery_level"], "?")
            # Das Thermostat misst am Heizkörper; die Differenz zum Fühler ist der Wärmestau dort
            if sensors and thermostats:
                s, t = temperature_of(sensors[0]), thermostats[0].get("temp_c")
                if s is not None and t is not None and sensors[0].get("available"):
                    entry["radiator_offset_k"] = round(t - s, 2)
            entry["devices"] = devs
            out[room] = entry
        return out


# ---------------------------------------------------------------------------- Dienst
class Bridge:
    def __init__(self):
        self.rooms = Rooms()
        self.matter = None
        # Ereignisse kommen im Lese-Thread an. Von dort darf kein Befehl abgesetzt werden: der Lese-Thread
        # wäre es selbst, der die Antwort zustellen müsste, und würde auf sich selbst warten. Also nur
        # ein Merker, den die Hauptschleife abarbeitet.
        self.stale = threading.Event()
        self.published = set()      # zuletzt veröffentlichte Raumnamen, um verwaiste Topics zu erkennen
        self.client = mqtt.Client(client_id=f"tado-bridge-{os.getpid()}",
                                  callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ---------------------------------------------------------------- MQTT
    def on_connect(self, client, userdata, flags, rc, props=None):
        LOG.info("MQTT verbunden %s:%s", HOST, PORT)
        client.subscribe([(f"{BASE}/grolo/tado/set", 0), (f"{BASE}/grolo/tado/commission", 0)])

    def on_message(self, client, userdata, msg):
        try:
            if msg.topic.endswith("/tado/set"):
                self.on_set(json.loads(msg.payload))
            elif msg.topic.endswith("/tado/commission"):
                self.on_commission(json.loads(msg.payload))
        except Exception:
            LOG.exception("Nachricht auf %s nicht verarbeitet", msg.topic)

    def on_set(self, req):
        """Solltemperatur oder Betriebsart eines Raums setzen, gegen die Grenzen des Geräts geprüft."""
        result = {"room": req.get("room"), "node": req.get("node"), "ok": False}
        rooms = self.rooms.view()
        # Ein Gerät lässt sich über seinen Raumnamen ansprechen oder über die Node-ID. Die ID ist das, was
        # das Koppeln zurückgibt – damit kann ein frisch gekoppeltes Gerät sofort benannt werden, ohne erst
        # abzuwarten, bis es unter seinem Platzhalternamen in der Raumliste auftaucht.
        if req.get("node") is not None:
            room = next((r for r in rooms.values() if int(req["node"]) in r["nodes"]), None)
            if room is None and self.rooms.nodes.get(int(req["node"])) is not None:
                room = {"nodes": [int(req["node"])], "thermostats": [int(req["node"])],
                        "modes": ["off", "heat"], "limit_min_c": 5.0, "limit_max_c": 30.0,
                        "name": f"Node {req['node']}"}
            missing = f"unbekannte Node {req['node']}"
        else:
            room = rooms.get(str(req.get("room", "")).lower())
            missing = f"unbekannter Raum {req.get('room')!r}; bekannt: {', '.join(sorted(rooms)) or 'keiner'}"
        if room is None:
            result["error"] = missing
        elif "setpoint" in req:
            try:
                want = float(req["setpoint"])
            except (TypeError, ValueError):
                want = None
            lo, hi = room["limit_min_c"], room["limit_max_c"]
            if want is None:
                result["error"] = f"{req['setpoint']!r} ist keine Temperatur"
            elif not lo <= want <= hi:
                result["error"] = f"{want} °C liegt außerhalb von {lo} bis {hi} °C"
            elif not room.get("thermostats"):
                result["error"] = f"{room['name']} hat kein Thermostat, nur einen Fühler"
            else:
                # An alle Thermostate des Raums: hängen zwei Heizkörper darin, sollen beide gleich stehen
                for node in room["thermostats"]:
                    ep = endpoint_of((self.rooms.nodes.get(node) or {}).get("attributes") or {}, *SETPOINT[:2])
                    self.matter.cmd("write_attribute", node_id=node,
                                    attribute_path=f"{ep}/{SETPOINT[0]}/{SETPOINT[1]}",
                                    value=int(round(want * 100)))
                LOG.info("%s: Solltemperatur auf %.1f °C (%d Thermostat(e))", room["name"], want,
                         len(room["thermostats"]))
                result.update(ok=True, setpoint=want, nodes=room["thermostats"])
        elif "name" in req:
            # NodeLabel auf dem Gerät setzen: der Raumname lebt dann dort und übersteht jeden Neustart
            want = str(req["name"]).strip()[:32]
            if not want:
                result["error"] = "leerer Name"
            else:
                # Benannt wird ein einzelnes Gerät. Bekommen zwei denselben Namen, bilden sie einen Raum;
                # nur so lässt sich ein Fühler zu seinem Thermostat stellen – oder wieder herauslösen.
                node = int(req["node"]) if req.get("node") is not None else room["nodes"][0]
                ep = endpoint_of((self.rooms.nodes.get(node) or {}).get("attributes") or {}, *LABEL)
                self.matter.cmd("write_attribute", node_id=node,
                                attribute_path=f"{ep}/{LABEL[0]}/{LABEL[1]}", value=want)
                LOG.info("Node %s heißt jetzt %r", node, want)
                result.update(ok=True, name=want, node=node)
                self.stale.set()
        elif "mode" in req:
            want = str(req["mode"]).lower()
            allowed = room.get("modes") or ["off", "heat"]
            if want not in MODE_VALUES:
                result["error"] = f"{want!r} ist keine Betriebsart ({', '.join(MODE_VALUES)})"
            elif want not in allowed:
                result["error"] = f"das Gerät kann nur {', '.join(allowed)} – {want!r} nicht"
            elif not room.get("thermostats"):
                result["error"] = f"{room['name']} hat kein Thermostat, nur einen Fühler"
            else:
                for node in room["thermostats"]:
                    ep = endpoint_of((self.rooms.nodes.get(node) or {}).get("attributes") or {}, *MODE[:2])
                    self.matter.cmd("write_attribute", node_id=node,
                                    attribute_path=f"{ep}/{MODE[0]}/{MODE[1]}", value=MODE_VALUES[want])
                LOG.info("%s: Betriebsart %s", room["name"], want)
                result.update(ok=True, mode=want, nodes=room["thermostats"])
        else:
            result["error"] = "weder setpoint, mode noch name angegeben"
        if not result["ok"]:
            LOG.warning("Schreibauftrag abgelehnt: %s", result.get("error"))
        self.client.publish(f"{BASE}/grolo/tado/set/result", json.dumps(result, ensure_ascii=False))

    def on_commission(self, req):
        """Ein Gerät unserer Fabric hinzufügen. network_only, weil es schon im Thread-Netz hängt."""
        code = str(req.get("code", "")).strip().replace("-", "").replace(" ", "")
        result = {"ok": False}
        if not code:
            result["error"] = "kein Code angegeben"
        else:
            LOG.info("Koppeln gestartet (Code endet auf …%s)", code[-4:])
            try:
                node = self.matter.cmd("commission_with_code", timeout=300, code=code, network_only=True)
                result.update(ok=True, node=node.get("node_id") if isinstance(node, dict) else node)
                LOG.info("Gerät gekoppelt: %s", result["node"])
                self.refresh()          # hier erlaubt: eigener Thread, nicht der Lese-Thread
                self.publish()
            except Exception as exc:
                result["error"] = str(exc)
                LOG.warning("Koppeln fehlgeschlagen: %s", exc)
        self.client.publish(f"{BASE}/grolo/tado/commission/result", json.dumps(result, ensure_ascii=False))

    # ---------------------------------------------------------------- Matter
    def on_event(self, msg):
        event, data = msg.get("event"), msg.get("data")
        if event == "attribute_updated" and isinstance(data, list) and len(data) == 3:
            self.rooms.update(data[0], data[1], data[2])
        elif event in ("node_added", "node_updated", "node_removed", "endpoint_added", "endpoint_removed"):
            self.stale.set()

    def refresh(self):
        nodes = self.matter.cmd("get_nodes") or []
        self.rooms.load(nodes)
        self.log_state(nodes)

    def log_state(self, nodes):
        devs = self.rooms.devices()
        LOG.info("%d Knoten: %d Thermostate, %d Fühler, %d Räume", len(nodes),
                 sum(1 for d in devs if d["kind"] == "thermostat"),
                 sum(1 for d in devs if d["kind"] == "sensor"), len(self.rooms.view()))

    def connect_matter(self):
        while True:
            try:
                self.matter = Matter(MATTER_URL, self.on_event)
                LOG.info("Matter-Server: %s, Fabric %s", self.matter.info.get("sdk_version"),
                         self.matter.info.get("fabric_id"))
                nodes = self.matter.cmd("start_listening") or []
                self.rooms.load(nodes)
                self.log_state(nodes)
                return
            except Exception as exc:
                LOG.warning("Matter-Server nicht erreichbar (%s), neuer Versuch in 15 s", exc)
                time.sleep(15)

    # ---------------------------------------------------------------- veröffentlichen
    def publish(self):
        rooms = self.rooms.view()
        self.client.publish(f"{BASE}/grolo/tado/rooms", json.dumps(rooms, ensure_ascii=False), retain=True)
        # Wird ein Raum umbenannt, ändert sich sein Topic. Das alte bliebe als retained Nachricht stehen und
        # Telegraf schriebe eine Karteileiche weiter – deshalb leeren wir es ausdrücklich.
        for gone in self.published - set(rooms):
            self.client.publish(f"{BASE}/grolo/tado/{gone}/state", None, retain=True)
            LOG.info("Raum %s verschwunden, Topic geleert", gone)
        self.published = set(rooms)
        for room, entry in rooms.items():
            flat = {k: v for k, v in entry.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            flat["online"] = 1 if entry.get("available") else 0
            self.client.publish(f"{BASE}/grolo/tado/{room}/state", json.dumps(flat), retain=True)
        status = {"ts": int(time.time()), "rooms": len(rooms),
                  "nodes": len(self.rooms.nodes), "matter": bool(self.matter),
                  "fabric": (self.matter.info.get("fabric_id") if self.matter else None),
                  "sdk": (self.matter.info.get("sdk_version") if self.matter else None)}
        self.client.publish(f"{BASE}/grolo/tado/status", json.dumps(status, ensure_ascii=False), retain=True)

    def run(self):
        while True:
            try:
                self.client.connect(HOST, PORT, 60); break
            except Exception as exc:
                LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch in 10 s", exc); time.sleep(10)
        self.client.loop_start()
        self.connect_matter()
        while True:
            time.sleep(INTERVAL)
            try:
                if self.matter is None or self.matter.ws.sock.fileno() < 0:
                    self.connect_matter()
                if self.stale.is_set():
                    self.stale.clear()
                    self.refresh()
                self.publish()
            except Exception:
                LOG.exception("Veröffentlichen fehlgeschlagen")
                try:
                    self.connect_matter()
                except Exception:
                    pass


if __name__ == "__main__":
    Bridge().run()
