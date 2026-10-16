"""Git's real ext transport, with a local Git server standing in for Docker."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.devcontainer/claude/scripts/git-agent.py'


class GitAgentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = self.root / 'host % workspace'
        self.host.mkdir()
        self.server = self.root / 'agent.git'
        config = self.root / 'gitconfig'
        config.write_text('[user]\n name = fixture\n email = fixture@example.com\n')
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM='1',
                        FAKE_AGENT_REPO=str(self.server), FAKE_DOCKER_LOG=str(self.root / 'calls'),
                        FAKE_CONTAINERS='abc123\n')
        self.inspect = {'Config': {'Labels': {'com.docker.compose.project': 'fixture',
                                             'dev.claude-devcontainer.git-isolation': 'clone'}},
                        'Mounts': [{'Destination': '/workspace', 'Type': 'volume',
                                    'Name': 'fixture_workspace', 'RW': True}]}
        self.env['FAKE_INSPECT'] = json.dumps([self.inspect])
        self.git('init', '-b', 'main')
        self.git('commit', '--allow-empty', '-m', 'local')
        self.git('clone', '--bare', str(self.host), str(self.server))
        self.transport = self.host / '.devcontainer/claude/scripts/git-agent.py'
        self.transport.parent.mkdir(parents=True)
        shutil.copy(SCRIPT, self.transport)
        binary = self.root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'ps':
    print(os.environ['FAKE_CONTAINERS'], end='')
elif args[0] == 'inspect':
    print(os.environ['FAKE_INSPECT'])
elif args[:4] == ['exec', '-i', '--user', 'node'] and args[-1] == '/workspace':
    command = args[5:-1]
    os.execvp(command[0], [*command, os.environ['FAKE_AGENT_REPO']])
else:
    sys.exit('unexpected Docker invocation')
''')
        docker.chmod(0o755)
        self.env['PATH'] = str(binary) + ':' + self.env['PATH']

    def git(self, *args, check=True):
        return subprocess.run(['git', '-C', str(self.host), *args], env=self.env,
                              text=True, capture_output=True, check=check)

    def register(self, check=True):
        return subprocess.run([sys.executable, str(self.transport), 'register'], env=self.env,
                              text=True, capture_output=True, check=check)

    def test_registration_is_automatic_and_idempotent_without_origin(self):
        self.register()
        config = (self.host / '.git/config').read_text()
        self.register()
        self.assertEqual((self.host / '.git/config').read_text(), config)
        self.assertEqual(self.git('remote').stdout.strip(), 'agent')
        self.assertEqual(self.git('config', 'protocol.ext.allow').stdout.strip(), 'user')

    def test_submodules_cannot_run_commands_through_ext(self):
        # A .gitmodules merged from the agent can name a command as a URL.
        self.register()
        marker = self.root / 'submodule-ran'
        url = 'ext::sh -c touch% ' + str(marker).replace('%', '%%').replace(' ', '% ')
        self.git('config', '-f', '.gitmodules', 'submodule.probe.path', 'probe')
        self.git('config', '-f', '.gitmodules', 'submodule.probe.url', url)
        head = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{head},probe')
        self.git('add', '-f', '.gitmodules')
        self.git('commit', '-m', 'submodule')
        result = self.git('submodule', 'update', '--init', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("transport 'ext' not allowed", result.stderr)
        self.assertFalse(marker.exists())

    def test_registration_preserves_existing_remotes_and_rejects_collision(self):
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        self.git('remote', 'add', 'agent', 'https://elsewhere.example/repo.git')
        before = (self.host / '.git/config').read_bytes()
        result = self.register(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('agent', result.stderr)
        self.assertEqual((self.host / '.git/config').read_bytes(), before)

    def test_fetch_and_push_use_git_protocol_as_node(self):
        self.register()
        self.git('fetch', 'agent')
        self.assertEqual(self.git('rev-parse', 'agent/main').stdout, self.git('rev-parse', 'HEAD').stdout)
        self.git('commit', '--allow-empty', '-m', 'host update')
        self.git('push', 'agent', 'HEAD:refs/heads/host/update')
        result = subprocess.run(['git', '-C', str(self.server), 'rev-parse', 'host/update'],
                                check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout, self.git('rev-parse', 'HEAD').stdout)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertTrue(any(call[-2:] == ['git-upload-pack', '/workspace'] for call in calls))
        self.assertTrue(any(call[-2:] == ['receive-pack', '/workspace'] for call in calls))
        self.assertTrue(all(call[:4] == ['exec', '-i', '--user', 'node'] for call in calls if call[0] == 'exec'))
        self.assertTrue(any('label=devcontainer.local_folder=' + str(self.host) in call for call in calls))

    def test_push_to_checked_out_branch_keeps_uncommitted_work(self):
        self.register()
        agent = self.root / 'agent'
        def agent_git(*args):
            return subprocess.run(['git', '-C', str(agent), *args], env=self.env,
                                  text=True, capture_output=True, check=True)
        subprocess.run(['git', 'clone', str(self.server), str(agent)], env=self.env, check=True, capture_output=True)
        self.env['FAKE_AGENT_REPO'] = str(agent)
        (agent / 'work').write_text('agent\n')
        agent_git('add', 'work')
        agent_git('commit', '-m', 'agent work')
        dropped = agent_git('rev-parse', 'HEAD').stdout
        self.git('commit', '--amend', '--allow-empty', '-m', 'rewritten on the host')
        (agent / 'work').write_text('uncommitted\n')
        result = self.git('push', '--force', 'agent', 'HEAD:main', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unstaged changes', result.stderr)
        self.assertEqual((agent / 'work').read_text(), 'uncommitted\n')
        agent_git('checkout', '--', 'work')
        self.git('push', '--force', 'agent', 'HEAD:main')
        self.assertEqual(agent_git('rev-parse', 'HEAD').stdout, self.git('rev-parse', 'HEAD').stdout)
        self.assertFalse((agent / 'work').exists())
        self.assertEqual(agent_git('rev-parse', 'main@{1}').stdout, dropped)

    def test_fetch_refuses_missing_or_ambiguous_container(self):
        self.register()
        for containers in ('', 'abc123\ndef456\n'):
            self.env['FAKE_CONTAINERS'] = containers
            result = self.git('fetch', 'agent', check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('running agent container', result.stderr)

    def test_linked_worktrees_and_subdirectories_select_their_own_container(self):
        self.register()
        linked = self.root / 'linked % worktree'
        self.git('worktree', 'add', '-b', 'linked', str(linked))
        transport = linked / '.devcontainer/claude/scripts/git-agent.py'
        transport.parent.mkdir(parents=True)
        shutil.copy(SCRIPT, transport)
        subprocess.run([sys.executable, str(transport), 'register'], env=self.env,
                       check=True, capture_output=True, text=True)
        for workspace in (linked, self.host):
            subdirectory = workspace / 'nested'
            subdirectory.mkdir()
            subprocess.run(['git', '-C', str(subdirectory), 'fetch', 'agent'], env=self.env,
                           check=True, capture_output=True, text=True)
            calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
            discovery = [call for call in calls if call[0] == 'ps'][-1]
            self.assertIn('label=devcontainer.local_folder=' + str(workspace), discovery)

    def test_unknown_service_is_rejected_before_docker(self):
        result = subprocess.run([sys.executable, str(self.transport), 'sh'], env=self.env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'calls').exists())

    def test_transport_checks_running_mode_and_mount_before_git(self):
        self.register()
        for mode, kind, name, allowed in [('shared', 'bind', '', False),
                                           ('clone', 'bind', '', False),
                                           ('unknown', 'volume', 'fixture_workspace', False),
                                           (None, 'bind', '', False),
                                           (None, 'volume', 'other_workspace', False),
                                           (None, 'volume', 'fixture_workspace', True),
                                           ('clone', 'volume', 'fixture_workspace', True)]:
            with self.subTest(mode=mode, kind=kind, name=name):
                labels = self.inspect['Config']['Labels']
                labels.pop('dev.claude-devcontainer.git-isolation', None)
                if mode is not None:
                    labels['dev.claude-devcontainer.git-isolation'] = mode
                self.inspect['Mounts'][0].update(Type=kind, Name=name)
                self.env['FAKE_INSPECT'] = json.dumps([self.inspect])
                # A TOML edit for the next container must not affect this one.
                (self.host / '.devcontainer/claude.local.toml').write_text('[git]\nisolation = "shared"\n')
                for args in [('fetch', 'agent'), ('push', 'agent', 'HEAD:probe')]:
                    (self.root / 'calls').write_text('')
                    result = self.git(*args, check=False)
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)
                    calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
                    self.assertEqual(any(call[0] == 'exec' for call in calls), allowed)

    def use_shared(self):
        """The running container's view in shared mode: the host tree bound, its own Git volume."""
        self.inspect['Config']['Labels']['dev.claude-devcontainer.git-isolation'] = 'shared'
        self.inspect['Mounts'] = [
            {'Destination': '/workspace', 'Type': 'bind', 'Source': str(self.host), 'RW': True},
            {'Destination': '/workspace/.git', 'Type': 'volume', 'Name': 'fixture_agent-git', 'RW': True}]
        self.env['FAKE_INSPECT'] = json.dumps([self.inspect])

    def test_unsafe_shared_refuses_remote_exchange(self):
        self.register()
        self.use_shared()
        self.inspect['Config']['Labels']['dev.claude-devcontainer.git-isolation'] = 'unsafe_shared'
        self.inspect['Mounts'][1] = {'Destination': '/workspace/.git', 'Type': 'bind',
                                    'Source': str(self.host / '.git'), 'RW': True}
        self.env['FAKE_INSPECT'] = json.dumps([self.inspect])
        for args in (('fetch', 'agent'), ('push', 'agent', 'HEAD:probe')):
            result = self.git(*args, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('uses the host Git directly', result.stderr)

    def test_shared_serves_fetch_but_refuses_push(self):
        self.register()
        self.use_shared()
        self.git('fetch', 'agent')
        self.assertEqual(self.git('rev-parse', 'agent/main').stdout, self.git('rev-parse', 'HEAD').stdout)
        (self.root / 'calls').write_text('')
        result = self.git('push', 'agent', 'HEAD:refs/heads/host/update', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/run/host/git', result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertFalse(any(call[0] == 'exec' for call in calls))

    def test_shared_mounts_are_verified_before_git(self):
        self.register()
        for change in [{'Destination': '/workspace/.git', 'Type': 'bind', 'Source': str(self.host / '.git'), 'RW': True},
                       {'Destination': '/workspace/.git', 'Type': 'volume', 'Name': 'other_agent-git', 'RW': True},
                       {'Destination': '/workspace', 'Type': 'bind', 'Source': str(self.root), 'RW': True},
                       {'Destination': '/workspace', 'Type': 'volume', 'Name': 'fixture_workspace', 'RW': True}]:
            with self.subTest(change=change):
                self.use_shared()
                self.inspect['Mounts'] = [m for m in self.inspect['Mounts'] if m['Destination'] != change['Destination']] + [change]
                self.env['FAKE_INSPECT'] = json.dumps([self.inspect])
                result = self.git('fetch', 'agent', check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('shared mounts', result.stderr)

    def test_shared_fetch_and_mixed_reset_preserve_files(self):
        self.register()
        self.use_shared()
        agent = self.root / 'agent'
        subprocess.run(['git', 'clone', '-q', str(self.server), str(agent)], env=self.env, check=True)
        self.env['FAKE_AGENT_REPO'] = str(agent)
        # The agent edits the shared tree, commits part of it, and leaves the rest.
        (self.host / 'done').write_text('committed by the agent\n')
        (self.host / 'extra').write_text('left in the tree\n')
        (agent / 'done').write_text('committed by the agent\n')
        subprocess.run(['git', '-C', str(agent), 'switch', '-qc', 'agent/task'], env=self.env, check=True)
        subprocess.run(['git', '-C', str(agent), 'add', 'done'], env=self.env, check=True)
        subprocess.run(['git', '-C', str(agent), 'commit', '-qm', 'agent work'], env=self.env, check=True)
        self.git('fetch', '--no-tags', 'agent', 'refs/heads/agent/task')
        commit = self.git('rev-parse', 'FETCH_HEAD').stdout.strip()
        # Mixed reset changes only the host's Git; the container may already be gone.
        self.env['FAKE_CONTAINERS'] = ''
        self.git('diff', '--cached', '--quiet')
        self.git('merge-base', '--is-ancestor', 'HEAD', commit)
        self.git('reset', '-q', '--mixed', commit)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), commit)
        self.assertEqual(self.git('status', '--porcelain', '--', 'done', 'extra').stdout, '?? extra\n')
        self.assertEqual((self.host / 'done').read_text(), 'committed by the agent\n')
        self.assertEqual((self.host / 'extra').read_text(), 'left in the tree\n')
