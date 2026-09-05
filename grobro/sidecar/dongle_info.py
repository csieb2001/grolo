#!/usr/bin/env python3
"""Sidecar: decodiert die Konfigurationsnachricht (0xFE19) des Growatt-Dongles und veröffentlicht sie
als retained JSON auf <HA_BASE_TOPIC>/grobro/<Seriennummer>/dongle.

Hintergrund: GroBro (Image :latest) wertet FE19 nur für NOAH (0PVP...) aus, nicht für NEXA (0HVR...).
Läuft im GroBro-Image, nutzt dessen Parser. Liest zusätzlich beim Start alle Frames aus /dump.
"""
import json, logging, os, struct, sys, time
import paho.mqtt.client as mqtt
from grobro.grobro import parser

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
DUMP = os.getenv("DUMP_DIR", "/dump")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("dongle-info")

FIELDS = ["serial_number", "device_type", "model_id", "sw_version", "hw_version", "protocol_version", "mac_address",
          "local_ip", "subnet_mask", "default_gateway", "dns_address", "remote_url", "remote_ip", "remote_port",
          "data_interval", "timezone", "datetime", "wifi_signal", "unknown_port"]


def decode(raw: bytes):
    u = parser.unscramble(raw)
    if len(u) < 26 or struct.unpack_from(">H", u, 6)[0] != 0xFE19:
        return None
    msg = parser.parse_noah_fe19(u)
    cfg = msg.get("config")
    if cfg is None:
        return None
    d = cfg.model_dump(exclude_none=True)
    out = {k: d[k] for k in FIELDS if k in d}
    extra = {k: v for k, v in d.items() if k not in FIELDS and k != "raw"}
    if extra:
        out["extra"] = extra
    out["subtype"] = msg.get("subtype")
    out["device_id"] = msg.get("device_id")
    return out


def publish(client, device_id, info):
    info["received_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    topic = f"{BASE}/grobro/{device_id}/dongle"
    client.publish(topic, json.dumps(info, ensure_ascii=False), retain=True)
    LOG.info("Dongle-Info für %s veröffentlicht: %s", device_id, {k: info.get(k) for k in ("model_id", "sw_version", "hw_version", "mac_address", "local_ip", "wifi_signal")})


def scan_dump(client):
    best = {}
    for root, _, files in os.walk(DUMP):
        for f in sorted(files):
            if f.startswith("."):
                continue
            try:
                info = decode(open(os.path.join(root, f), "rb").read())
            except Exception as e:
                LOG.debug("Dump %s: %s", f, e); continue
            if info and info.get("subtype") == 0x20 and info.get("model_id"):
                best[info["device_id"] or root.split("/")[-1]] = info
    for dev, info in best.items():
        publish(client, dev, info)


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s", HOST, PORT)
    client.subscribe("c/#")
    scan_dump(client)


def on_message(client, userdata, msg):
    try:
        info = decode(msg.payload)
    except Exception as e:
        LOG.debug("Frame %s nicht dekodierbar: %s", msg.topic, e); return
    if not info:
        return
    device_id = info.get("device_id") or msg.topic.split("/")[-1]
    if info.get("subtype") == 0x20 or info.get("model_id"):
        publish(client, device_id, info)
    else:
        LOG.info("FE19 Subtyp 0x%04x von %s ohne Modelldaten: %s", info.get("subtype") or 0, device_id, info)


client = mqtt.Client(client_id="grobro-dongle-info", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect; client.on_message = on_message
while True:
    try:
        client.connect(HOST, PORT, 60); break
    except Exception as e:
        LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch", e); time.sleep(5)
client.loop_forever()
