#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
node --check public/app.js
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
systemctl daemon-reload
systemctl enable --now reset-radar.service
systemctl restart reset-radar.service
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1/health | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(not d["ready"])'; then
    systemctl is-active reset-radar.service
    echo "Deployed $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 1
done
journalctl -u reset-radar.service -n 15 --no-pager
exit 1
