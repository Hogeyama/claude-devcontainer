"""The Linux client's sandbox (code-server/linux/client.py); bwrap on the host runs the probe."""
import argparse
import asyncio
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
from unittest import mock

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
        home, runtime, electron = self.tmp / 'home', self.tmp / 'runtime', self.tmp / 'electron'
        wayland = self.tmp / 'wayland-0'
        environ = {'LANG': 'ja_JP.UTF-8', 'DISPLAY': ':0', 'SSH_AUTH_SOCK': '/tmp/ssh', 'HOME': '/home/me'}
        cmd = client.sandbox('/usr/bin/bwrap', electron, home, runtime, wayland, environ)
        text = ' '.join(cmd)
        for flag in ('--unshare-net', '--unshare-user', '--unshare-pid', '--cap-drop ALL', '--clearenv', '--die-with-parent'):
            self.assertIn(flag, text)
        writable = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == '--bind']
        self.assertEqual(writable, [str(home)])
        shared = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == '--ro-bind' and not cmd[i + 1].startswith(str(self.tmp))]
        self.assertTrue(shared and all(path in client.SYSTEM for path in shared), shared)
        self.assertIn(f'--ro-bind {runtime}/etc/passwd /etc/passwd', text)
        self.assertIn(f'--bind {home} /home/vscode', text)
        self.assertIn(f'--ro-bind {electron} /opt/electron', text)
        self.assertIn('--ro-bind ' + str(wayland) + ' /run/user/' + str(os.getuid()) + '/wayland-0', text)
        self.assertIn('--setenv LANG ja_JP.UTF-8', text)
        for name in ('DISPLAY', 'SSH_AUTH_SOCK', 'DBUS_SESSION_BUS_ADDRESS unix:path=/run'):
            self.assertNotIn('--setenv ' + name, text)
        bare = ' '.join(client.sandbox('/usr/bin/bwrap', None, home, runtime, None, environ))
        self.assertNotIn('wayland', bare)
        self.assertNotIn('/opt/electron', bare)

    def test_sandbox_refuses_a_python_in_the_home(self):
        with mock.patch.object(client.sys, 'base_prefix', str(Path.home() / '.local/python')):
            with self.assertRaisesRegex(SystemExit, 'home directory'):
                client.sandbox('/usr/bin/bwrap', None, self.tmp, self.tmp, None, {})

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

    def test_shell_command_uses_the_relay_and_wayland(self):
        cmd = client.shell_command('/workspace')
        self.assertEqual(cmd[:2], ['/opt/electron/electron', '/run/code-server/shell'])
        self.assertIn('--proxy-port=18086', cmd)
        self.assertIn('--folder=/workspace', cmd)
        self.assertIn('--user-data-dir=/home/vscode/shell', cmd)
        self.assertIn('--ozone-platform=wayland', cmd)
        self.assertIn('--wayland-text-input-version=3', cmd)
        self.assertNotIn('--no-sandbox', cmd)

    def test_userns_restriction_is_read_from_the_sysctl(self):
        sysctl = self.tmp / 'apparmor_restrict_unprivileged_userns'
        sysctl.write_text('1\n')
        self.assertTrue(client.userns_restricted(sysctl))
        sysctl.write_text('0\n')
        self.assertFalse(client.userns_restricted(sysctl))
        self.assertFalse(client.userns_restricted(self.tmp / 'missing'))

    def test_open_needs_the_token(self):
        env = {name: value for name, value in os.environ.items() if name != client.TOKEN}
        env['XDG_STATE_HOME'] = str(self.tmp / 'state')
        result = subprocess.run([sys.executable, str(LINUX / 'client.py'), '--proxy-port', '1'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(client.TOKEN, result.stderr)

    def test_diagnostic_modes_do_not_need_the_token(self):
        for mode in ('check', 'prepare', 'probe'):
            with self.subTest(mode=mode):
                args = argparse.Namespace(check=False, prepare=False, probe=False)
                setattr(args, mode, True)
                with (mock.patch.dict(os.environ, {}, clear=True),
                      mock.patch.object(client.shutil, 'which', return_value=None)):
                    with self.assertRaisesRegex(SystemExit, 'bubblewrap'):
                        asyncio.run(client.outside(args))

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
        result = subprocess.run([sys.executable, str(LINUX / 'client.py'), '--probe',
                                 '--proxy-port', str(echo.getsockname()[1])], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertTrue(found['own_network_namespace'])
        self.assertNotEqual(found['outside'], 'connected')
        self.assertEqual(found['home_entries'], ['vscode'])
        self.assertEqual(found['relay'], 'probe')
        self.assertTrue((client.state_directory(environ=env) / 'home').is_dir())
        self.assertEqual(list(runtime.iterdir()), [])

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
    def test_detach_returns_once_started_and_logs(self):
        state, runtime = self.tmp / 'state', self.tmp / 'runtime'
        runtime.mkdir(mode=0o700)
        env = os.environ | {'XDG_STATE_HOME': str(state), 'XDG_RUNTIME_DIR': str(runtime)}
        # Nothing listens on the port: the probe still runs, with relay=''.
        result = subprocess.run([sys.executable, str(LINUX / 'client.py'), '--probe', '--detach', '--proxy-port', '1'],
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
