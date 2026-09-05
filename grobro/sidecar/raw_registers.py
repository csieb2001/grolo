#!/usr/bin/env python3
"""Sidecar: veröffentlicht ALLE Rohregister des NEXA (auch die, die GroBro nicht kennt) als JSON.

  Eingangsregister (Datenframe 0x0104, alle paar Sekunden) -> <BASE>/grobro/<sn>/raw_input
  Halteregister    (Stunden-Dump  0x0103)                  -> <BASE>/grobro/<sn>/raw_holding  (retained)

Zweck: unbekannte Register über die Zeit beobachten und mit bekannten Werten korrelieren (Forschungswerkzeug).
Nur lesend. Läuft im GroBro-Image mit dessen Parser (PYTHONPATH=/app).
"""
import json, logging, os, struct, time
import paho.mqtt.client as mqtt
from grobro.grobro import parser
import grobro.model.modbus_message as mm

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
REGMAP = os.getenv("REGMAP", "/app/grobro/model/growatt_nexa_registers.json")
ONLY_UNKNOWN = os.getenv("ONLY_UNKNOWN", "true").lower() == "true"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("raw-registers")

known_in, known_hold = set(), set()
try:
    m = json.load(open(REGMAP))
    for v in m["input_registers"].values():
        p = v["growatt"]["position"]; known_in.update(range(p["register_no"], p["register_no"] + max(1, p["size"] // 2)))
    for v in m["holding_registers"].values():
        known_hold.add(v["growatt"]["position"]["register_no"])
except Exception as e:
    LOG.warning("Register-Map nicht lesbar (%s), veröffentliche alle Register", e)


def parse_input(u: bytes):
    msg = mm.GrowattModbusMessage.parse_grobro(u)
    if not msg or not msg.register_blocks:
        return None, None
    regs = {}
    for b in msg.register_blocks:
        for i, v in enumerate(struct.unpack(f">{len(b.values) // 2}H", b.values)):
            regs[b.start + i] = v
    return msg.device_id, regs


def parse_holding(u: bytes):
    """0x0103: Header(8) + 2x Seriennummer(30) + ... + Blöcke (start,end,values). Blockstart wird gesucht."""
    device = u[8:38].rstrip(b"\x00").decode("ascii", "replace")
    for off in range(60, 120):
        regs, pos, ok = {}, off, False
        while pos + 4 <= len(u):
            st, en = struct.unpack_from(">HH", u, pos); n = en - st + 1
            if not (0 <= st < en < 2000 and n <= 512 and pos + 4 + 2 * n <= len(u)):
                break
            for i, v in enumerate(struct.unpack_from(f">{n}H", u, pos + 4)):
                regs[st + i] = v
            pos += 4 + 2 * n; ok = True
            if len(u) - pos < 4:
                break
        if ok and len(regs) >= 100 and regs.get(250, 0) <= 100 and regs.get(251, 0) <= 30:
            return device, regs
    return device, None


def publish(client, topic, device, regs, known, retain):
    sel = {r: v for r, v in regs.items() if (not ONLY_UNKNOWN or r not in known)}
    payload = {"device": device, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(regs),
               "registers": {f"r{r}": v for r, v in sorted(sel.items())}}
    client.publish(topic, json.dumps(payload), retain=retain)


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s, bekannte Register: input=%d holding=%d", HOST, PORT, len(known_in), len(known_hold))
    client.subscribe("c/#")


def on_message(client, userdata, msg):
    try:
        u = parser.unscramble(msg.payload)
        if len(u) < 8:
            return
        t = struct.unpack_from(">H", u, 6)[0]
        if t == 0x0104:
            device, regs = parse_input(u)
            if regs:
                publish(client, f"{BASE}/grobro/{device}/raw_input", device, regs, known_in, False)
        elif t == 0x0103:
            device, regs = parse_holding(u)
            if regs:
                publish(client, f"{BASE}/grobro/{device}/raw_holding", device, regs, known_hold, True)
                LOG.info("Halteregister-Dump von %s: %d Register", device, len(regs))
            else:
                LOG.warning("0x0103 von %s: Blockstruktur nicht gefunden", device)
    except Exception as e:
        LOG.debug("Frame %s: %s", msg.topic, e)


client = mqtt.Client(client_id="grobro-raw-registers", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect; client.on_message = on_message
while True:
    try:
        client.connect(HOST, PORT, 60); break
    except Exception as e:
        LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch", e); time.sleep(5)
client.loop_forever()
