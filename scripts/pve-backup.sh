#!/usr/bin/env bash
# pve-backup: sichert die Konfiguration des Proxmox-Hosts in denselben restic-Ablageort.
#
# Der Host selbst ist in einer Stunde neu installiert; was fehlen würde, sind die paar Dateien, die seinen
# Zustand ausmachen: die Container- und VM-Definitionen unter /etc/pve, das Netz, die Bootzeile (dort steht
# bei uns, dass IPv6 nicht mehr abgeschaltet ist – ohne das kein Matter), Speicher- und Nutzerkonfiguration.
# Zusammen sind das wenige hundert Kilobyte, und sie sparen beim Wiederaufbau den halben Tag.
#
# Das vzdump-Abbild des Containers läuft getrennt davon (pve-image-backup.sh, monatlich): es ist knapp
# zwei Gigabyte je Stand, lässt sich kaum deduplizieren und braucht deshalb eine eigene, knappe
# Aufbewahrung. Beides in einem Lauf hätte zur Folge, dass eine Regel die Stände der anderen wegräumt.
set -euo pipefail

ENV_FILE=${GROLO_BACKUP_ENV:-/etc/grolo-backup.env}
WORK=${PVE_BACKUP_WORK:-/var/tmp/pve-backup}

[[ -r $ENV_FILE ]] || { echo "Zugangsdaten fehlen: $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}"

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
trap 'rm -rf "$WORK"' EXIT
rm -rf "$WORK"; mkdir -p "$WORK"

cp -a /etc/pve/lxc "$WORK/lxc" 2>/dev/null || true
cp -a /etc/pve/qemu-server "$WORK/qemu-server" 2>/dev/null || true
for f in /etc/pve/storage.cfg /etc/pve/user.cfg /etc/pve/datacenter.cfg /etc/pve/jobs.cfg \
         /etc/network/interfaces /etc/default/grub /etc/hosts /etc/fstab; do
  [[ -r $f ]] && cp -a "$f" "$WORK/$(basename "$f")"
done
pveversion -v > "$WORK/pveversion.txt" 2>/dev/null || true
pvesm status  > "$WORK/storage-status.txt" 2>/dev/null || true
lvs -o+seg_pe_ranges > "$WORK/lvs.txt" 2>/dev/null || true

restic snapshots >/dev/null 2>&1 || { log "Ablageort wird angelegt"; restic init; }
restic backup --tag grolo --tag pve --host "$(hostname)" "$WORK" --exclude-caches --compression auto
restic forget --tag pve --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
log "fertig"
