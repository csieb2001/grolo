#!/bin/sh
# Liest einen Konfigurationsparameter des Growatt-Dongles im Klartext.
# Aufruf: dongle-param.sh <Parameter-ID> [Broker] [Seriennummer]
ID=${1:-57}; HOST=${2:-192.168.1.51}; DEV=${3:-0HVRE0ED25DT0003}
python3 - "$ID" "$HOST" "$DEV" <<'PY'
import struct, sys, subprocess, threading, time
reg, host, dev = int(sys.argv[1]), sys.argv[2], sys.argv[3]
mask = b"Growatt"
def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b
        for _ in range(8): c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c
def scr(m): return m[:8] + bytes(b ^ mask[i % 7] for i, b in enumerate(m[8:]))
payload = b"\x00" * 14 + struct.pack(">HH", 1, reg)
msg = b"\x00\x01\x00\x07" + struct.pack(">HH", len(payload) + 18, 0x0119) + dev.encode().ljust(16, b"\x00") + payload
frame = scr(msg); frame += struct.pack(">H", crc16(frame))
sub = subprocess.Popen(["mosquitto_sub", "-h", host, "-t", f"c/33/{dev}", "-F", "%x", "-W", "8"], stdout=subprocess.PIPE, text=True)
time.sleep(2)
subprocess.run(["mosquitto_pub", "-h", host, "-t", f"s/33/{dev}", "-s"], input=frame)
for line in sub.stdout:
    u = scr(bytes.fromhex(line.strip()))
    if u[6:8] == b"\x01\x19" and struct.unpack_from(">H", u, 41)[0] == reg:
        n = struct.unpack_from(">H", u, 43)[0]
        print(f"Parameter {reg} = {u[45:45+n].decode('ascii', 'replace')}"); sub.kill(); break
else:
    print("keine Antwort")
PY
