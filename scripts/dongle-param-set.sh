#!/bin/sh
# Schreibt einen Konfigurationsparameter des Growatt-Dongles (0x0118) und liest ihn zur Kontrolle zurück.
# Aufruf: dongle-param-set.sh <Parameter-ID> <Wert> [Broker] [Seriennummer]
# ACHTUNG: Falsche Parameter können den Dongle unerreichbar machen. Gesperrt sind die Felder,
# die WLAN, Broker und IOT-Modul steuern (siehe SPERRE unten).
ID=$1; VAL=$2; HOST=${3:-$GROLO_BROKER}; DEV=${4:-$GROLO_SN}
[ -z "$HOST" ] && HOST=192.168.1.51
if [ -z "$ID" ] || [ -z "$VAL" ] || [ -z "$DEV" ]; then
  echo "Aufruf: dongle-param-set.sh <ID> <Wert> [Broker] [SN]"
  echo "        die Seriennummer kann auch aus \$GROLO_SN kommen"
  exit 2
fi
python3 - "$ID" "$VAL" "$HOST" "$DEV" <<'PY'
import struct, sys, subprocess, time
import os
SPERRE = {4, 7, 12, 14, 17, 18, 19, 25, 26, 32, 35, 56, 57}
# Bewusste Ausnahme, z. B. GROLO_UNLOCK=17 zum Umstellen des Brokers. Nur absichtlich setzen.
FREI = {int(x) for x in os.getenv("GROLO_UNLOCK", "").replace(" ", "").split(",") if x}
reg, val, host, dev = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
if reg in SPERRE and reg not in FREI:
    sys.exit(f"Parameter {reg} ist gesperrt (WLAN/Broker/IOT-Modul). Bewusst freigeben mit GROLO_UNLOCK={reg}.")
if reg in FREI:
    print(f"ACHTUNG: Parameter {reg} ist gesperrt und wurde per GROLO_UNLOCK freigegeben.")
mask = b"Growatt"
def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b
        for _ in range(8): c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c
def scr(m): return m[:8] + bytes(b ^ mask[i % 7] for i, b in enumerate(m[8:]))
def frame(msg_type, body):
    payload = b"\x00" * 14 + body
    msg = b"\x00\x01\x00\x07" + struct.pack(">HH", len(payload) + 18, msg_type) + dev.encode().ljust(16, b"\x00") + payload
    f = scr(msg)
    return f + struct.pack(">H", crc16(f))
def talk(send, want, timeout=10):
    sub = subprocess.Popen(["mosquitto_sub", "-h", host, "-t", f"c/33/{dev}", "-F", "%x", "-W", str(timeout)],
                           stdout=subprocess.PIPE, text=True)
    time.sleep(2)
    subprocess.run(["mosquitto_pub", "-h", host, "-t", f"s/33/{dev}", "-s"], input=send)
    for line in sub.stdout:
        u = scr(bytes.fromhex(line.strip()))
        if struct.unpack_from(">H", u, 6)[0] == want:
            sub.kill(); return u
    return None
v = val.encode("ascii")
body = struct.pack(">HH", 1, len(v) + 4) + struct.pack(">HH", reg, len(v)) + v
print(f"schreibe Parameter {reg} = {val}")
print("Quittung erhalten" if talk(frame(0x0118, body), 0x0118) else "KEINE Quittung")
u = talk(frame(0x0119, struct.pack(">HH", 1, reg)), 0x0119)
if u and struct.unpack_from(">H", u, 41)[0] == reg:
    n = struct.unpack_from(">H", u, 43)[0]
    got = u[45:45 + n].decode("ascii", "replace")
    print(f"zurückgelesen: Parameter {reg} = {got}")
    print("OK" if got == val else "ABWEICHUNG – Wert wurde nicht übernommen")
else:
    print("Rücklesen fehlgeschlagen")
PY
