"""How code-server/windows/client.py opens the shell on Windows from WSL; WSL itself is mocked."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / '.devcontainer/claude/code-server/windows'
spec = importlib.util.spec_from_file_location('windows_client', WINDOWS / 'client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def wslpath(option, path):
    """C:\\... for /mnt/c/..., as wslpath -w gives it."""
    assert option == '-w'
    return 'C:\\' + str(path).removeprefix('/mnt/c/').replace('/', '\\')


class WindowsClientTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_workspace_directory_is_per_workspace(self):
        first = client.workspace_directory(self.tmp, self.tmp / 'a')
        self.assertTrue(first.is_relative_to(self.tmp / 'workspaces'))
        self.assertNotEqual(first, client.workspace_directory(self.tmp, self.tmp / 'b'))
        self.assertEqual(first, client.workspace_directory(self.tmp, self.tmp / 'a'))

    def test_host_mode_names_the_repository_not_the_core(self):
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_WORKSPACE': str(self.tmp / 'a')}):
            self.assertEqual(client.workspace_directory(self.tmp), client.workspace_directory(self.tmp, self.tmp / 'a'))

    def test_shell_command_takes_windows_paths(self):
        with mock.patch.object(client, 'wslpath', wslpath):
            cmd = client.shell_command(Path('/mnt/c/d/electron/electron.exe'), Path('/mnt/c/d/w/shell'),
                                       Path('/mnt/c/d/w/profile'), 18087, '/workspace')
        self.assertEqual(cmd, ['/mnt/c/d/electron/electron.exe', 'C:\\d\\w\\shell', '--proxy-port=18087',
                               '--folder=/workspace', '--user-data-dir=C:\\d\\w\\profile'])

    def test_open_passes_the_token_through_wslenv_and_copies_the_shell(self):
        base = self.tmp / 'ClaudeDevcontainer'
        environ = {client.TOKEN: 'secret', 'WSLENV': 'USERPROFILE/p', 'PATH': os.environ['PATH']}
        with mock.patch.object(client, 'base_directory', return_value=base), \
                mock.patch.object(client.electron, 'ensure', return_value=base / 'electron/x') as ensure, \
                mock.patch.object(client, 'wslpath', wslpath), \
                mock.patch.object(client.subprocess, 'Popen') as popen, \
                mock.patch.dict(os.environ, environ, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            client.open_window(18090, '/workspace')
        ensure.assert_called_once_with(base / 'electron', 'win32-x64')
        command, = popen.call_args.args
        options = popen.call_args.kwargs
        self.assertEqual(command[0], str(base / 'electron/x/electron.exe'))
        self.assertIn('--proxy-port=18090', command)
        self.assertEqual(options['env']['WSLENV'], f'USERPROFILE/p:{client.TOKEN}/w')
        self.assertEqual(options['env'][client.TOKEN], 'secret')
        self.assertTrue(options['start_new_session'])
        state = Path(options['cwd'])
        self.assertTrue((state / 'shell/main.js').is_file())
        self.assertTrue((state / 'shell/policy.js').is_file())

    def test_open_needs_the_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SystemExit, client.TOKEN):
                client.open_window(18090, '/workspace')


if __name__ == '__main__':
    unittest.main()
