"""How code-server/linux/server.py puts the host's VS Code's server into the agent; no Docker needed."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / '.devcontainer/claude/code-server/linux'
spec = importlib.util.spec_from_file_location('server', LINUX / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
COMMIT = '8b640eef5a6c6089c029249d48efa5c99adf7d51'


class FakeCompose:
    """compose.sh as a list of calls; `exec test -e` answers from `present`."""
    def __init__(self, present=()):
        self.present = set(present)
        self.calls = []

    def __call__(self, *args, input=None, check=True, capture=False):
        self.calls.append((args, input))
        code = 0
        if args[:4] == ('exec', '-T', 'agent', 'test'):
            code = 0 if args[5] in self.present else 1
        return subprocess.CompletedProcess(args, code, '', '')


class ServerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_server_url_names_the_commit_and_architecture(self):
        self.assertEqual(server.server_url(COMMIT, 'x64'),
                         f'https://update.code.visualstudio.com/commit:{COMMIT}/server-linux-x64/stable')
        self.assertEqual(server.server_url(COMMIT, 'arm64'),
                         f'https://update.code.visualstudio.com/commit:{COMMIT}/server-linux-arm64/stable')
        with self.assertRaisesRegex(SystemExit, 'no Linux server'):
            server.server_url(COMMIT, 'armhf')

    def test_ensure_server_downloads_once(self):
        fetched = []

        def fetch(url, target):
            fetched.append(url)
            (target / 'bin').mkdir(parents=True)
            (target / 'bin/code-server').write_text('')
        path = server.ensure_server(self.tmp, COMMIT, 'x64', fetch)
        self.assertEqual(path, self.tmp / COMMIT / 'server')
        self.assertEqual(server.ensure_server(self.tmp, COMMIT, 'x64', fetch), path)
        self.assertEqual(fetched, [server.server_url(COMMIT, 'x64')])

    def test_ensure_extensions_installs_a_changed_list_with_the_server_cli(self):
        cli = self.tmp / 'server/bin/code-server'
        cli.parent.mkdir(parents=True)
        runs = []

        def run(cmd, **kwargs):
            runs.append(cmd)
            listed = 'ms-python.python@2026.1.0\nvscodevim.vim@1.27.3\n' if '--list-extensions' in cmd else ''
            return subprocess.CompletedProcess(cmd, 0, listed, '')
        ids = ['ms-python.python', 'vscodevim.vim@1.27.3']
        path = server.ensure_extensions(self.tmp, cli.parent.parent, ids, run)
        self.assertEqual(path, self.tmp / 'extensions' / server.extensions_digest(ids))
        self.assertTrue(path.is_dir())
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0][:1], [str(cli)])
        self.assertIn('--install-extension', runs[0])
        self.assertEqual(runs[0][runs[0].index('--install-extension') + 1], 'ms-python.python')
        self.assertIn('--extensions-dir', runs[0])
        self.assertEqual((path / '.installed').read_text(), ' '.join(ids) + '\n')
        self.assertEqual(server.ensure_extensions(self.tmp, cli.parent.parent, ids, run), path)
        self.assertEqual(len(runs), 2)
        # An interrupted installation has no marker and is redone.
        (path / '.installed').unlink()
        self.assertEqual(server.ensure_extensions(self.tmp, cli.parent.parent, ids, run), path)
        self.assertEqual(len(runs), 4)
        with self.assertRaisesRegex(SystemExit, 'vscodevim.vim@1.0.0 was not installed'):
            server.ensure_extensions(self.tmp, cli.parent.parent, ['vscodevim.vim@1.0.0'], run)
        empty = server.ensure_extensions(self.tmp, cli.parent.parent, [], run)
        self.assertTrue((empty / '.installed').is_file())
        self.assertEqual(len(runs), 6)

    def test_deploy_copies_whole_directories_once_as_root(self):
        compose = FakeCompose(present=['/opt/vscode-server/' + COMMIT])
        server.deploy(compose, self.tmp / 'server', '/opt/vscode-server/' + COMMIT)
        self.assertEqual([c[0][0] for c in compose.calls], ['exec'])
        server.deploy(compose, self.tmp / 'ext', '/opt/vscode-server/extensions-abc')
        self.assertEqual([c[0] for c in compose.calls[1:]], [
            ('exec', '-T', 'agent', 'test', '-e', '/opt/vscode-server/extensions-abc'),
            ('exec', '-T', 'agent', 'rm', '-rf', '/opt/vscode-server/extensions-abc.partial'),
            ('cp', str(self.tmp / 'ext'), 'agent:/opt/vscode-server/extensions-abc.partial'),
            ('exec', '-T', 'agent', 'mv', '/opt/vscode-server/extensions-abc.partial', '/opt/vscode-server/extensions-abc')])

    def test_new_token_goes_to_the_agent_and_the_client(self):
        compose = FakeCompose()
        token = server.new_token(compose, self.tmp / 'state')
        self.assertEqual(len(token), 32)
        self.assertEqual((self.tmp / 'state/token').read_text(), token + '\n')
        self.assertEqual(oct((self.tmp / 'state/token').stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct((self.tmp / 'state').stat().st_mode & 0o777), '0o700')
        (args, stdin), = compose.calls
        self.assertEqual(args[:5], ('exec', '-T', '-u', 'node', 'agent'))
        self.assertIn(server.TOKEN_FILE, args[-1])
        self.assertEqual(stdin, token + '\n')

    def test_start_runs_the_server_as_node_with_the_token(self):
        compose = FakeCompose()
        server.start(compose, COMMIT, '/opt/vscode-server/extensions-abc')
        (args, _), = compose.calls
        self.assertEqual(args[:7], ('exec', '-d', '-u', 'node', '-e', 'HOME=/home/node', 'agent'))
        script = args[-1]
        self.assertIn(f'/opt/vscode-server/{COMMIT}/bin/code-server --host 0.0.0.0 --port 8089', script)
        self.assertIn('--connection-token-file ' + server.TOKEN_FILE, script)
        self.assertIn('cp -r /opt/vscode-server/extensions-abc /home/node/.vscode-server/extensions', script)
        # Anchored, so that it does not match the shell that runs it.
        self.assertIn("pkill -f '^/opt/vscode-server/'", script)


if __name__ == '__main__':
    unittest.main()
