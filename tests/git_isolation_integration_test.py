"""Real mounts and Git across clone and shared, in a disposable repository.

RUN_GIT_ISOLATION_DOCKER_TESTS=1 enables this; an existing agent image supplies
Python/Git/setpriv. Current workspace scripts are mounted over its old copies.
No builds, network access, credentials or developer repository writes are used.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / '.devcontainer/claude'
spec = importlib.util.spec_from_file_location('isolation_settings', CORE / 'scripts/settings.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


@unittest.skipUnless(os.environ.get('RUN_GIT_ISOLATION_DOCKER_TESTS') == '1', 'Git isolation Docker tests disabled')
class GitIsolationIntegrationTest(unittest.TestCase):
    def command(self, *args, check=True):
        return subprocess.run(args, text=True, capture_output=True, check=check)

    def docker(self, *args, check=True):
        return self.command('docker', *args, check=check)

    def setUp(self):
        self.image = os.environ.get('AGENT_TEST_IMAGE', 'claude-devcontainer-agent:initial-branch-20261013')
        self.docker('image', 'inspect', self.image)
        self.project = 'cdc-git-isolation-' + uuid.uuid4().hex[:12]
        self.container = self.project + '-agent'
        self.volume = self.project + '_workspace'
        self.agent_git = self.project + '_agent-git'
        self.dependencies = self.project + '_node_modules'
        temporary = tempfile.TemporaryDirectory(prefix='cdc-isolation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o755)
        self.host = self.root / 'host'
        self.host.mkdir()
        self.command('git', 'init', '-qb', 'main', str(self.host))
        self.git('config', 'user.name', 'fixture')
        self.git('config', 'user.email', 'fixture@example.com')
        (self.host / 'tracked').write_text('original\n')
        self.git('add', 'tracked')
        self.git('commit', '-qm', 'fixture')
        self.initial = self.git('rev-parse', 'HEAD').stdout.strip()
        scripts = self.host / '.devcontainer/claude/scripts'
        scripts.mkdir(parents=True)
        for name in ('workspace-source.py', 'git-agent.py'):
            shutil.copy(CORE / 'scripts' / name, scripts / name)
        self.command(sys.executable, str(scripts / 'workspace-source.py'))
        self.command(sys.executable, str(scripts / 'git-agent.py'), 'register')
        self.generated = self.host / '.devcontainer/.generated'
        (self.host / '.secrets').mkdir()
        (self.host / '.secrets/token').write_text('directory-secret\n')
        (self.host / '.env').write_text('file-secret\n')
        (self.host / '.devcontainer/sumi.txt').write_text('sumi-secret\n')
        lower = self.host / 'node_modules'
        lower.mkdir()
        (lower / 'dependency').write_text('host dependency\n')
        for name in ('upper', 'work'):
            (self.root / name).mkdir()
        self.docker('volume', 'create', self.volume)
        self.addCleanup(self.docker, 'volume', 'rm', self.volume)
        self.addCleanup(self.docker, 'volume', 'rm', self.agent_git, check=False)
        self.docker('volume', 'create', '--driver', 'local', '--opt', 'type=overlay', '--opt', 'device=overlay',
                    '--opt', f'o=lowerdir={lower},upperdir={self.root}/upper,workdir={self.root}/work', self.dependencies)
        self.addCleanup(self.docker, 'volume', 'rm', self.dependencies)
        # Remove root-owned overlay work directories before TemporaryDirectory.
        self.addCleanup(self.clean_state)
        self.addCleanup(self.docker, 'rm', '-f', self.container, check=False)

    def clean_state(self):
        self.docker('run', '--rm', '--network', 'none', '--entrypoint', 'sh',
                    '--mount', f'type=bind,src={self.root},dst=/fixture', self.image,
                    '-c', 'rm -rf /fixture/upper /fixture/work')

    def git(self, *args, check=True):
        return self.command('git', '-C', str(self.host), *args, check=check)

    def execute(self, *args, check=True):
        return self.docker('exec', '--user', 'node', self.container, *args, check=check)

    def start(self, mode):
        self.docker('rm', '-f', self.container, check=False)
        (self.host / '.devcontainer/claude.toml').write_text(
            f'[git]\nisolation = "{mode}"\nshared_hidden_paths = [".secrets", ".env"]\n'
            '[sumi]\nsecrets = "sumi.txt"\n')
        settings.generate(self.host, {})
        volumes = [
            {'type': 'volume', 'source': 'dependencies', 'target': '/workspace/node_modules'},
            {'type': 'bind', 'source': str(self.generated), 'target': '/run/host/generated', 'read_only': True}]
        for name in ('workspace_mounts.py', 'clone-workspace.py', 'wait-ready.sh'):
            volumes.append({'type': 'bind', 'source': str(CORE / 'agent' / name),
                            'target': '/usr/local/bin/' + name, 'read_only': True})
        script = ('mkdir -p /run/claude-devcontainer; touch /run/claude-devcontainer/host-config-copied; '
                  'python3 /usr/local/bin/workspace_mounts.py && '
                  'exec setpriv --reuid=node --regid=node --init-groups '
                  '--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs sleep infinity')
        runner = self.root / 'runner.json'
        runner.write_text(json.dumps({
            'services': {'agent': {
                'image': self.image, 'container_name': self.container, 'init': True, 'network_mode': 'none',
                'user': '0:0', 'entrypoint': ['sh', '-ec', script], 'command': [],
                'cap_drop': ['ALL'], 'cap_add': ['CHOWN', 'SETUID', 'SETGID', 'SETPCAP'],
                'security_opt': ['no-new-privileges=true'],
                'labels': {'devcontainer.local_folder': str(self.host), 'dev.claude-devcontainer.git-isolation': mode},
                'environment': {'CLAUDE_DEVCONTAINER_GIT_ISOLATION': mode, 'CLAUDE_DEVCONTAINER_DIND': '0',
                                'CLAUDE_DEVCONTAINER_NIX': '0', 'CLAUDE_DEVCONTAINER_SUMI': '0',
                                'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.com',
                                'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.com'},
                'volumes': volumes}},
            'volumes': {'workspace': {'external': True, 'name': self.volume},
                        'agent-git': {},
                        'dependencies': {'external': True, 'name': self.dependencies}}}))
        self.docker('compose', '-p', self.project, '-f', str(self.generated / 'workspace.compose.yaml'),
                    '-f', str(runner), 'up', '-d', '--no-build', '--pull', 'never', 'agent')
        # docker exec may start before root mount preparation has finished.
        self.docker('exec', self.container, 'sh', '-ec',
                    'for i in $(seq 1 100); do '
                    '[ "$(stat -c %u /workspace/node_modules)" = "$(id -u node)" ] && exit 0; '
                    'sleep 0.1; done; exit 1')
        self.execute('sh', '/usr/local/bin/wait-ready.sh')

    def test_unsafe_shared_commits_reach_host_and_protections_hold(self):
        (self.host / 'tracked').write_text('staged on host\n')
        self.git('add', 'tracked')
        before = (self.host / '.git/index').read_bytes()
        self.start('unsafe_shared')
        self.assertEqual((self.host / '.git/index').read_bytes(), before)
        self.execute('git', '-C', '/workspace', 'commit', '-qm', 'shared commit')
        self.assertEqual(self.git('log', '-1', '--format=%s').stdout.strip(), 'shared commit')
        self.assertEqual(self.git('show', 'HEAD:tracked').stdout, 'staged on host\n')
        self.assert_shared_protections()
        self.execute('python3', '-c', '''
import errno, os
for path in ('/workspace/.git/objects', '/workspace/.git/config', '/workspace/.git/config.worktree'):
    try:
        os.rename(path, path + '-moved')
    except OSError as error:
        assert error.errno in (errno.EBUSY, errno.EROFS), (path, error)
    else:
        raise AssertionError(path)
''')
        for name in ('config', 'config.worktree', 'hooks/pre-commit', 'info/attributes', 'objects/info/alternates'):
            result = self.execute('python3', '-c',
                                  'from pathlib import Path; Path("/workspace/.git/" + ' + repr(name) +
                                  ').write_text("tampered")', check=False)
            self.assertNotEqual(result.returncode, 0, name)
        result = self.execute('git', '-C', '/workspace', 'config', '--local', 'core.hooksPath', '/tmp/hooks', check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_unsafe_shared_rebase_todo_is_writable_and_shared(self):
        (self.host / 'tracked').write_text('rebase fixture\n')
        self.git('add', 'tracked')
        self.git('commit', '-qm', 'rebase fixture')
        self.start('unsafe_shared')
        result = self.execute('git', '-C', '/workspace', 'rebase', '--exec', 'false', 'HEAD~1', check=False)
        self.assertNotEqual(result.returncode, 0)
        todo = self.host / '.git/rebase-merge/git-rebase-todo'
        self.assertTrue(todo.is_file())
        self.execute('python3', '-c',
                     'from pathlib import Path; Path("/workspace/.git/rebase-merge/git-rebase-todo")'
                     '.write_text("exec true\\n")')
        self.assertEqual(todo.read_text(), 'exec true\n')
        self.git('rebase', '--continue')
        self.assertFalse(todo.parent.exists())
        self.assertEqual(self.git('show', 'HEAD:tracked').stdout, 'rebase fixture\n')

    def assert_shared_protections(self):
        self.execute('python3', '-c', '''
import ctypes, errno, os, sys
from pathlib import Path
status = Path('/proc/self/status').read_text()
assert int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16) == 0
assert list(Path('/workspace/.secrets').iterdir()) == []
for name in ('.env', '.devcontainer/sumi.txt'):
    assert (Path('/workspace') / name).read_bytes() == b''
assert not Path('/workspace/.secrets/token').exists()
for name in ('.devcontainer/claude.toml', '.devcontainer/new', '.devcontainer/.generated/empty-secret',
             '.secrets/new', '.env', '.devcontainer/sumi.txt'):
    try:
        (Path('/workspace') / name).write_text('tampered')
    except OSError as error:
        assert error.errno == errno.EROFS, (name, error)
    else:
        raise AssertionError(name)
libc = ctypes.CDLL(None, use_errno=True)
for name in ('.git', '.devcontainer', '.secrets', '.env'):
    path = '/workspace/' + name
    try:
        os.rename(path, path + '-moved')
    except OSError as error:
        assert error.errno in (errno.EBUSY, errno.EROFS), (name, error)
    else:
        raise AssertionError(name)
    assert libc.umount2(os.fsencode(path), 0) == -1
    assert ctypes.get_errno() == errno.EPERM
''')
        self.assertEqual((self.host / '.secrets/token').read_text(), 'directory-secret\n')
        self.assertEqual((self.host / '.env').read_text(), 'file-secret\n')
        self.assertEqual((self.host / '.devcontainer/sumi.txt').read_text(), 'sumi-secret\n')

    def test_shared_commits_stay_private_until_accepted(self):
        self.start('clone')
        host_git = {p: p.read_bytes() for p in (self.host / '.git').rglob('*') if p.is_file()}
        (self.host / 'tracked').write_text('host dirty\n')
        self.start('shared')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'branch', '--show-current').stdout.strip(), 'main')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'HEAD').stdout.strip(), self.initial)
        self.assertEqual(self.execute('cat', '/workspace/tracked').stdout, 'host dirty\n')
        self.execute('git', '-C', '/workspace', 'switch', '-qc', 'agent/task')
        self.execute('sh', '-ec', 'echo agent-commit > /workspace/tracked; echo agent-left > /workspace/left')
        self.execute('git', '-C', '/workspace', 'add', 'tracked')
        self.execute('git', '-C', '/workspace', 'commit', '-qm', 'agent commit')
        agent_commit = self.execute('git', '-C', '/workspace', 'rev-parse', 'HEAD').stdout.strip()
        self.execute('git', '-C', '/workspace', 'stash', 'list')
        # The host's Git is untouched; its files carry the agent's edits.
        self.assertEqual({p: p.read_bytes() for p in (self.host / '.git').rglob('*') if p.is_file()}, host_git)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.initial)
        self.assertEqual((self.host / 'tracked').read_text(), 'agent-commit\n')
        self.assert_shared_protections()
        self.execute('python3', '-c', '''
import errno, os
from pathlib import Path
for name in ('HEAD', 'config', 'hooks/evil', 'objects/evil', 'refs/heads/main'):
    try:
        (Path('/run/host/git') / name).write_text('tampered')
    except OSError as error:
        assert error.errno == errno.EROFS, (name, error)
    else:
        raise AssertionError(name)
assert Path('/workspace/.git/objects').is_dir() and not Path('/workspace/.git/objects/info/alternates').exists()
''')
        self.git('fetch', '--no-tags', 'agent', 'refs/heads/agent/task')
        self.assertEqual(self.git('rev-parse', 'FETCH_HEAD').stdout.strip(), agent_commit)
        self.assertIn('+agent-commit', self.git('diff', self.initial, agent_commit).stdout)
        result = self.git('push', 'agent', 'HEAD:probe', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('shared', result.stderr)
        scripts = self.host / '.devcontainer/claude/scripts'
        self.git('diff', '--cached', '--quiet')
        self.git('merge-base', '--is-ancestor', 'HEAD', agent_commit)
        self.git('reset', '-q', '--mixed', agent_commit)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), agent_commit)
        self.assertEqual(self.git('status', '--porcelain', '--', 'tracked', 'left').stdout, '?? left\n')
        self.assertEqual((self.host / 'left').read_text(), 'agent-left\n')
        # Recreated, the agent follows the host's branch once its commit is accepted.
        self.git('switch', '-qc', 'host/next')
        self.git('commit', '-q', '--allow-empty', '-m', 'host commit')
        self.command(sys.executable, str(scripts / 'workspace-source.py'))
        self.start('shared')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'branch', '--show-current').stdout.strip(), 'host/next')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'HEAD').stdout.strip(),
                         self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'agent/task').stdout.strip(), agent_commit)
        self.assertEqual((self.host / 'left').read_text(), 'agent-left\n')
        # The clone volume is untouched by shared, and the agent-git volume survives clone.
        self.start('clone')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'HEAD').stdout.strip(), self.initial)
        self.assertNotEqual(self.execute('test', '-e', '/workspace/left', check=False).returncode, 0)
        self.start('shared')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'agent/task').stdout.strip(), agent_commit)


if __name__ == '__main__':
    unittest.main()
