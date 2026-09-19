#!/usr/bin/env bash
# wolf-config: fragt den WOLF Link nach den Busteilnehmern der Anlage und schreibt wolf/parameter.json.
#
# Der Link spricht auf Port 9092 (TLS) das ISM7-Protokoll. ism7config meldet sich dort mit dem Gerätepasswort
# an (dasselbe wie für die Konfig-Website http://<ip>/) und listet auf, welche Geräte am eBus hängen und
# welche Parameter jedes davon kennt. Danach baut scripts/wolf-catalog.py daraus den Katalog mit Namen,
# Einheiten, Grenzwerten und Menüzuordnung.
#
# Aufruf:  scripts/wolf-config.sh            (nimmt WOLF_HOST und WOLF_PASSWORD aus .env)
#          scripts/wolf-config.sh 192.168.1.57 geheim
#
# Hinweis: Der Link nimmt nur eine lokale Verbindung an. Läuft der Dienst "wolf" schon, muss er für die
# Dauer der Abfrage gestoppt werden – das Skript macht das selbst und startet ihn danach wieder.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:-}"; PASS="${2:-}"
if [[ -z "$HOST" || -z "$PASS" ]] && [[ -f .env ]]; then
  HOST="${HOST:-$(grep -E '^WOLF_HOST=' .env | cut -d= -f2- || true)}"
  PASS="${PASS:-$(grep -E '^WOLF_PASSWORD=' .env | cut -d= -f2- || true)}"
fi
if [[ -z "$HOST" || -z "$PASS" ]]; then
  echo "WOLF_HOST und WOLF_PASSWORD fehlen (in .env eintragen oder als Argumente übergeben)." >&2
  exit 1
fi

RESTART=0
if docker compose ps --services --status running 2>/dev/null | grep -qx wolf; then
  echo "Dienst wolf anhalten – der Link erlaubt nur eine lokale Verbindung"
  docker compose stop wolf >/dev/null
  RESTART=1
fi
cleanup() { [[ $RESTART -eq 1 ]] && docker compose start wolf >/dev/null && echo "Dienst wolf wieder gestartet"; }
trap cleanup EXIT

mkdir -p wolf
echo "Frage $HOST ab …"
docker run --rm -v "$PWD/wolf:/out" -w /out --entrypoint /app/ism7config \
  zivillian/ism7mqtt:latest -i "$HOST" -p "$PASS" 2>&1 | grep -viE '^\S+\|(TRACE|DEBUG)\|' || true

if [[ ! -s wolf/parameter.json ]]; then
  echo "wolf/parameter.json wurde nicht geschrieben – stimmen IP und Passwort?" >&2
  exit 1
fi
python3 - <<'PY'
import json
d = json.load(open("wolf/parameter.json"))
devs = d.get("Devices", [])
print(f"\nwolf/parameter.json: Port {d.get('TcpPort')}, {len(devs)} Busteilnehmer, "
      f"{sum(len(x['Parameter']) for x in devs)} Parameter")
for x in devs:
    print(f"  Vorlage {x['DeviceTemplateId']:>7}  Bus {x['ReadBusAddress']:>5}  {len(x['Parameter']):>3} Parameter")
PY

echo
echo "Jetzt den Katalog bauen:  scripts/wolf-catalog.py"
