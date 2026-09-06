#!/usr/bin/env bash
# Schritt-für-Schritt-Verifikation des Setups.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$ROOT_DIR/mosquitto/certs"
cd "$ROOT_DIR"
[[ -f .env ]] && { set -a; source .env; set +a; }
FQDN="${DUCKDNS_DOMAIN:-localhost}"; FQDN="${FQDN%.duckdns.org}.duckdns.org"
PLAIN_PORT="${MQTT_PLAIN_PORT:-1883}"; TLS_PORT="${MQTT_TLS_PORT:-7006}"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"

# Homebrew-OpenSSL 3 bevorzugen (macOS-LibreSSL kennt -verify_hostname / -ext nicht)
OPENSSL="${OPENSSL:-$(ls /opt/homebrew/opt/openssl@3/bin/openssl /usr/local/opt/openssl@3/bin/openssl 2>/dev/null | head -1)}"
OPENSSL="${OPENSSL:-openssl}"
IS_OSSL3=0; "$OPENSSL" version 2>/dev/null | grep -q '^OpenSSL 3' && IS_OSSL3=1

fail=0
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$*"; fail=1; }
info() { printf '  · %s\n' "$*"; }
step() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }
cn()   { "$OPENSSL" x509 -in "$1" -noout -subject | sed -E 's/.*CN ?= ?([^,\/]+).*/\1/'; }

step 1 "Zertifikatsdateien in mosquitto/certs"
[[ -f "$CERT_DIR/DEV-PLACEHOLDER.txt" ]] && bad "DEV-PLACEHOLDER.txt vorhanden: das ist noch die Wegwerf-Kette aus dem Pipeline-Test, kein Let's-Encrypt-Zertifikat. scripts/setup-certs.sh ausführen."
for f in cert.pem privkey.pem chain-full.pem root.pem; do
  [[ -s "$CERT_DIR/$f" ]] && ok "$f vorhanden" || bad "$f fehlt"
done
if [[ -s "$CERT_DIR/chain-full.pem" ]]; then
  n=$(grep -c 'BEGIN CERTIFICATE' "$CERT_DIR/chain-full.pem")
  [[ $n -ge 3 ]] && ok "chain-full.pem enthält $n Zertifikate (cert + Intermediate(s) + root)" || bad "chain-full.pem enthält $n Zertifikate, erwartet mindestens 3"
  info "Reihenfolge in chain-full.pem:"
  tmpd=$(mktemp -d)
  awk -v d="$tmpd" 'BEGIN{c=0} /BEGIN CERT/{c++; f=(d "/" c ".pem")} {print > f}' "$CERT_DIR/chain-full.pem"
  for i in $(seq 1 $n); do printf '      %d. CN=%s\n' $i "$(cn "$tmpd/$i.pem")"; done
  last_cn=$(cn "$tmpd/$n.pem"); last_iss=$("$OPENSSL" x509 -in "$tmpd/$n.pem" -noout -issuer | sed -E 's/.*CN ?= ?([^,\/]+).*/\1/')
  [[ "$last_cn" == "$last_iss" ]] && ok "letztes Glied '$last_cn' ist selbstsigniert (Root ist enthalten)" || bad "letztes Glied '$last_cn' ist kein Root (Issuer $last_iss)"
  rm -rf "$tmpd"
  if "$OPENSSL" verify -CAfile "$CERT_DIR/root.pem" -untrusted "$CERT_DIR/chain-full.pem" "$CERT_DIR/cert.pem" >/dev/null 2>&1; then
    ok "openssl verify: Kette bis Root gültig"; else bad "openssl verify fehlgeschlagen"; fi
  root_cn=$(cn "$CERT_DIR/root.pem")
  case "$root_cn" in "ISRG Root X1"|"ISRG Root X2") ok "Root '$root_cn' ist in der Growatt-Firmware-Liste" ;; *) bad "Root '$root_cn' ist NICHT in der Firmware-Liste (self-signed/Dev-Cert?)" ;; esac
  exp=$("$OPENSSL" x509 -in "$CERT_DIR/cert.pem" -noout -enddate | cut -d= -f2)
  "$OPENSSL" x509 -in "$CERT_DIR/cert.pem" -noout -checkend 604800 >/dev/null && ok "cert.pem gültig bis $exp" || bad "cert.pem läuft in <7 Tagen ab oder ist abgelaufen ($exp)"
  san=$("$OPENSSL" x509 -in "$CERT_DIR/cert.pem" -noout -text | grep -A1 'Subject Alternative Name' | tail -1 | sed 's/^ *//')
  echo "$san" | grep -q "DNS:$FQDN" && ok "SAN enthält $FQDN" || bad "SAN ($san) enthält nicht $FQDN"
fi

