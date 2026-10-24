"""Exercise the actual CLI process against API and release HTTP fixtures."""
import fcntl
import hashlib
import io
import json
import os
import platform
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import agent_api
from tests.test_agent_api import NOW, event, snapshot


BINARY = os.environ.get('RESET_RADAR_TEST_BINARY')
TARGET = 'aarch64-apple-darwin' if platform.system() == 'Darwin' else 'x86_64-unknown-linux-musl'


def archive(files):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content); info.mode = 0o755
            tar.addfile(info, io.BytesIO(content))
    return result.getvalue()


@unittest.skipUnless(BINARY, 'set RESET_RADAR_TEST_BINARY to a built executable')
class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = str(Path(BINARY).resolve())
        cls.current_version = subprocess.check_output([cls.binary, '--version'], text=True).strip().split()[-1]
        cls.raw_binary = Path(cls.binary).read_bytes()
        cls.releases = {}
        for version in (cls.current_version, '9.0.0', '8.0.0'):
            binary = cls.raw_binary if version == cls.current_version else (
                f'#!/bin/sh\nprintf "reset-radar {version}\\n"\n').encode()
            binary_tar = archive({'reset-radar': binary})
            skill_tar = archive({'reset-radar/SKILL.md': f'---\nname: reset-radar\ndescription: Test {version}\n---\n'.encode()})
            name = f'reset-radar-{TARGET}.tar.gz'
            manifest = {'version': version, 'api_version': '1', 'artifacts': {TARGET: {
                'file': name, 'sha256': hashlib.sha256(binary_tar).hexdigest(),
                'binary_sha256': hashlib.sha256(binary).hexdigest()}}, 'skill': {
                'file': 'reset-radar-skill.tar.gz', 'sha256': hashlib.sha256(skill_tar).hexdigest()}}
            cls.releases[version] = {name: binary_tar, 'reset-radar-skill.tar.gz': skill_tar,
                                     'release-manifest.json': json.dumps(manifest).encode()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.home = Path(self.temp.name)
        self.data = snapshot([event()]); self.site_down = False; self.corrupt = False
        self.broken_binary = False; self.interrupt = False; self.stable_version = '9.0.0'; self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_GET(self):
                parsed = urlsplit(self.path); path = parsed.path
                outer.requests.append(self.path)
                status, data = 200, None
                if path.startswith('/bad-api/'):
                    data = {'api_version': '2', 'data': {}, 'meta': {}}
                elif path.startswith('/failure/'):
                    status, data = 429, {'error': {'code': 'rate_limited', 'message': 'Try later'}}
                elif path.startswith('/slow/'):
                    time.sleep(2); data = {}
                elif path.startswith('/api/v1/'):
                    status, data = agent_api.respond(path, parse_qs(parsed.query), outer.data, NOW)
                elif path.startswith('/downloads/') and outer.site_down:
                    status, data = 503, {}
                elif path == '/downloads/cli/stable.json':
                    data = {'version': outer.stable_version}
                elif path == '/releases':
                    data = [{'tag_name': 'v99.0.0', 'prerelease': False},
                            {'tag_name': 'cli-v10.0.0-rc.1', 'prerelease': True},
                            {'tag_name': 'cli-v9.0.0', 'prerelease': False}]
                elif path.startswith(('/downloads/cli-v', '/github/cli-v')):
                    version, filename = path.split('/')[-2:]; version = version.removeprefix('cli-v')
                    data = outer.releases.get(version, {}).get(filename)
                    if data is None: status, data = 404, {}
                    elif filename.endswith('.tar.gz') and filename.startswith('reset-radar-' + TARGET):
                        if outer.corrupt: data = b'corrupt archive'
                        elif outer.broken_binary:
                            data = archive({'reset-radar': b'#!/bin/sh\nexit 1\n'})
                    if outer.broken_binary and filename == 'release-manifest.json':
                        body = json.loads(data)
                        body['artifacts'][TARGET]['sha256'] = hashlib.sha256(archive({'reset-radar': b'#!/bin/sh\nexit 1\n'})).hexdigest()
                        data = json.dumps(body).encode()
                else: status, data = 404, {}
                raw = data if isinstance(data, bytes) else json.dumps(data).encode()
                interrupted = outer.interrupt and path.endswith(TARGET + '.tar.gz')
                self.send_response(status); self.send_header('Content-Length', str(len(raw) + (100 if interrupted else 0))); self.end_headers()
                try: self.wfile.write(raw)
                except BrokenPipeError: pass
                if interrupted: self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.env = {**os.environ, 'HOME': str(self.home), 'RESET_RADAR_DOWNLOAD_BASE': self.base + '/downloads',
                    'RESET_RADAR_GITHUB_DOWNLOAD_BASE': self.base + '/github', 'RESET_RADAR_GITHUB_API': self.base + '/releases'}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def run_cli(self, *args, code=0):
        default_api = [] if '--api-url' in args else ['--api-url', self.base]
        result = subprocess.run([self.binary, *default_api, '--json', *args],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def install(self, skill=True):
        return self.run_cli('install', *(['--agent', 'codex'] if skill else []))

    def current(self):
        return (self.home / '.local/share/reset-radar/current').readlink()

    def test_status_semantics_and_text_timezone(self):
        self.assertEqual(self.run_cli('status')['data']['forecast_status'], 'active')
        self.data['last_sync'] = '2026-10-23T00:00:00Z'
        self.assertEqual(self.run_cli('status')['data']['forecast_status'], 'unknown')
        result = subprocess.run([self.binary, '--api-url', self.base, '--timezone', 'Asia/Shanghai', 'status'],
                                capture_output=True, text=True)
        self.assertIn('2026-10-24 18:00:00', result.stdout)
        self.assertIn('无法确认', result.stdout)

    def test_filters_detail_and_invalid_json_arguments(self):
        body = self.run_cli('events', 'list', '--since', '7d', '--state', 'scheduled')
        self.assertTrue(body['data']['filters']['since'].endswith('Z'))
        self.assertEqual(self.run_cli('events', 'get', 'reset-1')['data']['event_id'], 'reset-1')
        self.assertEqual(self.run_cli('events', 'get', 'missing', code=4)['error']['code'], 'not_found')
        self.run_cli('events', 'list', '--limit', '0', code=2)
        self.run_cli('events', 'list', '--since', '0d', code=2)
        self.run_cli('events', 'list', '--since', '😀', code=2)

    def test_network_api_errors_and_protocol_version(self):
        self.run_cli('--api-url', self.base + '/bad-api', 'status', code=5)
        self.assertEqual(self.run_cli('--api-url', self.base + '/failure', 'status', code=4)['error']['code'], 'rate_limited')
        self.run_cli('--api-url', self.base + '/slow', '--timeout', '1', 'status', code=3)
        self.run_cli('--api-url', 'http://example.com', 'status', code=2)
        body = self.run_cli('--api-url', self.base.replace('http:', 'https:') + '/?token=acceptance-secret', 'status', code=3)
        self.assertNotIn('acceptance-secret', json.dumps(body))
        self.assertIn(':', body['error']['message'])

    def test_install_idempotence_and_skill_removal_preserves_other_skill(self):
        self.install(); before = self.current()
        self.assertFalse(self.install()['changed']); self.assertEqual(self.current(), before)
        other = self.home / '.agents/skills/another/SKILL.md'; other.parent.mkdir(); other.write_text('keep')
        self.assertTrue(self.run_cli('skill', 'status')['installed'])
        self.assertFalse(self.run_cli('skill', 'update')['changed'])
        self.run_cli('skill', 'remove')
        self.assertFalse(self.run_cli('skill', 'status')['installed'])
        self.assertTrue(other.exists()); self.assertTrue((self.home / '.local/bin/reset-radar').exists())
        self.run_cli('skill', 'install', '--agent', 'codex')
        self.assertTrue(self.run_cli('skill', 'status')['installed'])

    def test_update_check_upgrade_and_explicit_rollback_sync_skill(self):
        self.install(); before = self.current()
        self.assertTrue(self.run_cli('update', '--check')['update_available']); self.assertEqual(self.current(), before)
        result = self.run_cli('update'); self.assertEqual(result['version'], '9.0.0')
        self.assertIn('9.0.0', (self.home / '.agents/skills/reset-radar/SKILL.md').read_text())
        self.assertIn('9.0.0', subprocess.check_output([str(self.home / '.local/bin/reset-radar'), '--version'], text=True))
        self.run_cli('update', '--version', self.current_version)
        self.assertIn(self.current_version, (self.home / '.agents/skills/reset-radar/SKILL.md').read_text())

    def test_cli_update_does_not_add_uninstalled_skill(self):
        self.install(False); self.run_cli('update')
        self.assertFalse((self.home / '.agents/skills/reset-radar').exists())

    def test_default_update_never_downgrades(self):
        self.install(); self.run_cli('update'); before = self.current()
        self.stable_version = '8.0.0'
        self.assertFalse(self.run_cli('update')['changed'])
        self.assertEqual(self.current(), before)
        self.assertEqual(self.run_cli('update', '--version', '8.0.0')['version'], '8.0.0')

    def test_checksum_failure_and_failed_new_binary_preserve_old_pair(self):
        self.install(); before = self.current()
        self.corrupt = True
        self.assertEqual(self.run_cli('update', code=6)['error']['code'], 'checksum_mismatch')
        self.assertEqual(self.current(), before)
        self.corrupt = False; self.broken_binary = True
        self.run_cli('update', code=6); self.assertEqual(self.current(), before)

    def test_user_edits_and_unowned_files_are_not_overwritten(self):
        self.install(); before = self.current()
        skill = self.home / '.agents/skills/reset-radar/SKILL.md'; skill.write_text('user change')
        self.run_cli('update', code=6); self.run_cli('skill', 'remove', code=6)
        self.assertEqual(skill.read_text(), 'user change'); self.assertEqual(self.current(), before)

    def test_unmanaged_binary_conflict(self):
        path = self.home / '.local/bin/reset-radar'; path.parent.mkdir(parents=True); path.write_text('other')
        self.run_cli('install', code=6); self.assertEqual(path.read_text(), 'other')

    def test_auto_fallback_and_github_only_filter_out_backend_releases(self):
        self.install(); self.site_down = True
        result = self.run_cli('update', '--check'); self.assertEqual(result['available_version'], '9.0.0')
        self.run_cli('update', '--download-source', 'github')
        self.assertTrue(any('/github/cli-v9.0.0/' in p for p in self.requests))

    def test_concurrent_update_lock(self):
        self.install()
        with (self.home / '.local/share/reset-radar/install.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_cli('update', code=6)

    def test_interrupted_download_does_not_switch_version(self):
        self.install(); before = self.current(); self.interrupt = True
        self.run_cli('update', code=3)
        self.assertEqual(self.current(), before)

    def test_filesystem_failure_after_switch_rolls_back(self):
        self.install(False); before = self.current()
        parent = self.home / '.agents/skills'; parent.mkdir(parents=True)
        parent.chmod(0o500)
        try:
            self.run_cli('skill', 'install', code=6)
            self.assertEqual(self.current(), before)
            self.assertFalse((parent / 'reset-radar').exists())
        finally:
            parent.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
