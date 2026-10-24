"""Run the public shell installer against real release fixtures and fresh homes."""
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


BINARY = os.environ.get('RESET_RADAR_TEST_BINARY')
ROOT = Path(__file__).resolve().parent.parent
MARKER = '# >>> reset-radar PATH >>>'


@unittest.skipUnless(BINARY, 'set RESET_RADAR_TEST_BINARY to a built executable')
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = tempfile.TemporaryDirectory()
        cls.version = subprocess.check_output([BINARY, '--version'], text=True).split()[-1]
        target = 'aarch64-apple-darwin' if sys.platform == 'darwin' else 'x86_64-unknown-linux-musl'
        subprocess.run([sys.executable, str(ROOT / 'scripts/package-cli.py'), '--version', cls.version,
                        '--binary', target + '=' + str(Path(BINARY).resolve()),
                        '--out', str(Path(cls.release.name) / 'downloads')], check=True, capture_output=True)

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=cls.release.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.script = (ROOT / 'scripts/install-cli.sh').read_text().replace('@VERSION@', cls.version)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.release.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='radar home ')
        self.home = Path(self.temp.name)
        self.env = {**os.environ, 'HOME': str(self.home), 'SHELL': '/bin/bash', 'PATH': '/usr/bin:/bin',
                    'RESET_RADAR_DOWNLOAD_BASE': f'http://127.0.0.1:{self.server.server_port}/downloads'}
        self.env.pop('ZDOTDIR', None)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *args, code=0):
        result = subprocess.run(['sh', '-s', '--', '--agent', 'codex', '--download-source', 'site', *args],
                                input=self.script, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def shell_version(self, shell, *args):
        result = subprocess.run([shell, *args, 'reset-radar --version'], env=self.env,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('reset-radar ' + self.version, result.stdout)

    def test_bash_interactive_login_backup_and_idempotence(self):
        originals = {'.bashrc': '# keep bashrc without final newline', '.bash_profile': '# keep login\n'}
        for name, text in originals.items():
            (self.home / name).write_text(text)
        result = self.install('--json')
        self.assertTrue(json.loads(result.stdout)['skill_installed'])
        for name, text in originals.items():
            config = self.home / name
            self.assertTrue(config.read_text().startswith(text))
            self.assertEqual(config.read_text().count(MARKER), 1)
            backups = list(self.home.glob(name + '.reset-radar-backup.*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), text)
        self.shell_version('/bin/bash', '-ic')
        self.shell_version('/bin/bash', '-lc')
        self.install()
        for name in originals:
            self.assertEqual((self.home / name).read_text().count(MARKER), 1)
            self.assertEqual(len(list(self.home.glob(name + '.reset-radar-backup.*'))), 1)
        self.assertFalse((self.home / '.profile').exists())

    @unittest.skipUnless(shutil.which('zsh'), 'zsh is not installed')
    def test_zsh_custom_zdotdir_new_shell_and_unique_path(self):
        zsh = shutil.which('zsh')
        self.env.update(SHELL=zsh, ZDOTDIR=str(self.home / 'zsh config'))
        config = Path(self.env['ZDOTDIR'])
        config.mkdir()
        (config / '.zshrc').write_text('# existing zsh config\n')
        self.install()
        for name in ('.zprofile', '.zshrc'):
            self.assertEqual((config / name).read_text().count(MARKER), 1)
        self.shell_version(zsh, '-ic')
        self.shell_version(zsh, '-lc')
        result = subprocess.run([zsh, '-lic', 'print -l $path'], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines().count(str(self.home / '.local/bin')), 1)
        self.install()
        self.assertEqual((config / '.zshrc').read_text().count(MARKER), 1)
        self.assertFalse((self.home / '.zshrc').exists())

    def test_bash_login_precedence_and_profile_symlink(self):
        target = self.home / 'shared profile'
        target.write_text('# linked profile\n')
        (self.home / '.bash_login').symlink_to(target)
        (self.home / '.profile').write_text('# lower priority\n')
        self.install()
        self.assertTrue((self.home / '.bash_login').is_symlink())
        self.assertIn(MARKER, target.read_text())
        self.assertEqual((self.home / '.profile').read_text(), '# lower priority\n')
        self.shell_version('/bin/bash', '-lc')

    def test_fresh_bash_profile_and_current_shell_guidance(self):
        result = self.install()
        self.assertIn(MARKER, (self.home / '.profile').read_text())
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', result.stderr)
        self.assertIn(str(self.home / '.local/bin/reset-radar'), result.stderr)
        self.shell_version('/bin/bash', '-lc')
        self.shell_version('/bin/bash', '-ic')

    def test_one_line_install_makes_current_shell_immediately_usable(self):
        url = f'http://127.0.0.1:{self.server.server_port}/downloads/cli-v{self.version}/install.sh'
        command = ('curl -fsSL ' + url + ' | sh -s -- --agent codex'
                   ' && export PATH="$HOME/.local/bin:$PATH" && reset-radar --version')
        result = subprocess.run(['/bin/bash', '-c', command], env=self.env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('reset-radar ' + self.version, result.stdout)

    def test_config_directory_is_not_overwritten(self):
        (self.home / '.profile').mkdir()
        result = self.install(code=1)
        self.assertTrue((self.home / '.profile').is_dir())
        self.assertIn('Cannot update shell config', result.stderr)
        self.assertFalse((self.home / '.bashrc').exists())

    def test_unknown_shell_leaves_configs_untouched(self):
        self.env['SHELL'] = '/bin/fish'
        result = self.install()
        self.assertFalse((self.home / '.profile').exists())
        self.assertIn('fish', result.stderr)
        self.assertIn('reset-radar', result.stderr)

    def test_install_failure_does_not_modify_shell_config(self):
        self.env['RESET_RADAR_DOWNLOAD_BASE'] += '/missing'
        self.install(code=1)
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertFalse((self.home / '.profile').exists())


if __name__ == '__main__':
    unittest.main()
