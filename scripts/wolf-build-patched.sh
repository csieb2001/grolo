#!/usr/bin/env bash
# Baut ism7mqtt mit dem Fix für WOLF-Link-Firmware 5.20.6 und legt ihn als Abbild ism7mqtt:fw5 ab.
#
# Warum überhaupt ein eigener Bau:
# Firmware 5.20.6 hört auf zu antworten – Keepalives eingeschlossen – wenn ism7mqtt beim Start alle
# Push-Abos am Stück schickt. Etwa 90 s später kappt der Link die Verbindung, ism7mqtt endet mit
# Exit-Code 0 und Docker startet neu: ein Zyklus von rund 133 s, hunderte Neustarts am Tag. Gemessen
# am 25.09.2026: 20/40/60 Parameter (1–3 Push-Pakete) laufen durch, 100 (5 Pakete) sterben nach 96 s,
# die vollen 329 nach ~133 s.
#
# Der Fix stammt aus zivillian/ism7mqtt PR #239 ("Send push subscribes one at a time"): der Client
# wartet die Antwort auf jedes Push-Abo ab, bevor er das nächste schickt. Neun Zeilen im Client.
# Der PR ist offen und in keinem Release – 0.0.22 ist das neueste. Deshalb der Fork.
#
# Gemessen am 26.09.2026 mit dem vollständigen Parametersatz (329 Parameter): 401 s ohne jede
# Fehlermeldung, abgebrochen haben wir. Mit dem offiziellen Abbild an derselben Stelle: ~133 s.
#
# Sobald der PR in einem Release von zivillian/ism7mqtt steckt, ist dieses Skript überflüssig:
# in docker-compose.yml beim Dienst "wolf" wieder zivillian/ism7mqtt:latest eintragen und das
# Abbild hier löschen.
#
# Aufruf:  scripts/wolf-build-patched.sh            (baut lokal)
#          ssh root@192.168.1.51 'bash -s' < scripts/wolf-build-patched.sh   (baut im LXC)
set -euo pipefail

REPO="${ISM7_FORK_REPO:-https://github.com/lukasleitsch/ism7mqtt.git}"
BRANCH="${ISM7_FORK_BRANCH:-fix/fw5-serial-push-subscribes}"
# Festgenagelt, damit ein späterer Bau dasselbe ergibt. Zum Nachziehen auf einen neueren Stand des
# PR hier den neuen Commit eintragen (und danach messen, nicht hoffen).
COMMIT="${ISM7_FORK_COMMIT:-282cec584f73268484fedb0047cb9fbaa9ba9002}"
TAG="${ISM7_IMAGE_TAG:-ism7mqtt:fw5}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "→ hole $BRANCH aus $REPO"
git clone --quiet --branch "$BRANCH" "$REPO" "$WORK/src"
git -C "$WORK/src" checkout --quiet "$COMMIT"
echo "  Commit $(git -C "$WORK/src" rev-parse --short HEAD)"

echo "→ baue $TAG"
docker build -t "$TAG" --build-arg VERSION=0.0.22-fw5 "$WORK/src"

echo "✓ $TAG liegt bereit. Einsetzen:  docker compose up -d --no-deps wolf"
