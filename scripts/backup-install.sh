#!/usr/bin/env bash
# backup-install: legt Zeitplan und Zugangsdaten für die Sicherung an – auf dem Host und im Container.
# Aufruf auf dem Proxmox-Host, nachdem /etc/grolo-backup.env dort gefüllt ist.
set -euo pipefail
CT=${CT:-103}

unit() {   # $1 = Name, $2 = Skriptpfad, $3 = Beschreibung, $4 = Zeit
  cat <<UNIT > "/etc/systemd/system/$1.service"
[Unit]
Description=$3
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$2
Nice=10
IOSchedulingClass=idle
UNIT
  cat <<UNIT > "/etc/systemd/system/$1.timer"
[Unit]
Description=$3

[Timer]
OnCalendar=$4
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
UNIT
}

# ---------------------------------------------------------------- Container: die Daten, täglich
pct exec "$CT" -- mkdir -p /usr/local/sbin
pct push "$CT" "$(dirname "$0")/grolo-backup.sh" /usr/local/sbin/grolo-backup.sh --perms 755
pct exec "$CT" -- bash -c 'cat > /etc/systemd/system/grolo-backup.service <<U
[Unit]
Description=GroLo Datensicherung
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/grolo-backup.sh
Nice=10
IOSchedulingClass=idle
U
cat > /etc/systemd/system/grolo-backup.timer <<U
[Unit]
Description=GroLo Datensicherung

[Timer]
OnCalendar=*-*-* 03:20:00
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
U
systemctl daemon-reload && systemctl enable --now grolo-backup.timer'

# ---------------------------------------------------------------- Host: die Konfiguration, wöchentlich
install -m 755 "$(dirname "$0")/pve-backup.sh" /usr/local/sbin/pve-backup.sh
unit pve-backup /usr/local/sbin/pve-backup.sh "Proxmox-Konfigurationssicherung" "Sun *-*-* 03:00:00"

# ---------------------------------------------------------------- Host: das Abbild, monatlich
install -m 755 "$(dirname "$0")/pve-image-backup.sh" /usr/local/sbin/pve-image-backup.sh
unit pve-image-backup /usr/local/sbin/pve-image-backup.sh "Proxmox-Abbildsicherung" "*-*-01 02:00:00"

systemctl daemon-reload && systemctl enable --now pve-backup.timer pve-image-backup.timer

echo "Zeitpläne aktiv:"
systemctl list-timers --no-pager pve-backup.timer pve-image-backup.timer 2>/dev/null | head -4
pct exec "$CT" -- systemctl list-timers --no-pager grolo-backup.timer 2>/dev/null | head -3
