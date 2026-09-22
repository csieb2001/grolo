#!/usr/bin/env bash
# grolo-backup: sichert die Daten des Stacks verschlüsselt in einen restic-Ablageort.
#
# Was gesichert wird, und warum genau das:
#   influx/       konsistenter Auszug der Messreihen. NICHT das laufende Datenverzeichnis kopieren –
#                 InfluxDB schreibt währenddessen weiter, und eine halb geschriebene TSM-Datei ist beim
#                 Zurückspielen wertlos. `influx backup` erzeugt einen in sich stimmigen Stand.
#   postgres/     Auszug der Website-Datenbank, aus demselben Grund wie bei InfluxDB: das laufende
#                 Datenverzeichnis zu kopieren ergibt keinen verlässlichen Stand, pg_dump schon.
#   volumes/      die übrigen Docker-Volumes. Klein, aber teils unersetzlich: growatt_matter-data trägt
#                 die Schlüssel unserer Matter-Fabric – ohne sie müssen alle tado-Geräte neu gekoppelt
#                 werden. growatt_wolf-state trägt die Takt-Historie der Wärmepumpe.
#   opt/          /opt/growatt mit .env und den Mosquitto-Zertifikaten. Der Code liegt auf GitHub, diese
#                 beiden nicht – und ohne sie startet der Stack nicht.
#
# Verschlüsselt wird immer: in diesem Paket stecken die Matter-Schlüssel, der Fachmann-Code der Wärmepumpe,
# das Grafana-Passwort und der Ingest-Token der Website. restic verschlüsselt grundsätzlich; das Repository-
# Passwort ist damit der einzige Schlüssel zu allem. Geht es verloren, ist die Sicherung wertlos.
#
# Umgebung (aus /etc/grolo-backup.env):
#   RESTIC_REPOSITORY, RESTIC_PASSWORD und die Zugangsdaten des Ablageorts
set -euo pipefail

ENV_FILE=${GROLO_BACKUP_ENV:-/etc/grolo-backup.env}
WORK=${GROLO_BACKUP_WORK:-/var/tmp/grolo-backup}
KEEP_DAILY=${KEEP_DAILY:-7}; KEEP_WEEKLY=${KEEP_WEEKLY:-4}; KEEP_MONTHLY=${KEEP_MONTHLY:-6}

[[ -r $ENV_FILE ]] || { echo "Zugangsdaten fehlen: $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY fehlt}" "${RESTIC_PASSWORD:?RESTIC_PASSWORD fehlt}"

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
trap 'rm -rf "$WORK"' EXIT
rm -rf "$WORK"; mkdir -p "$WORK"/{influx,postgres,volumes,opt}

# ---------------------------------------------------------------- InfluxDB, in sich stimmig
if docker ps --format '{{.Names}}' | grep -qx grolo-influxdb; then
  set -a; . /opt/growatt/.env; set +a
  log "InfluxDB-Auszug…"
  docker exec grolo-influxdb rm -rf /tmp/grolo-backup
  docker exec grolo-influxdb influx backup /tmp/grolo-backup \
    --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" >/dev/null
  docker cp -q grolo-influxdb:/tmp/grolo-backup "$WORK/influx" 2>/dev/null \
    || docker cp grolo-influxdb:/tmp/grolo-backup "$WORK/influx"
  docker exec grolo-influxdb rm -rf /tmp/grolo-backup
  log "InfluxDB: $(du -sh "$WORK/influx" | cut -f1)"
else
  log "InfluxDB läuft nicht – überspringe den Auszug"
fi

# ---------------------------------------------------------------- Postgres der Website, in sich stimmig
if docker ps --format '{{.Names}}' | grep -qx grolo-db; then
  set -a; . /opt/growatt/.env; set +a
  log "Postgres-Auszug…"
  docker exec grolo-db pg_dump -U "${PGUSER:-grolo}" -d "${PGDATABASE:-grolo}" -Fc --no-owner --no-acl \
    > "$WORK/postgres/grolo.dump"
  log "Postgres: $(du -sh "$WORK/postgres" | cut -f1)"
else
  log "Postgres läuft nicht – überspringe den Auszug"
fi

# ---------------------------------------------------------------- übrige Volumes
# growatt_pgdata bleibt außen vor: dafür steht oben der pg_dump, eine Kopie des laufenden
# Datenverzeichnisses wäre beim Zurückspielen nicht verlässlich.
for v in $(docker volume ls -q --filter name=growatt_ | grep -v -e '^growatt_influxdb-data$' -e '^growatt_pgdata$'); do
  src="/var/lib/docker/volumes/$v/_data"
  [[ -d $src ]] || continue
  mkdir -p "$WORK/volumes/$v"
  cp -a "$src/." "$WORK/volumes/$v/" 2>/dev/null || true
done
log "Volumes: $(du -sh "$WORK/volumes" | cut -f1)"

# ---------------------------------------------------------------- Konfiguration
cp -a /opt/growatt/.env "$WORK/opt/.env" 2>/dev/null || true
cp -a /opt/growatt/NOTES.local.md "$WORK/opt/NOTES.local.md" 2>/dev/null || true
mkdir -p "$WORK/opt/certs"; cp -a /opt/growatt/mosquitto/certs/. "$WORK/opt/certs/" 2>/dev/null || true
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "$WORK/opt/container.txt" 2>/dev/null || true

# ---------------------------------------------------------------- sichern
restic snapshots >/dev/null 2>&1 || { log "Ablageort wird angelegt"; restic init; }
log "übertrage…"
restic backup --tag grolo --tag data --host "$(hostname)" "$WORK" \
  --exclude-caches --compression auto
log "aufräumen (täglich $KEEP_DAILY, wöchentlich $KEEP_WEEKLY, monatlich $KEEP_MONTHLY)"
restic forget --tag data --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" \
  --keep-monthly "$KEEP_MONTHLY" --prune
restic snapshots --tag data --compact | tail -5

# ---------------------------------------------------------------- Platz im Blick behalten
# Der Gratisrahmen von B2 endet bei 10 GB. Erreicht wird das planmäßig nie – die Aufbewahrungsregeln
# deckeln das Wachstum –, aber InfluxDB wächst, und eine Warnung ist billiger als eine Überraschung.
LIMIT_GB=${REPO_LIMIT_GB:-8}
raw=$(restic stats --mode raw-data --json 2>/dev/null | sed -n 's/.*"total_size":\([0-9]*\).*/\1/p' || true)
if [[ -n ${raw:-} ]]; then
  gb=$(awk -v b="$raw" 'BEGIN{printf "%.2f", b/1073741824}')
  log "Ablageort belegt ${gb} GB"
  awk -v g="$gb" -v l="$LIMIT_GB" 'BEGIN{exit !(g>l)}' && \
    log "ACHTUNG: über ${LIMIT_GB} GB – Aufbewahrung kürzen oder Tarif prüfen, der Gratisrahmen endet bei 10 GB"
fi
log "fertig"
