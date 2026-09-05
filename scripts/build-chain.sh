#!/usr/bin/env bash
# Baut mosquitto/certs/chain-full.pem = Server-Cert + alle Intermediates + Root (in dieser Reihenfolge)
# und prüft die Kette. Der Root muss mit rein: die Growatt-Firmware prüft die komplette Kette streng.
#
# Quelle ist fullchain-le.pem von acme.sh (Cert + Intermediates, ohne Root). Daraus wird der Issuer
# des letzten Glieds gelesen und der passende Let's-Encrypt-Root (ISRG Root X1 oder X2) angehängt.
# Beispielketten:
#   2024/25: cert -> E6 -> ISRG Root X2                       (3 Zertifikate)
#   2026:    cert -> YE1 -> Root YE (cross von X2) -> ISRG Root X2  (4 Zertifikate)
# Wird von setup-certs.sh aufgerufen und von acme.sh als --reloadcmd bei Renewals.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$ROOT_DIR/mosquitto/certs"
cd "$CERT_DIR"

# Homebrew-OpenSSL 3 bevorzugen (macOS-LibreSSL kennt einige Optionen nicht)
OPENSSL="$(ls /opt/homebrew/opt/openssl@3/bin/openssl /usr/local/opt/openssl@3/bin/openssl 2>/dev/null | head -1 || true)"
OPENSSL="${OPENSSL:-openssl}"
cn() { "$OPENSSL" x509 -in "$1" -noout -subject | sed -E 's/.*CN ?= ?([^,\/]+).*/\1/'; }
issuer_cn() { "$OPENSSL" x509 -in "$1" -noout -issuer | sed -E 's/.*CN ?= ?([^,\/]+).*/\1/'; }

for f in cert.pem privkey.pem fullchain-le.pem; do
  [[ -s "$f" ]] || { echo "FEHLT: $CERT_DIR/$f (erst scripts/setup-certs.sh laufen lassen)"; exit 1; }
done

# --- fullchain-le.pem in Einzelzertifikate zerlegen ---
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
awk -v d="$tmp" '/BEGIN CERT/{c++; f=(d "/" c ".pem")} {print > f}' fullchain-le.pem
n=$(ls "$tmp"/*.pem | wc -l | tr -d ' ')
[[ $n -ge 2 ]] || { echo "fullchain-le.pem enthält nur $n Zertifikat(e), erwartet Cert + Intermediate(s)"; exit 1; }

echo "Kette von acme.sh ($n Zertifikate):"
for i in $(seq 1 "$n"); do printf '  %d. CN=%s  (Issuer: %s)\n' "$i" "$(cn "$tmp/$i.pem")" "$(issuer_cn "$tmp/$i.pem")"; done

# Cert in fullchain muss zu cert.pem passen
[[ "$("$OPENSSL" x509 -in cert.pem -noout -fingerprint -sha256)" == "$("$OPENSSL" x509 -in "$tmp/1.pem" -noout -fingerprint -sha256)" ]] \
  || { echo "cert.pem != erstes Zertifikat in fullchain-le.pem"; exit 1; }

# --- Root anhand des Issuers des letzten Glieds bestimmen ---
last_issuer="$(issuer_cn "$tmp/$n.pem")"
case "$last_issuer" in
  "ISRG Root X2") root_url="https://letsencrypt.org/certs/isrg-root-x2.pem" ;;
  "ISRG Root X1") root_url="https://letsencrypt.org/certs/isrgrootx1.pem" ;;
  *) echo "Letztes Kettenglied ist von '$last_issuer' signiert – kein ISRG Root X1/X2. Nicht in der Growatt-Firmware-Liste. Abbruch."; exit 1 ;;
esac
curl -fsSL -o root.pem "$root_url"
[[ "$(cn root.pem)" == "$last_issuer" ]] || { echo "Root-CN '$(cn root.pem)' passt nicht zu '$last_issuer'"; exit 1; }
# Root muss selbstsigniert sein (sonst wäre es kein Root)
[[ "$(issuer_cn root.pem)" == "$(cn root.pem)" ]] || { echo "root.pem ist nicht selbstsigniert"; exit 1; }

# --- Intermediates einzeln ablegen (z. B. e6.pem, ye1.pem, root-ye.pem) ---
rm -f intermediate-*.pem
for i in $(seq 2 "$n"); do
  name="$(cn "$tmp/$i.pem" | tr '[:upper:] ' '[:lower:]-')"
  cp "$tmp/$i.pem" "intermediate-$((i-1))-${name}.pem"
done
cp "$tmp/2.pem" intermediate.pem   # Kompatibilität: erster Intermediate

# --- Kette zusammensetzen: cert + intermediates + root ---
cat fullchain-le.pem root.pem > chain-full.pem
total=$(grep -c 'BEGIN CERTIFICATE' chain-full.pem)
echo "chain-full.pem = cert + $((n-1)) Intermediate(s) + root  ($total Zertifikate), Root: $(cn root.pem)"

# --- Prüfen ---
"$OPENSSL" verify -CAfile root.pem -untrusted fullchain-le.pem cert.pem
pk_cert="$("$OPENSSL" x509 -in cert.pem -noout -pubkey | "$OPENSSL" sha256)"
pk_key="$("$OPENSSL" pkey -in privkey.pem -pubout | "$OPENSSL" sha256)"
[[ "$pk_cert" == "$pk_key" ]] || { echo "privkey.pem passt nicht zu cert.pem!"; exit 1; }
echo "Key passt zum Zertifikat."
"$OPENSSL" x509 -in cert.pem -noout -subject -enddate | sed 's/^/  /'
"$OPENSSL" x509 -in cert.pem -noout -text | grep -A1 "Subject Alternative Name" | tail -1 | sed 's/^ */  SAN: /'

# Der Mosquitto-Container läuft als uid 1883 und liest den Bind-Mount vom Host.
# Ohne Leserecht für "other" kann er den Key nicht öffnen (Docker-Desktop- und Colima-Mounts mappen keine uids).
chmod 644 ./*.pem
rm -f DEV-PLACEHOLDER.txt   # Wegwerf-Kette aus dem Pipeline-Test ist damit Geschichte

# --- Mosquitto neu starten, falls der Stack läuft ---
if docker compose -f "$ROOT_DIR/docker-compose.yml" ps --status running --services 2>/dev/null | grep -q '^mosquitto$'; then
  echo "Starte mosquitto neu, damit das neue Zertifikat greift ..."
  docker compose -f "$ROOT_DIR/docker-compose.yml" restart mosquitto
fi
echo "OK."