step 2 "Container"
for s in mosquitto grobro; do
  st=$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk -v s=$s '$1==s{print $2" "$3}')
  [[ "$st" == running* ]] && ok "$s: $st" || bad "$s: ${st:-nicht gestartet}"
done

step 3 "TLS-Listener $TLS_PORT: Handshake wie der Dongle (nur Root als Trust-Anchor + Hostname-Check)"
hn=(); [[ $IS_OSSL3 -eq 1 ]] && hn=(-verify_hostname "$FQDN")
out=$(echo Q | "$OPENSSL" s_client -connect 127.0.0.1:$TLS_PORT -servername "$FQDN" -CAfile "$CERT_DIR/root.pem" "${hn[@]}" -showcerts 2>&1)
sent=$(echo "$out" | grep -c 'BEGIN CERTIFICATE')
want=$(grep -c 'BEGIN CERTIFICATE' "$CERT_DIR/chain-full.pem" 2>/dev/null || echo 3)
[[ $sent -eq $want && $sent -ge 3 ]] && ok "Server sendet $sent Zertifikate (komplette Kette inkl. Root)" || bad "Server sendet $sent Zertifikate, erwartet $want"
if echo "$out" | grep -q 'Verify return code: 0 (ok)'; then
  [[ $IS_OSSL3 -eq 1 ]] && ok "Verify return code: 0 (ok), Hostname $FQDN geprüft" || ok "Verify return code: 0 (ok) (LibreSSL: ohne Hostname-Check)"
else bad "$(echo "$out" | grep 'Verify return code' || echo 'kein TLS-Handshake')"; fi
echo "$out" | grep -E '^ *[0-9]+ s:' | sed 's/^/      /'
echo "$out" | grep -E '^ *(Protocol|Cipher)  *:' | sed 's/^/      /' | head -2

step 4 "MQTT über TLS mit --cafile root.pem (mosquitto_pub, wie der Dongle: nur Root bekannt)"
if command -v mosquitto_pub >/dev/null; then
  if mosquitto_pub -h 127.0.0.1 -p $TLS_PORT --cafile "$CERT_DIR/root.pem" --insecure -t 'verify/tls' -m ping -u TESTSERIAL -P Growatt 2>/dev/null; then
    ok "TLS-Publish über 127.0.0.1 ok (Kette akzeptiert, Hostname-Check hier aus)"
  else bad "TLS-Publish fehlgeschlagen"; fi
  resolved=$(dig +short "$FQDN" A 2>/dev/null | tail -1)
  if [[ "$resolved" == "$LAN_IP" ]]; then
    if mosquitto_pub -h "$FQDN" -p $TLS_PORT --cafile "$CERT_DIR/root.pem" -t 'verify/tls' -m ping -u TESTSERIAL -P Growatt 2>/dev/null; then
      ok "Strikt: $FQDN -> $resolved, TLS-Publish inkl. Hostname-Check ok (lokaler DNS-Eintrag aktiv)"
    else bad "Strikt: $FQDN -> $resolved, TLS-Publish MIT Hostname-Check fehlgeschlagen"; fi
  else
    info "DNS-Override noch nicht aktiv: $FQDN -> '${resolved:-nichts}' (erwartet $LAN_IP). Strikter Test übersprungen."
  fi
else bad "mosquitto_pub fehlt (brew install mosquitto)"; fi

step 5 "Plain-Listener $PLAIN_PORT: mosquitto_sub empfängt, was auf TLS reinkommt"
if command -v mosquitto_sub >/dev/null; then
  tmp=$(mktemp)
  mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'verify/#' -C 1 -W 5 -v >"$tmp" 2>&1 &
  sleep 1
  mosquitto_pub -h 127.0.0.1 -p $TLS_PORT --cafile "$CERT_DIR/root.pem" --insecure -t 'verify/bridge' -m "hello-$(date +%s)" 2>/dev/null
  wait $! 2>/dev/null
  grep -q 'verify/bridge hello' "$tmp" && ok "empfangen: $(cat "$tmp")" || bad "nichts empfangen auf $PLAIN_PORT"
  rm -f "$tmp"
fi

step 6 "GroBro an beiden Listenern verbunden (mosquitto-Log)"
log=$(docker compose logs --no-log-prefix mosquitto 2>/dev/null | tail -500)
echo "$log" | grep -q 'New client connected from .* as grobro-grobro' && ok "grobro-grobro (Quelle, TLS $TLS_PORT)" || bad "grobro-grobro nicht verbunden"
echo "$log" | grep -q 'New client connected from .* as grobro-ha'     && ok "grobro-ha (Ziel, plain $PLAIN_PORT)"    || bad "grobro-ha nicht verbunden"
docker compose logs --no-log-prefix grobro 2>/dev/null | grep -iE 'error|traceback' | tail -3 | sed 's/^/      grobro: /'

