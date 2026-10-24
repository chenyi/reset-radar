#!/usr/bin/env python3
"""Emit bounded disk-cache health; this does not measure the in-memory send queue."""
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from observability import emit


def snapshot(root=Path('/usr/local/ilogtail'), current=None):
    current = current or time.time()
    config = json.loads((root / 'ilogtail_config.json').read_text())
    cache = Path(config.get('buffer_file_path') or root)
    files = [p.stat() for p in cache.glob('logtail_buffer_file_*') if p.is_file()]
    checkpoint = Path(config.get('check_point_filename', '/tmp/logtail_check_point'))
    return {'buffer_files': len(files), 'buffer_bytes': sum(p.st_size for p in files),
            'oldest_buffer_age_s': int(max((current-p.st_mtime for p in files), default=0)),
            'checkpoint_age_s': int(current-checkpoint.stat().st_mtime) if checkpoint.exists() else -1}


if __name__ == '__main__':
    try:
        state = snapshot()
        state['collector_active'] = int(subprocess.run(['systemctl', 'is-active', '--quiet', 'loongcollectord'], timeout=5).returncode == 0)
        emit('collector.health', **state)
    except Exception as error:
        emit('collector.health_failed', level='ERROR', error=error)
        raise SystemExit(1)
