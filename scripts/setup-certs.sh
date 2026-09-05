#!/usr/bin/env bash
# Holt ein Let's-Encrypt-Zertifikat für <DUCKDNS_DOMAIN>.duckdns.org per DNS-01 (acme.sh)
# und legt cert/privkey/intermediate in mosquitto/certs/ ab. Danach baut build-chain.sh
# die komplette Kette inkl. Root.
#
# Warum so:
#  - Kein self-signed: die Dongle-Firmware traut nur eingebauten Public Roots.
#  - --server letsencrypt: acme.sh nimmt sonst ZeroSSL (USERTrust-Root, nicht in der Firmware-Liste).
#  - ec-256 + preferred-chain "ISRG Root X2": Kette endet bei ISRG Root X2 (2026: cert -> YE1 -> Root YE -> ISRG Root X2).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$ROOT_DIR/mosquitto/certs"
ENV_FILE="$ROOT_DIR/.env"

[[ -f "$ENV_FILE" ]] || { echo "Keine .env – bitte .env.example nach .env kopieren und ausfüllen."; exit 1; }
set -a; # shellcheck disable=SC1090
source "$ENV_FILE"; set +a

: "${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN fehlt in .env}"
: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN fehlt in .env}"
if [[ "$DUCKDNS_DOMAIN" == "meine-subdomain" || "$DUCKDNS_TOKEN" == 00000000-* ]]; then
  echo "Bitte echte Werte für DUCKDNS_DOMAIN / DUCKDNS_TOKEN in .env eintragen."; exit 1
fi
FQDN="${DUCKDNS_DOMAIN%.duckdns.org}.duckdns.org"

command -v acme.sh >/dev/null || { echo "acme.sh fehlt: brew install acme.sh"; exit 1; }
mkdir -p "$CERT_DIR"

export DuckDNS_Token="$DUCKDNS_TOKEN"
LE_ARGS=(--server letsencrypt)

if [[ -n "${ACME_EMAIL:-}" ]]; then
  acme.sh --register-account -m "$ACME_EMAIL" "${LE_ARGS[@]}" >/dev/null || true
fi

echo "==> Zertifikat für $FQDN ausstellen (DNS-01 via DuckDNS, dauert ca. 2-3 Minuten wegen DNS-Propagation) ..."
set +e
acme.sh --issue --dns dns_duckdns -d "$FQDN" \
  "${LE_ARGS[@]}" \
  --keylength ec-256 \
  --preferred-chain "ISRG Root X2" \
  --dnssleep 120
rc=$?
set -e
case $rc in
  0) echo "==> Zertifikat ausgestellt." ;;
  2) echo "==> Zertifikat existiert bereits und ist noch gültig – überspringe Ausstellung (acme.sh --renew --force -d $FQDN --ecc zum Erzwingen)." ;;
  *) echo "acme.sh --issue fehlgeschlagen (rc=$rc). Log: ~/.acme.sh/acme.sh.log"; exit $rc ;;
esac

echo "==> Zertifikat nach $CERT_DIR installieren (inkl. Reload-Hook für Renewals) ..."
acme.sh --install-cert -d "$FQDN" --ecc \
  --cert-file "$CERT_DIR/cert.pem" \
  --key-file  "$CERT_DIR/privkey.pem" \
  --ca-file   "$CERT_DIR/intermediate.pem" \
  --fullchain-file "$CERT_DIR/fullchain-le.pem" \
  --reloadcmd "$ROOT_DIR/scripts/build-chain.sh"
# --install-cert führt reloadcmd (build-chain.sh) direkt aus; bei Renewals erneut.

echo
echo "Fertig. Nächste Schritte:"
echo "  docker compose up -d"
echo "  scripts/verify.sh"
echo
echo "Renewal: launchd-Agent sh.acme.renew (täglich 03:17), prüfen mit:"
echo "  launchctl print gui/\$(id -u)/sh.acme.renew | grep state"
