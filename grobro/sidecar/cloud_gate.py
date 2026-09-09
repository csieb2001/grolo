#!/usr/bin/env python3
"""cloud-gate: schaltbare Weiterleitung von GroBro zur Growatt-Cloud.

GroBro (GROWATT_CLOUD=true, FORWARD_MQTT_HOST=cloud-gate) verbindet sich per TLS hierher, so als wäre dies
mqtt.growatt.com. Dieser Dienst terminiert das TLS (eigenes Let's-Encrypt-Zertifikat) und

  - Schalter AN : öffnet pro Verbindung eine TLS-Verbindung zur echten Cloud-IP (SNI mqtt.growatt.com) und
                  reicht den MQTT-Strom in beide Richtungen durch. Rückrichtung (Cloud -> Gerät) wird auf
                  MQTT-Paketebene gelesen; PUBLISH-Pakete, die eine Dongle-Konfiguration schreiben
                  (Growatt-Typ 0x0110 / 0x0118), werden verworfen.
  - Schalter AUS: antwortet selbst als stummer MQTT-Broker (CONNACK, SUBACK, PUBACK, PINGRESP), verwirft alles.
                  GroBro merkt nichts und bleibt ruhig.

Steuerung über MQTT (lokaler Broker):
  <BASE>/switch/grobro/cloud_forward/set   ON | OFF        (Schalter, wird retained gespeichert)
  <BASE>/switch/grobro/cloud_forward/get   ON | OFF        (Zustand, retained)
  <BASE>/grobro/cloud_forward/status       JSON mit Verbindungen, Bytes, verworfenen Paketen (retained)
"""
import asyncio, json, logging, os, socket, ssl, struct, threading, time
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto"); MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "7006"))
CERT = os.getenv("TLS_CERT", "/certs/chain-full.pem"); KEY = os.getenv("TLS_KEY", "/certs/privkey.pem")
CLOUD_HOSTS = [h.strip() for h in os.getenv("CLOUD_HOSTS", "8.209.71.240,47.254.130.145").split(",") if h.strip()]
CLOUD_PORT = int(os.getenv("CLOUD_PORT", "7006")); CLOUD_SNI = os.getenv("CLOUD_SNI", "mqtt.growatt.com")
CLOUD_VERIFY = os.getenv("CLOUD_VERIFY", "true").lower() == "true"
CONFIG_FILTER = os.getenv("CONFIG_FILTER", "true").lower() == "true"
STATE_FILE = os.getenv("STATE_FILE", "/state/cloud_forward")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("cloud-gate")

try:
    from grobro.grobro import parser as grobro_parser
except Exception:
    grobro_parser = None

state = {"enabled": False, "connections": 0, "cloud_connected": 0, "bytes_up": 0, "bytes_down": 0,
         "blocked_config_writes": 0, "cloud_host": None, "last_error": None, "since": time.strftime("%Y-%m-%d %H:%M:%S")}
active_conns = set()
loop = None
mq = None


# --------------------------------------------------------------------------- MQTT-Paketrahmen
def read_packet(buf: bytearray):
    """Gibt (packet_bytes, rest) zurück oder (None, buf), wenn noch unvollständig."""
    if len(buf) < 2:
        return None, buf
    mult, value, i = 1, 0, 1
    while True:
        if i >= len(buf):
            return None, buf
        b = buf[i]; value += (b & 0x7F) * mult; mult *= 128; i += 1
        if not (b & 0x80):
            break
        if i > 4:
            raise ValueError("bad remaining length")
    total = i + value
    if len(buf) < total:
        return None, buf
    return bytes(buf[:total]), buf[total:]


def encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128; n //= 128
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def publish_payload(pkt: bytes):
    """Topic und Payload eines PUBLISH-Pakets (MQTT 3.1.1 oder 5)."""
    ptype = pkt[0] >> 4; qos = (pkt[0] >> 1) & 3
    if ptype != 3:
        return None, None
    i = 1
    while pkt[i] & 0x80:
        i += 1
    i += 1
    tlen = struct.unpack_from(">H", pkt, i)[0]; i += 2
    topic = pkt[i:i + tlen].decode("utf-8", "replace"); i += tlen
    if qos:
        i += 2
    # MQTT 5 hat hier Properties (Varint-Länge) – wir kennen die Version nicht sicher, GroBro forwardet mit v3.1.1
    return topic, pkt[i:]


# Dongle-Parameter, die die Cloud nie schreiben darf: Netz/Broker (12,14,17,18,19,25,26), Passwort (7), Intervall (4),
# Neustart (32), IOT-Modul aus (35), WLAN (56,57). Alles andere (z. B. die Zähler-Kopplung) wird durchgelassen und protokolliert.
PROTECTED_PARAMS = {int(x) for x in os.getenv("PROTECTED_PARAMS", "4,7,12,14,17,18,19,25,26,32,35,56,57").split(",") if x.strip()}
DUMP_DIR = os.getenv("DUMP_DIR", "/dump/cloud_down")
LAB_TOPIC = os.getenv("LAB_TOPIC", "grolo/lab/log")


