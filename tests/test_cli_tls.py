"""Verify macOS system CA trust with the released 0.1.0 as a regression baseline."""
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BINARY = os.environ.get('RESET_RADAR_TEST_BINARY')


@unittest.skipUnless(BINARY and platform.system() == 'Darwin' and os.environ.get('RESET_RADAR_TEST_SYSTEM_TRUST') == '1', 'requires explicit macOS CI system-keychain test opt-in')
class SystemTrustTest(unittest.TestCase):
    def test_system_trust_without_ca_configuration_and_hostname_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.joinpath('ca.conf').write_text('''[req]
distinguished_name=dn
x509_extensions=extensions
prompt=no
[dn]
CN=Reset Radar CI Root
[extensions]
basicConstraints=critical,CA:true
keyUsage=critical,keyCertSign,cRLSign
''')
            root.joinpath('leaf.conf').write_text('subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n')
            commands = [
                ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-config', 'ca.conf', '-keyout', 'ca.key', '-out', 'ca.pem'],
                ['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost', '-keyout', 'leaf.key', '-out', 'leaf.csr'],
                ['openssl', 'x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-days', '2', '-extfile', 'leaf.conf', '-out', 'leaf.pem'],
            ]
            for command in commands:
                subprocess.run(command, cwd=root, capture_output=True, check=True)

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass

                def do_GET(self):
                    body = json.dumps({'api_version': '1', 'data': {'system_trust': True}, 'meta': {}}).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / 'leaf.pem', root / 'leaf.key')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            environment = dict(os.environ)
            for name in ['SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
                environment.pop(name, None)
            keychain = '/Library/Keychains/System.keychain'
            fingerprint = hashlib.sha1(ssl.PEM_cert_to_DER_cert(root.joinpath('ca.pem').read_text())).hexdigest().upper()
            trusted = False

            def query(binary, hostname='localhost'):
                return subprocess.run([str(Path(binary).resolve()), '--api-url', f'https://{hostname}:{server.server_port}', '--timeout', '5', 'status', '--json'], env=environment, text=True, capture_output=True, timeout=15)

            try:
                rejected = query(BINARY)
                self.assertEqual(rejected.returncode, 3, rejected.stdout + rejected.stderr)
                self.assertEqual(json.loads(rejected.stdout)['error']['code'], 'network')
                self.assertIn(':', json.loads(rejected.stdout)['error']['message'])
                subprocess.run(['sudo', '-n', 'security', 'add-trusted-cert', '-d', '-r', 'trustRoot', '-k', keychain, str(root / 'ca.pem')], capture_output=True, check=True)
                trusted = True
                baseline = os.environ.get('RESET_RADAR_BASELINE_BINARY')
                if baseline:
                    before = query(baseline)
                    self.assertEqual(before.returncode, 3, '0.1.0 must reproduce the corporate CA failure')
                accepted = query(BINARY)
                self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
                self.assertTrue(json.loads(accepted.stdout)['data']['system_trust'])
                wrong_host = query(BINARY, '127.0.0.1')
                self.assertEqual(wrong_host.returncode, 3, wrong_host.stdout + wrong_host.stderr)
            finally:
                if trusted:
                    subprocess.run(['sudo', '-n', 'security', 'delete-certificate', '-Z', fingerprint, keychain], capture_output=True, check=True)
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
