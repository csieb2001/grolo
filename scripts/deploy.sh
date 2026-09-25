#!/usr/bin/env bash
# Stack und Website in den LXC ausliefern.
#
# Die .env bleibt ausgenommen: sie trägt die Produktivwerte (Postgres-Passwort, Tunnel-Token, WEB_URL auf den
# Container) und ist auf dem Mac regelmäßig älter. Ein rsync ohne diese Ausnahme hat am 25.09.2026 zweimal
# WEB_URL auf die abgeschaltete Vercel-Adresse zurückgesetzt und PGPASSWORD/CF_TUNNEL_TOKEN gelöscht — beides
# fällt erst auf, wenn der nächste `compose up -d` einen Container neu erzeugt.
set -euo pipefail

HOST="${GROLO_HOST:-root@192.168.1.51}"
SRC_STACK="$(cd "$(dirname "$0")/.." && pwd)"
SRC_WEB="${GROLO_WEB_SRC:-$SRC_STACK/../grolo-web}"
EX=(--exclude .git --exclude node_modules --exclude .next --exclude .env --exclude NOTES.local.md)

echo "→ Stack nach $HOST:/opt/growatt"
rsync -a --delete "${EX[@]}" "$SRC_STACK/" "$HOST:/opt/growatt/"
if [ -d "$SRC_WEB" ]; then
  echo "→ Website nach $HOST:/opt/grolo-web"
  rsync -a --delete "${EX[@]}" "$SRC_WEB/" "$HOST:/opt/grolo-web/"
fi

# shellcheck disable=SC2029
ssh "$HOST" bash -se <<'REMOTE'
set -euo pipefail
cd /opt/growatt
# Jede fehlende Variable geht beim Neuerzeugen eines Containers verloren – lieber hier abbrechen.
if docker compose config 2>&1 >/dev/null | grep -q 'is not set'; then
  echo "ABBRUCH: in /opt/growatt/.env fehlen Variablen:" >&2
  docker compose config 2>&1 >/dev/null | grep 'is not set' >&2
  exit 1
fi
docker compose build grolo-web >/dev/null
docker compose up -d --no-deps grolo-web
docker compose restart settings shelly-control web-push
REMOTE
echo "✓ ausgeliefert"
