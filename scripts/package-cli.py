#!/usr/bin/env python3
"""Create one immutable CLI release directory from already-built executables."""
import argparse
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(path, files):
    with tarfile.open(path, 'w:gz') as tar:
        for name, data, mode in files:
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(data), mode, 0
            tar.addfile(item, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--binary', action='append', required=True, help='target=/path/to/executable')
    parser.add_argument('--out', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', args.version):
        parser.error('Invalid version')
    dest = args.out / ('cli-v' + args.version)
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {'version': args.version, 'api_version': '1', 'artifacts': {}}
    for value in args.binary:
        target, executable = value.split('=', 1)
        if target not in ('aarch64-apple-darwin', 'x86_64-unknown-linux-musl'):
            parser.error('Unsupported target')
        data = Path(executable).read_bytes()
        filename = f'reset-radar-{target}.tar.gz'
        archive(dest / filename, [('reset-radar', data, 0o755)])
        manifest['artifacts'][target] = {'file': filename, 'sha256': sha((dest / filename).read_bytes()), 'binary_sha256': sha(data)}
    skill = 'reset-radar-skill.tar.gz'
    files = [(f'reset-radar/{p.relative_to(ROOT / "skills/reset-radar")}', p.read_bytes(), 0o644)
             for p in sorted((ROOT / 'skills/reset-radar').rglob('*')) if p.is_file()]
    archive(dest / skill, files)
    manifest['skill'] = {'file': skill, 'sha256': sha((dest / skill).read_bytes())}
    (dest / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (dest / 'install.sh').write_text((ROOT / 'scripts/install-cli.sh').read_text().replace('@VERSION@', args.version))
    (dest / 'SHA256SUMS').write_text(''.join(f'{sha(p.read_bytes())}  {p.name}\n' for p in sorted(dest.iterdir())))
    print(dest)


if __name__ == '__main__':
    main()
