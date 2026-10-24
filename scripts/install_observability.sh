#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
test "$(id -u)" = 0
backup="/opt/reset-radar/backups/observability-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
if [ ! -f /usr/local/ilogtail/ilogtail_config.json ]; then
  curl -fsSL --max-time 60 https://aliyun-observability-release-ap-southeast-1.oss-ap-southeast-1.aliyuncs.com/loongcollector/linux64/latest/loongcollector.sh -o "$backup/loongcollector.sh"
  # The official installer currently defaults package downloads to HTTP.
  sed 's@PACKAGE_REGION_ADDRESS="http://@PACKAGE_REGION_ADDRESS="https://@' "$backup/loongcollector.sh" > "$backup/loongcollector-https.sh"
  bash "$backup/loongcollector-https.sh" install ap-southeast-1 -v 3.3.10
fi
install -d -m 700 /var/lib/reset-radar-observability
cp -p /usr/local/ilogtail/ilogtail_config.json "$backup/ilogtail_config.json.before"
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/usr/local/ilogtail/ilogtail_config.json')
d=json.loads(p.read_text())
if any(server['region']!='ap-southeast-1' for server in d.get('data_servers', [])):
    raise SystemExit('Unexpected collector region; review existing configuration')
d.update(check_point_filename='/var/lib/reset-radar-observability/checkpoint', buffer_file_num=10, buffer_file_size=20971520, cpu_usage_limit=0.4,
         mem_usage_limit=384, max_bytes_per_sec=4096)
p.write_text(json.dumps(d,indent=2)+'\n')
identity=Path('/etc/ilogtail/user_defined_id')
lines=identity.read_text().splitlines() if identity.exists() else []
if 'reset-radar-sg' not in lines:
    with identity.open('a') as output:
        if lines and identity.read_bytes()[-1:]!=b'\n':output.write('\n')
        output.write('reset-radar-sg\n')
identity.chmod(0o600)
PY
/etc/init.d/loongcollectord stop
trap '/etc/init.d/loongcollectord start >/dev/null 2>&1 || true' EXIT
if [ -f /tmp/logtail_check_point ] && [ ! -f /var/lib/reset-radar-observability/checkpoint ]; then cp -p /tmp/logtail_check_point /var/lib/reset-radar-observability/checkpoint; fi
/etc/init.d/loongcollectord start
trap - EXIT
/etc/init.d/loongcollectord status
printf 'Collector configured: Singapore intranet, 200 MiB disk buffer, 4 KiB/s raw send target.\n'

install -m 644 deploy/reset-radar-observability.service deploy/reset-radar-observability.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now reset-radar-observability.timer
systemctl start reset-radar-observability.service
