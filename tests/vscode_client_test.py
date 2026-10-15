"""The Linux client's sandbox (code-server/linux/client.py); bwrap on the host runs the probe."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / '.devcontainer/claude/code-server/linux'
spec = importlib.util.spec_from_file_location('client', LINUX / 'client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_find_code_prefers_the_setting_and_resolves_links(self):
        real = self.tmp / 'opt/code'
        real.parent.mkdir()
        real.write_text('#!/bin/sh\n')
        real.chmod(0o755)
        link = self.tmp / 'code'
        link.symlink_to(real)
        self.assertEqual(client.find_code(str(link), which=lambda name: None), real.resolve())
        self.assertEqual(client.find_code('', which=lambda name: str(link) if name == 'code' else None), real.resolve())
        with self.assertRaisesRegex(SystemExit, 'codeServer.vscode: .* not an executable file'):
            client.find_code(str(self.tmp / 'missing'), which=lambda name: None)
        with self.assertRaisesRegex(SystemExit, 'not on PATH'):
            client.find_code('', which=lambda name: None)

    def test_wayland_socket_by_name_or_path(self):
        os.mknod(self.tmp / 'wayland-9', 0o600 | stat.S_IFSOCK)
        environ = {'XDG_RUNTIME_DIR': str(self.tmp), 'WAYLAND_DISPLAY': 'wayland-9'}
        self.assertEqual(client.wayland_socket(environ), self.tmp / 'wayland-9')
        environ = {'WAYLAND_DISPLAY': str(self.tmp / 'wayland-9')}
        self.assertEqual(client.wayland_socket(environ), self.tmp / 'wayland-9')
        (self.tmp / 'wayland-9').unlink()
        with self.assertRaisesRegex(SystemExit, 'Wayland session is required'):
            client.wayland_socket({'XDG_RUNTIME_DIR': str(self.tmp), 'WAYLAND_DISPLAY': 'wayland-9'})

    def test_sandbox_shares_nothing_of_the_host_but_the_system(self):
        home, runtime = self.tmp / 'home', self.tmp / 'runtime'
        wayland = self.tmp / 'wayland-0'
        environ = {'LANG': 'ja_JP.UTF-8', 'DISPLAY': ':0', 'SSH_AUTH_SOCK': '/tmp/ssh', 'HOME': '/home/me'}
        cmd = client.sandbox('/usr/bin/bwrap', Path('/usr/bin/code'), home, runtime, wayland, environ)
        text = ' '.join(cmd)
        for flag in ('--unshare-net', '--unshare-user', '--unshare-pid', '--cap-drop ALL', '--clearenv', '--die-with-parent'):
            self.assertIn(flag, text)
        writable = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == '--bind']
        self.assertEqual(writable, [str(home)])
        shared = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == '--ro-bind' and not cmd[i + 1].startswith(str(self.tmp))]
        self.assertTrue(shared and all(path in client.SYSTEM for path in shared), shared)
        self.assertIn(f'--ro-bind {runtime}/etc/passwd /etc/passwd', text)
        self.assertIn(f'--bind {home} /home/vscode', text)
        self.assertIn('--ro-bind ' + str(wayland) + ' /run/user/' + str(os.getuid()) + '/wayland-0', text)
        self.assertIn('--setenv LANG ja_JP.UTF-8', text)
        for name in ('DISPLAY', 'SSH_AUTH_SOCK', 'DBUS_SESSION_BUS_ADDRESS unix:path=/run'):
            self.assertNotIn('--setenv ' + name, text)
        self.assertNotIn('wayland', ' '.join(client.sandbox('/usr/bin/bwrap', Path('/usr/bin/code'), home, runtime, None, environ)))

    def test_sandbox_refuses_a_vscode_in_the_home(self):
        code = Path.home() / '.local/share/code/bin/code'
        with self.assertRaisesRegex(SystemExit, 'home directory'):
            client.sandbox('/usr/bin/bwrap', code, self.tmp, self.tmp, None, {})

    def test_write_etc_names_the_user_and_localhost(self):
        client.write_etc(self.tmp / 'etc')
        passwd = (self.tmp / 'etc/passwd').read_text()
        self.assertIn(f':x:{os.getuid()}:{os.getgid()}:', passwd)
        self.assertIn(':/home/vscode:', passwd)
        self.assertIn('127.0.0.1 localhost', (self.tmp / 'etc/hosts').read_text())

    def test_state_directory_is_per_workspace(self):
        environ = {'XDG_STATE_HOME': str(self.tmp)}
        first = client.state_directory(self.tmp / 'a', environ)
        second = client.state_directory(self.tmp / 'b', environ)
        self.assertTrue(first.is_relative_to(self.tmp / 'claude-devcontainer/vscode'))
        self.assertNotEqual(first, second)
        self.assertEqual(first, client.state_directory(self.tmp / 'a', environ))

    def test_code_command_is_remote_and_wayland(self):
        cmd = client.code_command(Path('/usr/bin/code'), '/workspace')
        self.assertIn('--folder-uri=vscode-remote://claude-devcontainer+agent/workspace', cmd)
        self.assertIn('--enable-proposed-api=claude-devcontainer.agent-resolver', cmd)
        self.assertIn('--ozone-platform=wayland', cmd)
        self.assertIn('--user-data-dir=/home/vscode/user-data', cmd)

    def test_prepare_home_installs_the_resolver_and_initial_settings_once(self):
        home = self.tmp / 'home'
        client.prepare_home(home)
        package = json.loads((LINUX / 'resolver/package.json').read_text())
        self.assertEqual(f"{package['publisher']}.{package['name']}", client.EXTENSION)
        installed = home / 'extensions' / f"{client.EXTENSION}-{package['version']}"
        self.assertTrue((installed / 'extension.js').is_file())
        self.assertIn('resolvers', package['enabledApiProposals'])
        settings = home / 'user-data/User/settings.json'
        settings.write_text('{"custom": true}\n')
        client.prepare_home(home)
        self.assertEqual(json.loads(settings.read_text()), {'custom': True})

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
    def test_bwrap_probe_reaches_only_the_relay(self):
        """The real sandbox: no network, no home, and the relay ends at the given host port."""
        echo = socket.create_server(('127.0.0.1', 0))
        self.addCleanup(echo.close)

        def serve():
            try:
                conn, _ = echo.accept()
            except OSError:
                return
            with conn:
                conn.sendall(conn.makefile('rb').readline())
        threading.Thread(target=serve, daemon=True).start()
        state, runtime = self.tmp / 'state', self.tmp / 'runtime'
        runtime.mkdir(mode=0o700)
        env = os.environ | {'XDG_STATE_HOME': str(state), 'XDG_RUNTIME_DIR': str(runtime)}
        result = subprocess.run([sys.executable, str(LINUX / 'client.py'), '--probe', '--code', shutil.which('true'),
                                 '--proxy-port', str(echo.getsockname()[1])], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertTrue(found['own_network_namespace'])
        self.assertNotEqual(found['outside'], 'connected')
        self.assertEqual(found['home_entries'], ['vscode'])
        self.assertEqual(found['relay'], 'probe')
        self.assertTrue((client.state_directory(environ=env) / 'home/extensions').is_dir())
        self.assertEqual(list(runtime.iterdir()), [])

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
    def test_detach_returns_once_started_and_logs(self):
        state, runtime = self.tmp / 'state', self.tmp / 'runtime'
        runtime.mkdir(mode=0o700)
        env = os.environ | {'XDG_STATE_HOME': str(state), 'XDG_RUNTIME_DIR': str(runtime)}
        # Nothing listens on the port: the probe still runs, with relay=''.
        result = subprocess.run([sys.executable, str(LINUX / 'client.py'), '--probe', '--detach',
                                 '--code', shutil.which('true'), '--proxy-port', '1'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('client.log', result.stdout)
        log = client.state_directory(environ=env) / 'client.log'
        for _ in range(100):
            if 'own_network_namespace' in log.read_text():
                break
            time.sleep(0.1)
        self.assertIn('"home_entries": ["vscode"]', log.read_text())


if __name__ == '__main__':
    unittest.main()
