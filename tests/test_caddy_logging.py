"""Exercise the real Caddy formatter/proxy on isolated loopback ports."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which('caddy'), 'requires real Caddy binary')
class CaddyLoggingTest(unittest.TestCase):
    def test_real_proxy_redacts_and_correlates_including_failures_and_rotation(self):
        class Fixture(server.Handler):
            def do_GET(self):
                if self.path.startswith('/failure'):
                    raise RuntimeError('fixture provider unavailable')
                self.send(200, b'ok', 'text/plain')

        root = Path(__file__).resolve().parent.parent
        config = json.loads(subprocess.check_output(['caddy', 'adapt', '--adapter', 'caddyfile',
            '--config', os.environ.get('RADAR_CADDYFILE', str(root / 'deploy/Caddyfile'))]))
        public = config['apps']['http']['servers']['srv0']
        self.assertEqual(public['listen'], [':443'])
        self.assertTrue(public['automatic_https']['disable_redirects'])
        self.assertTrue(config['apps']['tls']['automation']['policies'][0]['issuers'][0]['challenges']['http']['disabled'])
        http = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        proxy_port = port()
        public['listen'] = ['127.0.0.1:' + str(proxy_port)]
        public['automatic_https'] = {'disable': True}
        public['logs']['logger_names'] = {'127.0.0.1': next(iter(public['logs']['logger_names'].values()))}
        config['apps'].pop('tls', None)
        config['admin'] = {'disabled': True}
        def change(value):
            if isinstance(value, dict):
                if 'host' in value:
                    value['host'] = ['127.0.0.1']
                if 'dial' in value:
                    value['dial'] = '127.0.0.1:' + str(http.server_port)
                for child in value.values():
                    change(child)
            elif isinstance(value, list):
                for child in value:
                    change(child)
        change(public)
        output = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(output):
                temporary = Path(temporary)
                logfile = temporary / 'access.jsonl'
                # Seed a full file before opening the writer to exercise real size-based rotation.
                logfile.write_bytes(b' ' * 1048576)
                for logger in config['logging']['logs'].values():
                    writer = logger.get('writer', {})
                    if writer.get('output') == 'file':
                        self.assertEqual(writer['roll_size_mb'], 20)
                        writer.update(filename=str(logfile), roll_size_mb=1)
                filename = temporary / 'caddy.json'
                filename.write_text(json.dumps(config))
                with (temporary / 'runtime.log').open('w') as runtime:
                    process = subprocess.Popen(['caddy', 'run', '--config', str(filename)], stdout=runtime, stderr=runtime)
                    try:
                        for _ in range(50):
                            if process.poll() is not None:
                                self.fail((temporary / 'runtime.log').read_text())
                            try:
                                with socket.create_connection(('127.0.0.1', proxy_port), timeout=0.2):
                                    break
                            except OSError:
                                time.sleep(0.1)
                        request = urllib.request.Request('http://127.0.0.1:' + str(proxy_port) + '/example?q=private-search&token=private-query', headers={
                            'Cookie': 'session=private-cookie', 'Authorization': 'Bearer private-authorization',
                            'Referer': 'https://user:private-password@source.example/path?secret=private-referer',
                            'X-Forwarded-For': '198.51.100.77', 'User-Agent': 'RadarFixture/1.0',
                            'X-Request-ID': 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'})
                        with urllib.request.urlopen(request, timeout=4) as response:
                            self.assertEqual(response.status, 200)
                            identity = response.headers['X-Request-ID']
                            self.assertNotEqual(identity, request.headers['X-request-id'])
                        with self.assertRaises(urllib.error.HTTPError) as failure:
                            urllib.request.urlopen('http://127.0.0.1:' + str(proxy_port) + '/failure', timeout=4)
                        self.assertEqual(failure.exception.code, 500)
                        failed_identity = failure.exception.headers['X-Request-ID']
                        failure.exception.read(); failure.exception.close()
                    finally:
                        process.terminate(); process.wait(timeout=8)
                records = [json.loads(line) for line in logfile.read_text().splitlines() if line.strip()]
                self.assertTrue([p for p in temporary.iterdir() if p.name.startswith('access') and p != logfile], [p.name for p in temporary.iterdir()])
                first = next(r for r in records if r.get('request_id') == identity)
                self.assertEqual(first['client_ip'], '127.0.0.1')
                self.assertEqual(first['path'], '/example')
                self.assertEqual(first['referer_host'], 'source.example')
                self.assertEqual(first['user_agent'], 'RadarFixture/1.0')
                self.assertEqual(first['status'], 200)
                self.assertNotIn('request', first)
                self.assertNotIn('resp_headers', first)
                errors = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertTrue(any(r['event'] == 'http.failed' and r['request_id'] == failed_identity for r in errors))
                self.assertTrue(any(r['request_id'] == failed_identity and r['status'] == 500 for r in records))
                all_text = logfile.read_text() + output.getvalue() + (temporary / 'runtime.log').read_text()
                for secret in ['private-search', 'private-query', 'private-cookie', 'private-authorization', 'private-password', 'private-referer', '198.51.100.77']:
                    self.assertNotIn(secret, all_text)
        finally:
            http.shutdown(); http.server_close(); thread.join()