step 7 "Erreichbarkeit über die LAN-IP des Hosts ($LAN_IP), dahin zeigt der lokale DNS-Eintrag"
for p in $TLS_PORT $PLAIN_PORT; do
  nc -z -G 2 "$LAN_IP" $p 2>/dev/null && ok "$LAN_IP:$p erreichbar" || bad "$LAN_IP:$p NICHT erreichbar (Port nur auf 127.0.0.1 gebunden?)"
done

step 8 "Dongle-Verbindungen (erscheinen erst, wenn der NEXA umkonfiguriert ist)"
dongle=$(echo "$log" | grep "on port $TLS_PORT" -A1 | grep 'New client connected' | grep -v 'grobro-\|TESTSERIAL\|auto-' | tail -3)
if [[ -n "$dongle" ]]; then ok "Dongle-Verbindung(en):"; echo "$dongle" | sed 's/^/      /'; else info "noch keine Dongle-Verbindung auf $TLS_PORT"; fi
echo "$log" | grep -iE 'OpenSSL Error|tlsv1 alert|certificate' | tail -3 | sed 's/^/      TLS-Fehler: /'
info "Live mitlesen:  mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t '#' -v"
info "Roh vom Dongle: mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'c/#' -v   (verschlüsselte Growatt-Frames)"
info "Dekodiert:      mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t '${HA_BASE_TOPIC:-homeassistant}/#' -v"

step 9 "Datenbank: Telegraf schreibt nach InfluxDB"
for s in influxdb telegraf grafana; do
  st=$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk -v s=$s '$1==s{print $2" "$3}')
  [[ "$st" == running* ]] && ok "$s: $st" || bad "$s: ${st:-nicht gestartet}"
done
last=$(docker compose exec -T influxdb influx query --org "${INFLUX_ORG:-growatt}" --token "${INFLUX_TOKEN:-}" 'from(bucket:"'"${INFLUX_BUCKET:-nexa}"'") |> range(start:-10m) |> filter(fn:(r)=> r._field=="totalBatteryPackSoc") |> last() |> keep(columns:["_time","_value"])' 2>/dev/null | awk 'NF>=2 && $1 ~ /^20/ {print $1" SoC="$2}' | tail -1)
[[ -n "$last" ]] && ok "letzter Datensatz in InfluxDB: $last" || bad "keine Daten der letzten 10 min in InfluxDB (docker compose logs telegraf)"

step 10 "Grafana: Dashboard und alle Panel-Abfragen"
gh=$(curl -s -m 5 http://127.0.0.1:${GRAFANA_PORT:-3000}/api/health | grep -o '"database": *"ok"')
[[ -n "$gh" ]] && ok "Grafana erreichbar: http://$LAN_IP:${GRAFANA_PORT:-3000}" || bad "Grafana antwortet nicht"
dsh=$(curl -s -m 5 -u "${GRAFANA_ADMIN_USER:-admin}:${GRAFANA_ADMIN_PASSWORD:-}" http://127.0.0.1:${GRAFANA_PORT:-3000}/api/datasources/uid/influx-nexa/health | grep -o '"status": *"OK"')
[[ -n "$dsh" ]] && ok "Datasource InfluxDB ok" || bad "Datasource InfluxDB fehlerhaft"
if ls grafana/dashboards/nexa-*.json >/dev/null 2>&1; then
  res=$(python3 scripts/test-panels.py 2>&1); cnt=$(echo "$res" | head -1); okn=${cnt% *}; badn=${cnt#* }
  [[ "$badn" == "0" ]] && ok "alle $okn Panel-Abfragen fehlerfrei" || { bad "$badn Panel-Abfragen fehlerhaft:"; echo "$res" | tail -n +2 | sed 's/^/      /'; }
fi

step 11 "Einstellungsseite: Websocket-Listener und Seite"
st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="settings"{print $2}')
[[ "$st" == running* ]] && ok "settings: $st" || bad "settings: ${st:-nicht gestartet}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:${SETTINGS_PORT:-8080}/)
[[ "$code" == "200" ]] && ok "Seite erreichbar: http://$LAN_IP:${SETTINGS_PORT:-8080}" || bad "Seite antwortet mit HTTP $code"
nc -z -G 2 "$LAN_IP" ${MQTT_WS_PORT:-9001} 2>/dev/null && ok "Websocket-Port ${MQTT_WS_PORT:-9001} offen" || bad "Websocket-Port ${MQTT_WS_PORT:-9001} nicht erreichbar"
disc=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/device/+/config' --retained-only -C 1 -W 3 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(sum(1 for c in d["cmps"].values() if "command_topic" in c))' 2>/dev/null)
[[ -n "$disc" && "$disc" -gt 0 ]] && ok "Discovery retained, $disc schreibbare Einstellungen" || bad "keine retained Discovery mit Kommandos (GroBro neu starten)"

step 12 "Hardware-Infos: Firmware-Register und Dongle-Sidecar"
st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="dongle-info"{print $2}')
[[ "$st" == running* ]] && ok "dongle-info: $st" || bad "dongle-info: ${st:-nicht gestartet}"
dg=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/grobro/+/dongle' --retained-only -C 1 -W 3 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("model_id"),d.get("sw_version"),d.get("wifi_signal"))' 2>/dev/null)
[[ -n "$dg" ]] && ok "Dongle-Info retained: Modell/SW/WLAN = $dg" || bad "keine Dongle-Info (kommt erst nach Reconnect oder Neustart des Dongles)"
fw=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/grobro/+/state' -C 1 -W 20 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);p=[d.get(f"fw_version_part_{i}") for i in range(1,5)];print(".".join(str(int(x)) for x in p) if all(x is not None for x in p) else "")' 2>/dev/null)
[[ -n "$fw" ]] && ok "Firmware-Register im State: $fw" || bad "Firmware-Register fehlen im State (Register-Map-Mount prüfen)"

