#!/usr/bin/env bash
set -euo pipefail
if [ ! -L /opt/reset-radar/previous ]; then echo 'No previous release exists.' >&2; exit 1; fi
previous="$(readlink -f /opt/reset-radar/previous)"
current="$(readlink -f /opt/reset-radar/current)"
ln -sfn "$previous" /opt/reset-radar/current
ln -sfn "$current" /opt/reset-radar/previous
systemctl restart reset-radar.service
curl --retry 5 --retry-delay 1 --retry-connrefused -fsS http://127.0.0.1/health
