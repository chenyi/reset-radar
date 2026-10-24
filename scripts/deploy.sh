#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
node --check public/app.js
node tests/test_archive_ui.js
caddy validate --config deploy/Caddyfile --adapter caddyfile
if [ "$(systemctl is-enabled caddy 2>/dev/null || true)" = masked ]; then echo 'Unmask the prepared Caddy service before deploying.' >&2; exit 1; fi
if [ -n "$(git status --porcelain)" ]; then echo 'Commit the reviewed source before deploying.' >&2; exit 1; fi
release="/opt/reset-radar/releases/$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
mkdir -p "$release" /opt/reset-radar/backups
git archive HEAD | tar -x -C "$release"
if [ -L /opt/reset-radar/current ]; then
  previous="$(readlink -f /opt/reset-radar/current)"
  ln -sfn "$previous" /opt/reset-radar/previous
fi
if [ -f /etc/systemd/system/reset-radar.service ]; then cp -p /etc/systemd/system/reset-radar.service "/opt/reset-radar/backups/reset-radar.service.$(date -u +%Y%m%dT%H%M%SZ)"; fi
ln -sfn "$release" /opt/reset-radar/current
install -m 644 deploy/reset-radar.service /etc/systemd/system/reset-radar.service
if [ -f /etc/reset-radar/notifications.env ]; then
  install -m 644 deploy/reset-radar-notifications.service /etc/systemd/system/reset-radar-notifications.service
fi
if [ -f /etc/caddy/Caddyfile ]; then cp -p /etc/caddy/Caddyfile "/opt/reset-radar/backups/Caddyfile.$(date -u +%Y%m%dT%H%M%SZ)"; fi
install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now reset-radar.service
systemctl restart reset-radar.service
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:8787/health 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(not d["ready"])' 2>/dev/null; then
    systemctl is-active reset-radar.service
    systemctl enable --now caddy
    systemctl reload caddy
    https_ready=false
    for https_attempt in {1..60}; do
      if curl --noproxy '*' --connect-timeout 2 --max-time 3 --resolve radar.shadowearth.cn:443:127.0.0.1 -fsS https://radar.shadowearth.cn/health > /dev/null 2>&1; then https_ready=true; break; fi
      sleep 1
    done
    if [ "$https_ready" != true ]; then journalctl -u caddy -n 15 --no-pager; exit 1; fi
    if [ -f /etc/reset-radar/notifications.env ]; then
      systemctl enable --now reset-radar-notifications.service
      systemctl restart reset-radar-notifications.service
    fi
    echo "Deployed $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 1
done
journalctl -u reset-radar.service -n 15 --no-pager
exit 1