step 13 "Rohregister-Sidecar (Forschung)"
st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="raw-registers"{print $2}')
[[ "$st" == running* ]] && ok "raw-registers: $st" || bad "raw-registers: ${st:-nicht gestartet}"
ri=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/grobro/+/raw_input' -C 1 -W 20 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["count"],len(d["registers"]))' 2>/dev/null)
[[ -n "$ri" ]] && ok "raw_input: ${ri% *} Register gelesen, ${ri#* } unbekannte veröffentlicht" || bad "kein raw_input innerhalb 20 s"

step 14 "Cloud-Weiterleitung (cloud-gate)"
st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="cloud-gate"{print $2}')
[[ "$st" == running* ]] && ok "cloud-gate: $st" || bad "cloud-gate: ${st:-nicht gestartet}"
cs=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/grobro/cloud_forward/status' --retained-only -C 1 -W 3 2>/dev/null)
if [[ -n "$cs" ]]; then
  en=$(echo "$cs" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("AN" if d["enabled"] else "AUS", d["connections"], d["cloud_connected"], d["blocked_config_writes"], d.get("last_error") or "")')
  set -- $en; ok "Schalter $1, GroBro-Verbindungen $2, Cloud-Verbindungen $3, verworfene Konfig-Befehle $4"
  [[ "$1" == "AN" && "$3" == "0" ]] && bad "Schalter AN, aber keine Cloud-Verbindung${5:+ ($5)}"
else bad "kein Status von cloud-gate"; fi
docker compose logs --no-log-prefix --since 10m grobro 2>/dev/null | grep -q "Forwarding to Growatt Cloud failed" && bad "GroBro meldet Forwarding-Fehler (docker compose logs grobro)" || ok "keine Forwarding-Fehler bei GroBro (10 min)"

step 15 "Wetter (Open-Meteo)"
st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$1=="weather"{print $2}')
[[ "$st" == running* ]] && ok "weather: $st" || bad "weather: ${st:-nicht gestartet}"
wc=$(mosquitto_sub -h 127.0.0.1 -p $PLAIN_PORT -t 'homeassistant/grolo/weather/current' --retained-only -C 1 -W 3 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["condition_de"], d["temperature"], "°C, Strahlung", d["shortwave_radiation"], "W/m², Sonne", d["sunrise"], "-", d["sunset"])' 2>/dev/null)
[[ -n "$wc" ]] && ok "aktuelles Wetter: $wc" || bad "kein retained Wetter (WEATHER_LAT/LON gesetzt? docker compose logs weather)"
wf=$(docker compose exec -T influxdb influx query --org "${INFLUX_ORG:-growatt}" --token "${INFLUX_TOKEN:-}" 'from(bucket:"'"${INFLUX_BUCKET:-nexa}"'") |> range(start:-1h, stop: 48h) |> filter(fn:(r)=> r._measurement=="weather_forecast" and r._field=="shortwave_radiation") |> count() |> group() |> sum()' 2>/dev/null | grep -E "^\s+[0-9]+" | awk '{print $1}')
[[ -n "$wf" && "$wf" -gt 0 ]] && ok "Vorhersage in InfluxDB: $wf Stunden" || bad "keine Vorhersagedaten (Measurement weather_forecast)"

echo
[[ $fail -eq 0 ]] && printf '\033[32mAlle Checks bestanden.\033[0m\n' || printf '\033[31mMindestens ein Check fehlgeschlagen.\033[0m\n'
exit $fail
