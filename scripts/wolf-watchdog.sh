#!/usr/bin/env bash
# Wachhund für ism7mqtt (Dienst wolf). ism7mqtt kann hängen, ohne abzustürzen: Container läuft, kein Fehler im Log,
# aber es kommt kein einziger Wert mehr (28.09.–04.10.2026 und 18.10.2026, dort über eine Stunde). Erkennbar ist
# das nur am Alter der Wärmepumpenwerte, das die wolf-bridge auf <BASE>/grolo/wolf/status veröffentlicht.
#
# Regel: Wärmepumpe älter als MAX_AGE Sekunden → `docker compose restart wolf`, höchstens einmal je COOLDOWN.
# Der Cooldown ist Absicht: ist der WOLF Link selbst weg, soll er nicht im Minutentakt neue Anmeldungen bekommen.
# Läuft per cron im LXC (/etc/cron.d/grolo-wolf-watchdog, alle 5 min).
set -euo pipefail
cd /opt/growatt
MAX_AGE="${WOLF_MAX_AGE:-600}"; COOLDOWN="${WOLF_COOLDOWN:-900}"
STAMP=/var/tmp/grolo-wolf-watchdog.last
BASE="$(grep -E '^HA_BASE_TOPIC=' .env 2>/dev/null | cut -d= -f2 || true)"; BASE="${BASE:-homeassistant}"
docker ps --format '{{.Names}}' | grep -qx grolo-wolf || exit 0          # Profil wolf nicht aktiv
age=$(timeout 5 docker exec grolo-mosquitto mosquitto_sub -t "$BASE/grolo/wolf/status" -C 1 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((x["age"] for x in d["devices"] if x["id"]=="heatpump"), "") or "")' || true)
[ -z "$age" ] && exit 0
[ "$age" -le "$MAX_AGE" ] && exit 0
now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
[ $((now - last)) -lt "$COOLDOWN" ] && exit 0
echo "$now" > "$STAMP"
logger -t grolo-wolf-watchdog "Wärmepumpe seit ${age}s ohne Werte – starte ism7mqtt neu"
docker compose restart wolf >/dev/null 2>&1
