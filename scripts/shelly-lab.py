#!/usr/bin/env python3
"""Shelly-Labor: vorsichtige Lese-/Schreibversuche an den Konfigurationsparametern des Growatt-Dongles,
mit Live-Log über MQTT (grolo/lab/log, Seite settings-ui/shelly-lab.html).

Läuft im GroBro-Image (PYTHONPATH=/app):  python3 shelly-lab.py read 102 122
                                          python3 shelly-lab.py write 102 "DEV:"
                                          python3 shelly-lab.py watch 120        (nur zuhören)
Schreibt NIE auf die Sperrliste (WLAN, Broker, IP-Felder, Felder mit Wert ungleich 0).
"""
import json, struct, sys, time, threading
import paho.mqtt.client as mqtt
from grobro.grobro import parser
from grobro.grobro.builder import append_crc

DEV = "0HVRE0ED25DT0003"; HOST = "mosquitto"
BLOCKED = {7, 8, 12, 14, 16, 17, 18, 19, 20, 21, 22, 25, 26, 30, 31, 32, 35, 53, 54, 56, 57, 105, 106, 107, 108, 109, 139, 140}  # 32 Neustart, 35 schaltet das IOT-Modul ab (WLAN weg!)
MASK = b"Growatt"
c = mqtt.Client(client_id="grolo-lab", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
lock = threading.Lock(); events = []

def log(level, msg):
    line = {"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
    print(f"{line['ts']} [{level}] {msg}", flush=True); c.publish("grolo/lab/log", json.dumps(line, ensure_ascii=False))

def scramble(m): return m[:8] + bytes(b ^ MASK[i % 7] for i, b in enumerate(m[8:]))
def frame(msg_type, payload):
    msg = b"\x00\x01\x00\x07" + struct.pack(">HH", len(payload) + 18, msg_type) + DEV.encode().ljust(16, b"\x00") + payload
    return append_crc(scramble(msg))
def read_frame(reg): return frame(0x0119, b"\x00" * 14 + struct.pack(">HH", 1, reg))
def write_frame(reg, value):
    v = value.encode("ascii"); return frame(0x0118, b"\x00" * 14 + struct.pack(">HH", 1, len(v) + 4) + struct.pack(">HH", reg, len(v)) + v)

def on_msg(cl, u, m):
    try:
        d = parser.unscramble(m.payload); t = struct.unpack_from(">H", d, 6)[0]
    except Exception:
        return
    if t == 0x0119:                      # Lese-Antwort: 38 count(2) | 40 status(1) | 41 id(2) | 43 len(2) | 45 wert
        reg = struct.unpack_from(">H", d, 41)[0]; n = struct.unpack_from(">H", d, 43)[0]; val = d[45:45 + n].decode("ascii", "replace")
        with lock: events.append(("read", reg, val))
        shown = "[ausgeblendet]" if reg in (7, 57) else val
        log("info", f"← Antwort lesen  Parameter {reg} = {shown!r}  (Länge {n}, Roh ab Byte 38: {d[38:48].hex()})")
    elif t == 0x0118:                    # Schreib-Quittung
        with lock: events.append(("ack", d, None))
        log("ok", f"← Quittung schreiben, {len(d)} Bytes, Nutzdaten ab Byte 38: {d[38:-2].hex()}")
    elif t == 0x6F64:
        sm = parser.parse_noah_6f64(d); log("meter", f"← ZÄHLERNACHRICHT 0x6F64 von {sm['device_id']}, Zähler-SN {sm['smart_meter_sn']!r}: {sm['data'][:300]}")
    elif t not in (0x0104, 0x0105, 0x0106, 0x0116, 0x0150, 0xFE25):
        log("warn", f"← unbekannter Nachrichtentyp 0x{t:04x}, {len(d)} Bytes")

def wait_for(kind, reg=None, timeout=6):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with lock:
            for e in events:
                if e[0] == kind and (reg is None or e[1] == reg):
                    events.remove(e); return e
        time.sleep(0.1)
    return None

def do_read(reg):
    log("tx", f"→ lese Parameter {reg}"); c.publish(f"s/33/{DEV}", read_frame(reg))
    e = wait_for("read", reg); 
    if not e: log("warn", f"   keine Antwort auf lesen {reg}")
    return e[2] if e else None

def do_write(reg, value):
    if reg in BLOCKED: log("err", f"Parameter {reg} steht auf der Sperrliste, schreibe nicht"); return None
    before = do_read(reg)
    log("tx", f"→ SCHREIBE Parameter {reg} = {value!r}  (vorher {before!r})"); c.publish(f"s/33/{DEV}", write_frame(reg, value))
    ack = wait_for("ack")
    if not ack: log("warn", "   keine Quittung innerhalb 6 s")
    time.sleep(1); after = do_read(reg)
    log("ok" if after == value else "warn", f"   Ergebnis: Parameter {reg} ist jetzt {after!r} ({'übernommen' if after == value else 'NICHT übernommen'})")
    return after

c.on_message = on_msg; c.connect(HOST, 1883, 60); c.subscribe([("c/33/#", 0)]); c.loop_start(); time.sleep(1)
cmd = sys.argv[1] if len(sys.argv) > 1 else "watch"
if cmd == "read":
    for r in sys.argv[2:]: do_read(int(r)); time.sleep(0.5)
elif cmd == "write":
    do_write(int(sys.argv[2]), sys.argv[3])
elif cmd == "probe":
    # Kandidatenfelder einzeln auf 1 setzen, beobachten, auf 0 zurücksetzen. Stoppt, wenn der Dongle nicht mehr antwortet.
    dwell = int(sys.argv[2]); ids = [int(x) for x in sys.argv[3:]]
    log("info", f"Sondierung: {ids}, je {dwell} s Beobachtung")
    for reg in ids:
        cur = do_read(reg)
        if cur is None: log("err", f"Dongle antwortet nicht auf Lesen von {reg}, ABBRUCH"); break
        if cur != "0": log("warn", f"Parameter {reg} steht auf {cur!r}, nicht 0, überspringe"); continue
        after = do_write(reg, "1")
        if after is None: log("err", "Dongle antwortet nicht mehr, ABBRUCH"); break
        if after != "1": log("info", f"Parameter {reg} nimmt keinen Wert an (Statusfeld), weiter"); time.sleep(1); continue
        log("info", f"beobachte {dwell} s (mDNS, Zählernachricht, Statusfelder 102/122) ...")
        t0 = time.time(); last = None
        while time.time() - t0 < dwell:
            time.sleep(15); s102 = do_read(102); s122 = do_read(122)
            if (s102, s122) != ("DEV:", "DEV:"): log("meter", f"STATUSFELD GEÄNDERT: 102={s102!r} 122={s122!r}")
            if s102 is None and s122 is None: log("err", "Dongle antwortet nicht mehr während der Beobachtung"); break
        back = do_write(reg, "0")
        if back != "0": log("err", f"Parameter {reg} konnte NICHT auf 0 zurückgesetzt werden (ist {back!r}), ABBRUCH"); break
        log("ok", f"Parameter {reg}: keine Reaktion, zurückgesetzt")
    log("info", "Sondierungsblock beendet")
elif cmd == "trigger":
    # Einen Parameter setzen, einen Neustart/Verbindungsabbruch tolerieren, lange beobachten, dann zuverlässig zurücksetzen.
    reg, value, restore, dwell = int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5])
    if reg in BLOCKED: log("err", "Sperrliste"); sys.exit(1)
    log("tx", f"→ SCHREIBE Parameter {reg} = {value!r} (Trigger-Test, Rücksetzung auf {restore!r} nach {dwell} s)"); c.publish(f"s/33/{DEV}", write_frame(reg, value)); wait_for("ack")
    t0 = time.time(); gone = False; back_at = None
    while time.time() - t0 < dwell:
        time.sleep(10); v = do_read(reg)
        if v is None:
            if not gone: log("warn", "Dongle antwortet nicht (Neustart/Verbindungsabbruch?)"); gone = True
        else:
            if gone and back_at is None: back_at = time.time(); log("ok", f"Dongle wieder da nach {back_at - t0:.0f} s, Parameter {reg} = {v!r}")
            s102 = do_read(102); s122 = do_read(122)
            if (s102, s122) != ("DEV:", "DEV:"): log("meter", f"STATUSFELD GEÄNDERT: 102={s102!r} 122={s122!r}")
    for i in range(20):
        c.publish(f"s/33/{DEV}", write_frame(reg, restore)); wait_for("ack"); time.sleep(1)
        if do_read(reg) == restore: log("ok", f"Parameter {reg} zurück auf {restore!r}"); break
        log("warn", f"Rücksetzen Versuch {i + 1} noch nicht bestätigt, warte 10 s"); time.sleep(10)
    else:
        log("err", f"Parameter {reg} konnte nicht zurückgesetzt werden!")
elif cmd == "watch":
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60; log("info", f"höre {secs} s auf c/33 (Zählernachrichten, Quittungen)"); time.sleep(secs)
time.sleep(1); c.loop_stop()