def decode_down(payload: bytes):
    """Cloud -> Gerät: (typ, beschreibung, blockieren?)"""
    if not grobro_parser:
        return None, "kein Parser", False
    try:
        u = grobro_parser.unscramble(payload)
        if len(u) < 8:
            return None, "zu kurz", False
        t = struct.unpack_from(">H", u, 6)[0]
        dev = u[8:24].rstrip(b"\x00").decode("ascii", "replace")
        if t == 0x0118 and len(u) >= 46:           # Dongle-Konfiguration schreiben: 14x00 | count | len | reg | vlen | value
            reg = struct.unpack_from(">H", u, 42)[0]; vlen = struct.unpack_from(">H", u, 44)[0]
            val = u[46:46 + vlen].decode("ascii", "replace")
            shown = "[ausgeblendet]" if reg in (7, 57) else repr(val)
            return t, f"Dongle-Parameter {reg} = {shown} ({dev})", reg in PROTECTED_PARAMS
        if t == 0x0110 and len(u) >= 42:           # Register schreiben (Gerät)
            start, count = struct.unpack_from(">HH", u, 38)
            return t, f"Register {start} (+{count}) = {u[42:-2].hex()} ({dev})", False
        if t == 0x0106 and len(u) >= 42:
            reg, val = struct.unpack_from(">HH", u, 38)
            return t, f"Einzelregister {reg} = {val} ({dev})", False
        return t, f"Typ 0x{t:04x}, {len(u)} Bytes, Nutzdaten {u[24:min(len(u), 90)].hex()} ({dev})", False
    except Exception as e:
        return None, f"nicht dekodierbar: {e}", False


def record_down(topic, payload, desc, blocked):
    """Jeden Cloud-Befehl an das Gerät als Datei ablegen und ins Live-Log melden."""
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}" + ("-blocked" if blocked else "") + ".bin"
        open(os.path.join(DUMP_DIR, name), "wb").write(payload)
    except Exception as e:
        LOG.debug("dump: %s", e)
    if mq is not None:
        line = {"ts": time.strftime("%H:%M:%S"), "level": "err" if blocked else "tx",
                "msg": f"CLOUD → Gerät {'VERWORFEN' if blocked else 'durchgelassen'}: {desc} [Topic {topic}]"}
        mq.publish(LAB_TOPIC, json.dumps(line, ensure_ascii=False))


# --------------------------------------------------------------------------- Verbindungen
async def pump(reader, writer, direction, conn_state):
    """Bytes durchreichen. Rückrichtung: Pakete lesen und Konfig-Schreibbefehle filtern."""
    buf = bytearray()
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            if direction == "up":
                state["bytes_up"] += len(data); writer.write(data); await writer.drain(); continue
            state["bytes_down"] += len(data); buf += data
            while True:
                pkt, buf = read_packet(buf)
                if pkt is None:
                    break
                if (pkt[0] >> 4) == 3:
                    topic, payload = publish_payload(pkt)
                    if payload is not None:
                        t, desc, blocked = decode_down(payload)
                        blocked = blocked and CONFIG_FILTER
                        record_down(topic, payload, desc, blocked)
                        if blocked:
                            state["blocked_config_writes"] += 1
                            LOG.warning("Cloud-Befehl verworfen: %s (Topic %s)", desc, topic)
                            publish_status(); continue
                        LOG.info("Cloud-Befehl durchgelassen: %s", desc)
                writer.write(pkt)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionError, ssl.SSLError):
        pass
    except Exception as e:
        LOG.debug("pump %s: %s", direction, e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def connect_cloud():
    ctx = ssl.create_default_context()
    if not CLOUD_VERIFY:
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    last = None
    for host in CLOUD_HOSTS:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(host, CLOUD_PORT, ssl=ctx, server_hostname=CLOUD_SNI), 10)
            state["cloud_host"] = host; state["last_error"] = None
            return r, w
        except Exception as e:
            last = e; LOG.warning("Cloud %s:%s nicht erreichbar: %s", host, CLOUD_PORT, e)
    state["last_error"] = str(last)
    raise last


