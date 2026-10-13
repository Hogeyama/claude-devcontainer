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
