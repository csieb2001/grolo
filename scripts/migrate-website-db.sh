#!/usr/bin/env bash
# migrate-website-db: holt die Website-Datenbank aus der Cloud (Neon) in den Postgres-Container des Stacks.
#
# Der Ablauf ist so gewählt, dass keine Messwerte verloren gehen: der Push-Dienst wird zuerst angehalten
# und puffert währenddessen in seinem Volume weiter. Erst nach dem Umzug zeigt WEB_URL auf den eigenen
# Container, und der Puffer läuft in die lokale Datenbank – die Lücke schließt sich von selbst.
#
#   scripts/migrate-website-db.sh "postgres://user:pass@ep-xyz.eu-central-1.aws.neon.tech/neondb?sslmode=require"
#
# Die Quell-URL ist die UNGEPOOLTE Verbindung (DATABASE_URL_UNPOOLED bei Neon); pg_dump kommt mit dem
# Pooler nicht zuverlässig zurecht.
set -euo pipefail

SRC=${1:-${NEON_URL:-}}
[[ -n $SRC ]] || { echo "Aufruf: $0 <postgres-url-der-quelle>"; exit 1; }
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
DB=${PGDATABASE:-grolo}; USER=${PGUSER:-grolo}
DUMP=/var/tmp/grolo-website.dump

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

log "Push-Dienst anhalten (puffert ab jetzt in seinem Volume)…"
docker compose stop web-push

log "Datenbank starten…"
docker compose up -d grolo-db
for _ in $(seq 1 60); do docker exec grolo-db pg_isready -U "$USER" -d "$DB" >/dev/null 2>&1 && break; sleep 1; done
docker exec grolo-db pg_isready -U "$USER" -d "$DB"

have=$(docker exec grolo-db psql -U "$USER" -d "$DB" -tAc "SELECT to_regclass('public.samples') IS NOT NULL" || echo f)
if [[ $have == t && ${FORCE:-0} != 1 ]]; then
  echo "In $DB gibt es bereits eine Tabelle samples. Abbruch, damit nichts vermischt wird."
  echo "Absichtlich überschreiben: FORCE=1 $0 …  (leert das Schema public vorher)"
  exit 1
fi
[[ $have == t ]] && docker exec grolo-db psql -U "$USER" -d "$DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

log "Auszug aus der Quelle holen…"
docker exec -e PGURL="$SRC" grolo-db sh -c 'pg_dump "$PGURL" --no-owner --no-acl -Fc' > "$DUMP"
log "Auszug: $(du -h "$DUMP" | cut -f1)"

log "Einspielen…"
docker exec -i grolo-db pg_restore -U "$USER" -d "$DB" --no-owner < "$DUMP"

# Die Tabellen der Neon-Auth-Integration hat die Website nie benutzt; sie kommen nur im Auszug mit.
log "Unbenutzte Tabellen der Cloud-Anmeldung entfernen…"
docker exec grolo-db psql -U "$USER" -d "$DB" -q -c \
  'DROP TABLE IF EXISTS "user", session, account, verification, jwks, organization, member, invitation, project_config CASCADE' || true

log "Gegenprobe:"
docker exec grolo-db psql -U "$USER" -d "$DB" -c \
  "SELECT relname AS tabelle, n_live_tup AS zeilen FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 12"
docker exec grolo-db psql -U "$USER" -d "$DB" -c "SELECT min(ts), max(ts), count(*) FROM samples"

cat <<'NEXT'

Fertig. Weiter:
  1. In .env: WEB_URL=http://grolo-web:3000 (und SITE_PASSWORD leeren, wenn Cloudflare Access davor steht)
  2. docker compose up -d --build grolo-web
  3. docker compose up -d web-push   – der Puffer läuft nach, danach sind keine Lücken offen
     (up, nicht start: ein bestehender Container behält sonst das alte WEB_URL aus seiner Umgebung)
  4. docker logs -f grolo-web-push   – es darf keine Meldung "Senden fehlgeschlagen … Puffer N" mehr kommen
NEXT