async def handle_sink(reader, writer):
    """Stummer Broker: bestätigt, verwirft."""
    buf = bytearray()
    while True:
        data = await reader.read(65536)
        if not data:
            return
        buf += data
        while True:
            pkt, buf = read_packet(buf)
            if pkt is None:
                break
            ptype = pkt[0] >> 4
            if ptype == 1:      # CONNECT -> CONNACK (v3.1.1)
                writer.write(b"\x20\x02\x00\x00")
            elif ptype == 8:    # SUBSCRIBE -> SUBACK mit QoS 0 je Topic
                i = 1
                while pkt[i] & 0x80:
                    i += 1
                i += 1
                pid = pkt[i:i + 2]; body = pkt[i + 2:]
                n, j = 0, 0
                while j < len(body):
                    tl = struct.unpack_from(">H", body, j)[0]; j += 2 + tl + 1; n += 1
                writer.write(b"\x90" + encode_len(2 + n) + pid + b"\x00" * n)
            elif ptype == 3:    # PUBLISH -> PUBACK bei QoS1
                qos = (pkt[0] >> 1) & 3
                if qos == 1:
                    i = 1
                    while pkt[i] & 0x80:
                        i += 1
                    i += 1
                    tl = struct.unpack_from(">H", pkt, i)[0]; pid = pkt[i + 2 + tl:i + 4 + tl]
                    writer.write(b"\x40\x02" + pid)
            elif ptype == 12:   # PINGREQ -> PINGRESP
                writer.write(b"\xd0\x00")
            elif ptype == 14:   # DISCONNECT
                return
            elif ptype == 10:   # UNSUBSCRIBE -> UNSUBACK
                i = 1
                while pkt[i] & 0x80:
                    i += 1
                writer.write(b"\xb0\x02" + pkt[i + 1:i + 3])
        await writer.drain()


async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    state["connections"] += 1; active_conns.add(writer); publish_status()
    mode = "an" if state["enabled"] else "aus"
    LOG.info("GroBro verbunden von %s, Schalter %s", peer, mode)
    try:
        if state["enabled"]:
            cr, cw = await connect_cloud()
            state["cloud_connected"] += 1; publish_status()
            LOG.info("Cloud-Verbindung zu %s:%s steht (SNI %s)", state["cloud_host"], CLOUD_PORT, CLOUD_SNI)
            active_conns.add(cw)
            await asyncio.gather(pump(reader, cw, "up", None), pump(cr, writer, "down", None))
            active_conns.discard(cw); state["cloud_connected"] -= 1
        else:
            await handle_sink(reader, writer)
    except Exception as e:
        LOG.warning("Verbindung %s beendet: %s", peer, e)
    finally:
        state["connections"] -= 1; active_conns.discard(writer)
        try:
            writer.close()
        except Exception:
            pass
        publish_status()


def close_all():
    """Alle Verbindungen trennen, damit GroBro neu verbindet und den neuen Modus bekommt."""
    for w in list(active_conns):
        try:
            w.close()
        except Exception:
            pass
    active_conns.clear()


# --------------------------------------------------------------------------- Steuerung per MQTT
def publish_status():
    if mq is None:
        return
    mq.publish(f"{BASE}/switch/grobro/cloud_forward/get", "ON" if state["enabled"] else "OFF", retain=True)
    mq.publish(f"{BASE}/grobro/cloud_forward/status", json.dumps(state), retain=True)


def set_enabled(on: bool, source="mqtt"):
    if state["enabled"] == on:
        publish_status(); return
    state["enabled"] = on
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True); open(STATE_FILE, "w").write("ON" if on else "OFF")
    except Exception as e:
        LOG.warning("Zustand nicht speicherbar: %s", e)
    LOG.info("Cloud-Weiterleitung %s (%s)", "AN" if on else "AUS", source)
    if loop:
        loop.call_soon_threadsafe(close_all)
    publish_status()


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s", MQTT_HOST, MQTT_PORT)
    client.subscribe(f"{BASE}/switch/grobro/cloud_forward/set")
    publish_status()


def on_message(client, userdata, msg):
    v = msg.payload.decode(errors="replace").strip().upper()
    if v in ("ON", "1", "TRUE"):
        set_enabled(True)
    elif v in ("OFF", "0", "FALSE"):
        set_enabled(False)


def mqtt_thread():
    global mq
    mq = mqtt.Client(client_id="grobro-cloud-gate", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mq.on_connect = on_connect; mq.on_message = on_message
    while True:
        try:
            mq.connect(MQTT_HOST, MQTT_PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch", e); time.sleep(5)
    mq.loop_forever()


async def main():
    global loop
    loop = asyncio.get_running_loop()
    try:
        state["enabled"] = open(STATE_FILE).read().strip().upper() == "ON"
    except Exception:
        pass
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(CERT, KEY)
    server = await asyncio.start_server(handle_client, "0.0.0.0", LISTEN_PORT, ssl=ctx)
    LOG.info("cloud-gate lauscht auf :%s (TLS), Schalter %s, Cloud-Ziele %s, Konfig-Filter %s",
             LISTEN_PORT, "AN" if state["enabled"] else "AUS", CLOUD_HOSTS, CONFIG_FILTER)
    threading.Thread(target=mqtt_thread, daemon=True).start()

    async def status_loop():
        last = None
        while True:
            await asyncio.sleep(10)
            snap = json.dumps(state, sort_keys=True)
            if snap != last:
                publish_status(); last = snap

    asyncio.create_task(status_loop())
    async with server:
        await server.serve_forever()


asyncio.run(main())
