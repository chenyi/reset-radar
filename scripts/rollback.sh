#!/usr/bin/env bash
set -euo pipefail
if [ ! -L /opt/reset-radar/previous ]; then echo 'No previous release exists.' >&2; exit 1; fi
previous="$(readlink -f /opt/reset-radar/previous)"
current="$(readlink -f /opt/reset-radar/current)"
ln -sfn "$previous" /opt/reset-radar/current
ln -sfn "$current" /opt/reset-radar/previous
systemctl restart reset-radar.service
if [ -f /etc/reset-radar/notifications.env ]; then
  if grep -q 'PUBLIC_ORIGIN' "$previous/notification_templates.py"; then
    systemctl restart reset-radar-notifications.service
  else
    systemctl stop reset-radar-notifications.service
    echo 'Pre-HTTPS notification templates: sender paused until an HTTPS-compatible release is deployed.' >&2
  fi
fi
curl --retry 5 --retry-delay 1 --retry-connrefused -fsS http://127.0.0.1:8787/health
curl --noproxy '*' --resolve radar.shadowearth.cn:443:127.0.0.1 -fsS https://radar.shadowearth.cn/health
