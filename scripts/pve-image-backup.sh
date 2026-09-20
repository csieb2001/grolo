#!/usr/bin/env bash
# pve-image-backup: monatlich ein vollständiges Abbild des Containers in denselben restic-Ablageort.
#
# Ein vzdump ist knapp zwei Gigabyte und ein einziges zstd-Archiv – restic kann daran fast nichts
# deduplizieren, jeder Stand kostet also den vollen Platz. Deshalb monatlich statt täglich und nur zwei
# Stände aufbewahrt; zusammen rund vier der zehn freien Gigabyte. Die täglichen Daten (grolo-backup.sh)
# tragen eine eigene Marke und eine eigene Aufbewahrung und bleiben davon unberührt.
#
# Gebraucht wird das hier nur beim Totalausfall der Maschine: dann spart es den Wiederaufbau des Containers.
# Für alles andere – versehentlich gelöschte Messreihen, kaputte Konfiguration – sind die Daten schneller da.
set -euo pipefail

ENV_FILE=${GROLO_BACKUP_ENV:-/etc/grolo-backup.env}
WORK=${PVE_IMAGE_WORK:-/var/tmp/pve-image}
CT=${CT:-103}
KEEP=${KEEP_IMAGES:-2}

[[ -r $ENV_FILE ]] || { echo "Zugangsdaten fehlen: $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}"

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
trap 'rm -rf "$WORK"' EXIT
rm -rf "$WORK"; mkdir -p "$WORK"

# Genug Platz? Ein halb geschriebenes Abbild ist schlimmer als keines.
free_mb=$(df --output=avail -m "$WORK" | tail -1)
(( free_mb > 6000 )) || { echo "zu wenig Platz für ein Abbild: ${free_mb} MB frei"; exit 1; }

log "vzdump des Containers $CT – dauert einige Minuten"
vzdump "$CT" --mode snapshot --compress zstd --dumpdir "$WORK" >/dev/null
log "Abbild: $(du -sh "$WORK" | cut -f1)"

restic snapshots >/dev/null 2>&1 || restic init
restic backup --tag grolo --tag image --host "$(hostname)" "$WORK" --compression auto
restic forget --tag image --keep-last "$KEEP" --prune
restic snapshots --tag image --compact
log "fertig"
