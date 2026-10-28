"""Linux Electron launcher: direct proxy, profile persistence and process lifecycle."""
import argparse
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
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

    def test_display_selection(self):
        socket = self.tmp / 'wayland-0'
        os.mknod(socket, 0o600 | stat.S_IFSOCK)
        self.assertEqual(client.display_backend({'WAYLAND_DISPLAY': str(socket), 'DISPLAY': ':0'}),
                         ('wayland', str(socket)))
        self.assertEqual(client.display_backend({'DISPLAY': ':0'}), ('x11', ':0'))
        self.assertEqual(client.display_backend({'WAYLAND_DISPLAY': '', 'DISPLAY': 'localhost:10.0'}),
                         ('x11', 'localhost:10.0'))
        with self.assertRaisesRegex(SystemExit, 'Wayland or X11 session'):
            client.display_backend({})
        # A broken explicit Wayland selection is reported instead of silently changing backends.
        with self.assertRaisesRegex(SystemExit, 'WAYLAND_DISPLAY'):
            client.display_backend({'WAYLAND_DISPLAY': str(self.tmp / 'missing'), 'DISPLAY': ':0'})

    def test_x11_launch_preserves_display_auth_and_input_method(self):
        state = self.tmp / 'state'
        with client.take_lock(state):
            args = argparse.Namespace(proxy_port=18123, folder='/workspace', backend='x11')
            env = {'DISPLAY': 'localhost:10.0', 'XAUTHORITY': '/tmp/xauth',
                   'XMODIFIERS': '@im=fcitx', 'GTK_IM_MODULE': 'fcitx', client.TOKEN: 'test-token'}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(client, 'ensure_electron', return_value=self.tmp / 'electron'),
                  mock.patch.object(client.subprocess, 'Popen') as popen):
                popen.return_value.wait.return_value = 0
                popen.return_value.poll.return_value = 0
                self.assertEqual(client.open_window(args, state), 0)
            command = popen.call_args.args[0]
            self.assertIn('--ozone-platform=x11', command)
            self.assertFalse(any('wayland' in arg.lower() for arg in command))
            self.assertNotIn('--no-sandbox', command)
            for name, value in env.items():
                self.assertEqual(popen.call_args.kwargs['env'][name], value)

    def test_state_directory_is_per_workspace(self):
        environ = {'XDG_STATE_HOME': str(self.tmp)}
        first = client.state_directory(self.tmp / 'a', environ)
        second = client.state_directory(self.tmp / 'b', environ)
        self.assertTrue(first.is_relative_to(self.tmp / 'claude-devcontainer/vscode'))
        self.assertNotEqual(first, second)
        self.assertEqual(first, client.state_directory(self.tmp / 'a', environ))

    def test_host_mode_names_the_repository_not_the_core(self):
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_WORKSPACE': str(self.tmp / 'repo')}):
            self.assertEqual(client.workspace_root(), self.tmp / 'repo')

    def test_shell_command_uses_host_proxy_and_existing_profile(self):
        cmd = client.shell_command(self.tmp / 'electron/electron', self.tmp / 'shell',
                                   self.tmp / 'home/shell', 18123, '/workspace', 'wayland')
        self.assertEqual(cmd[:2], [str(self.tmp / 'electron/electron'), str(self.tmp / 'shell')])
        self.assertIn('--proxy-port=18123', cmd)
        self.assertIn('--folder=/workspace', cmd)
        self.assertIn(f'--user-data-dir={self.tmp}/home/shell', cmd)
        self.assertIn('--ozone-platform=wayland', cmd)
        self.assertIn('--wayland-text-input-version=3', cmd)
        self.assertNotIn('--no-sandbox', cmd)

    def test_electron_override_by_path_or_command(self):
        electron = self.tmp / 'bin/electron'
        electron.parent.mkdir()
        electron.write_text('#!/bin/sh\n')
        electron.chmod(0o755)
        self.assertIsNone(client.electron_override({}))
        self.assertEqual(client.electron_override({client.ELECTRON: str(electron)}), electron)
        self.assertEqual(client.electron_override({client.ELECTRON: 'electron', 'PATH': str(electron.parent)}),
                         electron)
        with self.assertRaisesRegex(SystemExit, client.ELECTRON):
            client.electron_override({client.ELECTRON: str(self.tmp / 'missing')})

    def test_override_skips_the_download(self):
        electron = self.tmp / 'electron'
        electron.write_text('#!/bin/sh\n')
        electron.chmod(0o755)
        with (mock.patch.dict(os.environ, {client.ELECTRON: str(electron)}),
              mock.patch.object(client, 'electron_module', side_effect=AssertionError('downloaded'))):
            self.assertEqual(client.ensure_electron(), electron)

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

    def test_prepare_needs_neither_token_nor_display(self):
        with (mock.patch.dict(os.environ, {}, clear=True),
              mock.patch.object(sys, 'argv', ['client.py', '--prepare']),
              mock.patch.object(client, 'ensure_electron', return_value=self.tmp) as ensure,
              mock.patch.object(client, 'report') as report):
            client.main()
        ensure.assert_called_once_with()
        report.assert_called_once_with(self.tmp, 'not set')

    def test_lock_rejects_second_launcher(self):
        with client.take_lock(self.tmp):
            with self.assertRaisesRegex(SystemExit, 'already open'):
                client.take_lock(self.tmp)

    def test_launch_passes_proxy_and_keeps_profile(self):
        state = self.tmp / 'state'
        with client.take_lock(state):
            profile = state / 'home/shell'
            profile.mkdir()
            (profile / 'existing').write_text('settings')
            args = argparse.Namespace(proxy_port=18123, folder='/workspace/project', backend='wayland')
            with (mock.patch.object(client, 'ensure_electron', return_value=self.tmp / 'electron'),
                  mock.patch.object(client.subprocess, 'Popen') as popen,
                  mock.patch.dict(os.environ, {client.TOKEN: 'test-token', 'ELECTRON_RUN_AS_NODE': '1',
                                               'NODE_OPTIONS': '--require bad', 'DISPLAY': ':0'})):
                popen.return_value.wait.return_value = 0
                popen.return_value.poll.return_value = 0
                self.assertEqual(client.open_window(args, state), 0)
            command = popen.call_args.args[0]
            env = popen.call_args.kwargs['env']
            self.assertIn('--proxy-port=18123', command)
            self.assertIn('--folder=/workspace/project', command)
            self.assertIn(f'--user-data-dir={profile}', command)
            self.assertEqual(env[client.TOKEN], 'test-token')
            for name in ('ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'DISPLAY'):
                self.assertNotIn(name, env)
            self.assertEqual((profile / 'existing').read_text(), 'settings')
            self.assertFalse(Path(command[1]).exists(), 'temporary shell copy is removed after exit')

    def test_detached_launcher_logs_and_cleans_up(self):
        electron = self.tmp / 'electron'
        electron.write_text('#!/bin/sh\necho launched\nsleep 0.4\n')
        electron.chmod(0o755)
        wrapper = self.tmp / 'launch.py'
        wrapper.write_text(
            'import importlib.util\nfrom pathlib import Path\n'
            f'spec = importlib.util.spec_from_file_location("client", {str(LINUX / "client.py")!r})\n'
            'client = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(client)\n'
            f'client.ensure_electron = lambda: Path({str(electron)!r})\n'
            'client.display_backend = lambda: ("x11", ":0")\nclient.main()\n')
        env = os.environ | {'XDG_STATE_HOME': str(self.tmp / 'state'), client.TOKEN: 'test-token'}
        result = subprocess.run([sys.executable, str(wrapper), '--detach', '--proxy-port', '18123'],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('client.log', result.stdout)
        state = client.state_directory(environ=env)
        for _ in range(100):
            try:
                lock = client.take_lock(state)
            except SystemExit:
                time.sleep(0.02)
            else:
                lock.close()
                break
        else:
            self.fail('launcher did not release its lock')
        self.assertIn('launched', (state / 'client.log').read_text())


if __name__ == '__main__':
    unittest.main()
