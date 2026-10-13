"""Real mounts and Git across clone -> shared -> clone, in a disposable repository.

RUN_GIT_ISOLATION_DOCKER_TESTS=1 enables this; an existing agent image supplies
Python/Git/setpriv. Current workspace scripts are mounted over its old copies.
No builds, network access, credentials or developer repository writes are used.
"""
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
        lower = self.host / 'node_modules'
        lower.mkdir()
        (lower / 'dependency').write_text('host dependency\n')
        for name in ('upper', 'work'):
            (self.root / name).mkdir()
        self.docker('volume', 'create', self.volume)
        self.addCleanup(self.docker, 'volume', 'rm', self.volume)
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
        source = self.volume if mode == 'clone' else str(self.host)
        kind = 'volume' if mode == 'clone' else 'bind'
        args = ['run', '-d', '--init', '--network', 'none', '--name', self.container,
                '--label', f'devcontainer.local_folder={self.host}',
                '--label', 'com.docker.compose.service=agent',
                '--label', f'com.docker.compose.project={self.project}',
                '--label', f'dev.claude-devcontainer.git-isolation={mode}',
                '-e', f'CLAUDE_DEVCONTAINER_GIT_ISOLATION={mode}',
                '-e', 'CLAUDE_DEVCONTAINER_DIND=0', '-e', 'CLAUDE_DEVCONTAINER_NIX=0',
                '-e', 'CLAUDE_DEVCONTAINER_SUMI=0',
                '--mount', f'type={kind},src={source},dst=/workspace',
                '--mount', f'type=volume,src={self.dependencies},dst=/workspace/node_modules',
                '--mount', f'type=bind,src={self.generated},dst=/run/host/generated,readonly']
        for name in ('workspace_mounts.py', 'clone-workspace.py', 'wait-ready.sh'):
            args += ['--mount', f'type=bind,src={CORE}/agent/{name},dst=/usr/local/bin/{name},readonly']
        script = ('mkdir -p /run/claude-devcontainer; touch /run/claude-devcontainer/host-config-copied; '
                  'python3 /usr/local/bin/workspace_mounts.py && '
                  'exec setpriv --reuid=node --regid=node --init-groups '
                  '--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs sleep infinity')
        self.docker(*args, '--entrypoint', 'sh', self.image, '-ec', script)
        # docker exec may start before root mount preparation has finished.
        self.docker('exec', self.container, 'sh', '-ec',
                    'for i in $(seq 1 100); do '
                    '[ "$(stat -c %u /workspace/node_modules)" = "$(id -u node)" ] && exit 0; '
                    'sleep 0.1; done; exit 1')
        self.execute('sh', '/usr/local/bin/wait-ready.sh')

    def test_real_shared_edits_git_and_clone_persistence(self):
        self.start('clone')
        self.execute('git', '-C', '/workspace', 'switch', '-c', 'agent/task')
        self.execute('sh', '-ec', 'echo clone-only > /workspace/dirty; echo overlay-change > /workspace/node_modules/dependency')
        self.assertFalse((self.host / 'dirty').exists())
        self.assertEqual((self.host / 'node_modules/dependency').read_text(), 'host dependency\n')
        self.git('fetch', 'agent')
        (self.host / 'tracked').write_text('host dirty\n')
        (self.host / 'untracked').write_text('host untracked\n')
        paths = [self.host, self.host / '.git', self.host / '.devcontainer', self.host / 'node_modules']
        before = [(p.stat().st_uid, p.stat().st_gid, p.stat().st_mode) for p in paths]
        self.start('shared')
        self.assertEqual(self.execute('cat', '/workspace/tracked').stdout, 'host dirty\n')
        self.assertEqual(self.execute('cat', '/workspace/untracked').stdout, 'host untracked\n')
        self.execute('sh', '-ec', 'echo shared-edit > /workspace/tracked')
        self.execute('git', '-C', '/workspace', 'switch', '-c', 'shared/task')
        self.execute('git', '-C', '/workspace', 'add', 'tracked')
        self.execute('git', '-C', '/workspace', 'commit', '-m', 'shared edit')
        self.assertEqual((self.host / 'tracked').read_text(), 'shared-edit\n')
        self.assertEqual(self.git('branch', '--show-current').stdout.strip(), 'shared/task')
        self.assertEqual([(p.stat().st_uid, p.stat().st_gid, p.stat().st_mode) for p in paths], before)
        self.assertEqual(self.execute('cat', '/workspace/node_modules/dependency').stdout, 'overlay-change\n')
        for args in [('fetch', 'agent'), ('push', 'agent', 'HEAD:probe')]:
            result = self.git(*args, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('shared', result.stderr)
        self.start('clone')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'branch', '--show-current').stdout.strip(), 'agent/task')
        self.assertEqual(self.execute('git', '-C', '/workspace', 'rev-parse', 'HEAD').stdout.strip(), self.initial)
        self.assertEqual(self.execute('cat', '/workspace/dirty').stdout, 'clone-only\n')
        self.assertEqual(self.execute('cat', '/workspace/node_modules/dependency').stdout, 'overlay-change\n')
        self.assertEqual((self.host / 'node_modules/dependency').read_text(), 'host dependency\n')
        self.assertEqual(self.git('branch', '--show-current').stdout.strip(), 'shared/task')


if __name__ == '__main__':
    unittest.main()
