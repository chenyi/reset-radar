#!/usr/bin/env bash
set -euo pipefail
umask 077
backup_dir=/opt/reset-radar/backups
mkdir -p "$backup_dir"
runuser -u postgres -- pg_dump -Fc reset_radar > "$backup_dir/radar-$(date -u +%Y%m%dT%H%M%SZ).dump"
