"""Whole-container, offline Docker, actual Claude Bash and SDK integration checks."""
import base64
import http.server
import json
import os
from pathlib import Path
import shutil
import shlex
import sys
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from proxy_integration_test import ROOT, PROXY_IMAGE, docker, fixtures

CORE = ROOT / '.devcontainer/claude'
AGENT_IMAGE = os.environ.get('AGENT_TEST_IMAGE', 'claude-devcontainer-agent:task2')
SUMI_IMAGE = os.environ.get('SUMI_TEST_IMAGE', 'claude-devcontainer-sumi:task2')
SECRET = 'Tr0ub4dor-fixture'
# The test workspace's agent.uid and agent.gid; AGENT_IMAGE must be built with them.
AGENT_UID = os.environ.get('CLAUDE_DEVCONTAINER_UID', '1000')
AGENT_GID = os.environ.get('CLAUDE_DEVCONTAINER_GID', '1000')
# The test workspace's subnets; unset, initializeCommand chooses free ones.
SUBNETS = {key: os.environ.get(f'CLAUDE_DEVCONTAINER_{key.upper()}_NET', '') for key in ('proxy', 'dind')}


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class DevcontainerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docker('info')
        docker('image', 'inspect', AGENT_IMAGE, PROXY_IMAGE, SUMI_IMAGE, 'busybox:1.37', 'testcontainers/ryuk:0.14.0')
        cls.project = 'cdc-integration-' + uuid.uuid4().hex[:12]
        cls.directory = Path(tempfile.mkdtemp(prefix='cdc-integration-'))
        cls.addClassCleanup(shutil.rmtree, cls.directory)
        fixtures(cls.directory)
        auth_variables = ('GITHUB_TOKEN', 'GH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY', 'CLAUDE_CREDENTIALS_FILE',
                          'ANTHROPIC_AUTH_TOKEN')
        # The settings come only from the workspace's claude.toml and
        # claude.local.toml (start_environment); initializeCommand refuses
        # them in the environment, where the agent itself has some.
        cls.env = {k: v for k, v in os.environ.items()
                   if k not in auth_variables and not k.startswith('CLAUDE_DEVCONTAINER_')}
        # initializeCommand reads the credentials config; never the developer's own.
        cls.env.update(XDG_CONFIG_HOME=str(cls.directory / 'config'), XDG_STATE_HOME=str(cls.directory / 'state'))
        # The sumi service reads a secrets file outside the workspace, under a
        # name other than the default. Not in cls.directory, which the agent mounts.
        # start_environment() sets it up through claude.toml and claude.local.toml.
        cls.sumi = Path(tempfile.mkdtemp(prefix='cdc-sumi-'))
        cls.addClassCleanup(shutil.rmtree, cls.sumi)
        (cls.sumi / 'list.txt').write_text(SECRET + '\n')
        (cls.sumi / 'list.txt').chmod(0o600)
        # initializeCommand copies the Claude Code configuration; never the developer's own.
        claude = cls.directory / 'claude'
        (claude / 'skills/fixture').mkdir(parents=True)
        (claude / 'skills/fixture/SKILL.md').write_text('fixture skill')
        (cls.directory / 'statusline.sh').write_text('echo fixture-status')
        (claude / 'statusline.sh').symlink_to(cls.directory / 'statusline.sh')
        (claude / 'settings.json').write_text(json.dumps({'statusLine': {'type': 'command', 'command': 'sh ~/.claude/statusline.sh'}}))
        (claude / '.credentials.json').write_text('synthetic-credentials')
        cls.env.update(CLAUDE_CONFIG_DIR=str(claude))
        extra = cls.directory / 'config/claude-devcontainer/extra'
        extra.mkdir(parents=True)
        (extra / '.gitconfig').write_text('[user]\n\tname = fixture-user\n')
        (extra.parent / 'credentials').write_text('GITHUB_TOKEN=synthetic-managed-token\nANTHROPIC_API_KEY=synthetic-managed-token\n')
        cls.addClassCleanup(docker, 'run', '--rm', '--network', 'none', '--entrypoint', 'rm',
                            '-v', str(cls.directory / 'state') + ':/state', AGENT_IMAGE,
                            '-rf', '/state/claude-devcontainer/node-modules')
        cls.start_environment()
        # initializeCommand chose free subnets unless they were set.
        chosen = cls.workspace / '.devcontainer/.generated/networks.env'
        cls.dind_net = SUBNETS['dind'] or dict(
            line.split('=', 1) for line in chosen.read_text().splitlines() if '=' in line and not line.startswith('#')
        )['CLAUDE_DEVCONTAINER_DIND_NET']
        cls.inject_credentials('api-key')
        # docker.images had them loaded into DinD before `up` returned.
        cls.exec('docker', 'image', 'inspect', 'busybox:1.37', 'testcontainers/ryuk:0.14.0')
        cls.container = cls.exec('docker', 'run', '-d', '-p', '8080', 'busybox:1.37', 'sh', '-c',
                                 'mkdir -p /www; echo bridge-ok > /www/index.html; exec httpd -f -p 8080 -h /www').stdout.strip()
        cls.port = cls.exec('docker', 'inspect', '--format', '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}', cls.container).stdout.strip()
        class HostHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'host-reachable')
        cls.host_server = http.server.ThreadingHTTPServer(('0.0.0.0', 0), HostHandler)
        threading.Thread(target=cls.host_server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.host_server.server_close)
        cls.addClassCleanup(cls.host_server.shutdown)
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:' + str(cls.host_server.server_port)) as response:
            assert response.read() == b'host-reachable'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('1.1.1.1', 80))  # Route lookup only; no packets sent.
            cls.host_ip = sock.getsockname()[0]

    @classmethod
    def start_environment(cls):
        # A disposable workspace gives the real CLI its normal root/config labels
        # without editing the user's config or installing editor/profile state.
        cls.workspace = cls.directory / 'workspace'
        cls.workspace.mkdir()
        # Lay it out as a consumer would: the distributed core plus its template.
        shutil.copytree(CORE, cls.workspace / '.devcontainer/claude', symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('devcontainer.json', 'compose.yaml', 'agent-setup.sh', 'claude.toml', '.gitignore'):
            shutil.copy(CORE / 'template' / name, cls.workspace / '.devcontainer' / name)
        # The workspace's settings, and this host's overriding them: with the
        # wrong precedence, initializeCommand finds no secrets file and up fails.
        # Every test here but the credentials ones relies on Docker.
        shared = cls.workspace / '.devcontainer/claude.toml'
        shared.write_text(shared.read_text()
                          .replace('\n[docker]\n', '\n[docker]\nenabled = true\nimages = ["busybox:1.37", "testcontainers/ryuk:0.14.0"]\n')
                          .replace('\n[sumi]\n', '\n[sumi]\nenabled = true\nsecrets = "missing.txt"\n'))
        local = f'[sumi]\nsecrets = "{cls.sumi}/list.txt"\n[agent]\nuid = {AGENT_UID}\ngid = {AGENT_GID}\n'
        local += '[subnets]\n' + ''.join(f'{key} = "{net}"\n' for key, net in SUBNETS.items() if net)
        (cls.workspace / '.devcontainer/claude.local.toml').write_text(local)
        shutil.copytree(ROOT / 'tests', cls.workspace / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        (cls.workspace / 'package.json').write_text(json.dumps({
            'private': True, 'scripts': {'test': 'node -e "require(\'testcontainers\')"'}}))
        nested = cls.workspace / 'packages/app'
        nested.mkdir(parents=True)
        (nested / 'package.json').write_text('{"private":true}\n')
        git = ['git', '-C', str(cls.workspace), '-c', 'user.name=t', '-c', 'user.email=t@example.com']
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'fixture']):
            subprocess.run([*git, *args], check=True)
        dependency_source = os.environ.get('TESTCONTAINERS_NODE_MODULES')
        if dependency_source:
            shutil.copytree(dependency_source, cls.workspace / 'node_modules', symlinks=True)
        # Delay the production entrypoint so that `up` must wait for postStartCommand.
        cls.initial_commit = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
        cls.initial_branch = subprocess.check_output([*git, 'branch', '--show-current'], text=True).strip()
        startup_dir = cls.directory / 'startup'
        startup_dir.mkdir()
        startup = startup_dir / 'entrypoint.sh'
        startup.write_text('#!/bin/sh\nsleep 3\nexec /usr/local/bin/entrypoint.sh "$@"\n')
        startup.chmod(0o755)
        # A consumer's read-only dependency bind, with a tmpfs where tools write
        # at runtime. Docker cannot create that mount point in the read-only
        # bind. Owned by the host user, which is agent.uid as for sumi's secrets.
        cls.frontend_dependencies = cls.directory / 'frontend-node-modules'
        (cls.frontend_dependencies / '.cache').mkdir(parents=True)
        (cls.frontend_dependencies / 'dependency').write_text('host-dependency\n')
        override = cls.directory / 'override.json'
        override.write_text(json.dumps({'services': {
            'agent': {'image': AGENT_IMAGE, 'volumes': [str(startup_dir) + ':/startup-fixture:ro',
                          'app-node-modules:/workspace/packages/app/node_modules',
                          {'type': 'bind', 'source': str(cls.frontend_dependencies),
                           'target': '/workspace/demos/frontend/node_modules', 'read_only': True,
                           'bind': {'create_host_path': False}}],
                      'tmpfs': ['/workspace/demos/frontend/node_modules/.cache:mode=1777'],
                      'entrypoint': ['/startup-fixture/entrypoint.sh']},
            'sumi': {'image': SUMI_IMAGE},
            'proxy': {'image': PROXY_IMAGE, 'entrypoint': ['python', '/fixtures/proxy_server.py'],
                      'volumes': [str(cls.directory) + ':/fixtures:ro']}}, 'volumes': {'app-node-modules': {}}}))
        config = cls.workspace / '.devcontainer/devcontainer.json'
        value = json.loads(config.read_text())
        value['dockerComposeFile'] = ['compose.yaml', str(override)]
        config.write_text(json.dumps(value))
        cls.compose = ['docker', 'compose', '-p', cls.project, '-f',
                       str(cls.workspace / '.devcontainer/compose.yaml'), '-f', str(override)]
        cls.addClassCleanup(cls.compose_run, 'down', '--volumes', '--remove-orphans')
        # A shim only selects the real installed CLI and isolates its user data;
        # it neither emulates up/exec nor inspects authentication values.
        command = shlex.split(os.environ.get('DEVCONTAINER_TEST_CLI', 'devcontainer'))
        executable = shutil.which(command[0])
        if not executable: raise RuntimeError('real Dev Containers CLI required')
        command[0] = executable
        binary = cls.directory / 'bin'
        binary.mkdir()
        shim = binary / 'devcontainer'
        shim.write_text('#!' + sys.executable + '\nimport os,sys\ncommand=' + repr(command)
                        + '\nargs=[sys.argv[1],"--user-data-folder",' + repr(str(cls.directory / 'cli-data'))
                        + ',*sys.argv[2:]]\nos.execvpe(command[0],command+args,os.environ)\n')
        shim.chmod(0o755)
        cls.env.update(PATH=str(binary) + ':' + cls.env['PATH'], COMPOSE_PROJECT_NAME=cls.project)
        result = subprocess.run(['devcontainer', 'up', '--workspace-folder', str(cls.workspace),
                                 '--default-user-env-probe', 'none'],
                                env=cls.env, capture_output=True, text=True, timeout=600)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)
        # No harness polling: up must return with the production gate passed.
        status = cls.exec('cat', '/proc/1/status').stdout
        for expected in ['Uid:' + ('\t' + AGENT_UID) * 4, 'Gid:' + ('\t' + AGENT_GID) * 4, 'NoNewPrivs:\t1',
                         *[field + ':\t0000000000000000' for field in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')]]:
            assert expected in status, status
        cls.exec('docker', 'info', '--format', '{{.ServerVersion}}')

    @classmethod
    def inject_credentials(cls, mode):
        # proxy.hosts' secrets come only from the credentials config.
        config = cls.directory / 'config/claude-devcontainer'
        config.mkdir(parents=True, exist_ok=True)
        (config / 'credentials').write_text('GITHUB_TOKEN=synthetic-managed-token\n')
        environment = dict(cls.env)
        if mode == 'oauth':
            # A synthetic host login, far from expiring, so that it is not refreshed.
            login = cls.directory / 'login.json'
            login.write_text(json.dumps({'claudeAiOauth': {
                'accessToken': 'synthetic-managed-token', 'refreshToken': 'synthetic-refresh',
                'expiresAt': int(time.time() * 1000) + 24 * 3600 * 1000,
                'scopes': ['user:profile', 'user:inference', 'user:mcp_servers']}}))
            environment['CLAUDE_CREDENTIALS_FILE'] = str(login)
        else:
            environment['ANTHROPIC_API_KEY'] = 'synthetic-managed-token'
        result = subprocess.run([sys.executable, str(cls.workspace / '.devcontainer/claude/scripts/inject-credentials.py')],
                                env=environment, capture_output=True, text=True, timeout=90)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)
        assert 'synthetic-managed-token' not in result.stdout + result.stderr

    @classmethod
    def compose_run(cls, *args):
        result = subprocess.run([*cls.compose, *args], env=cls.env, capture_output=True, text=True, timeout=180)
        if result.returncode: raise RuntimeError(result.stderr + result.stdout)
        return result.stdout

    @classmethod
    def exec(cls, *args, check=True, environment=None):
        flags = []
        for name, value in (environment or {}).items(): flags += ['-e', name + '=' + value]
        result = subprocess.run([*cls.compose, 'exec', '-T', '--user', 'node', *flags, 'agent', *args],
                                env=cls.env, capture_output=True, text=True, timeout=180)
        if check and result.returncode: raise AssertionError(result.stdout + result.stderr)
        return result

    @classmethod
    def cli_exec(cls, *args):
        result = subprocess.run(['devcontainer', 'exec', '--workspace-folder', str(cls.workspace), *args],
                                env=cls.env, capture_output=True, text=True, timeout=180)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)
        return result

    def capture_count(self):
        return len(self.compose_run('exec', '-T', 'proxy', 'sh', '-c', 'cat /tmp/upstream.jsonl 2>/dev/null || true').splitlines())

    def test_one_injection_enables_normal_cli_processes_in_both_modes(self):
        for mode in ('api-key', 'oauth'):
            with self.subTest(mode=mode):
                self.inject_credentials(mode)
                body = json.dumps({'model': 'fixture', 'max_tokens': 1, 'messages': []})
                result = self.cli_exec('curl', '--fail', '--max-time', '5', '-H', 'Content-Type: application/json',
                                   '-d', body, 'https://api.anthropic.com/v1/messages')
                self.assertIn('upstream-ok', result.stdout)
                item = json.loads(self.compose_run('exec', '-T', 'proxy', 'tail', '-n', '1', '/tmp/upstream.jsonl'))
                headers = {k.lower(): v for k, v in item['headers']}
                if mode == 'oauth':
                    self.assertEqual(headers['authorization'], 'Bearer synthetic-managed-token')
                    self.assertNotIn('x-api-key', headers)
                else:
                    self.assertEqual(headers['x-api-key'], 'synthetic-managed-token')
                    self.assertNotIn('authorization', headers)
                self.assertNotIn('synthetic-managed-token', self.compose_run('logs', 'proxy'))
        self.inject_credentials('api-key')

    def test_dummy_tokens_of_proxy_hosts(self):
        # env of api.github.com in template/claude.toml.
        self.assertEqual(self.exec('printenv', 'GH_TOKEN').stdout, 'managed-by-proxy\n')

    def test_vs_code_forwards_no_x11_display(self):
        # Dev Containers forwards the host's display only into a container
        # whose environment lacks DISPLAY; the processes it starts see none.
        container = self.compose_run('ps', '-q', 'agent').strip()
        self.assertIn('DISPLAY=none', json.loads(docker('inspect', container))[0]['Config']['Env'])
        result = self.cli_exec('sh', '-c', 'printf "display=[%s]" "${DISPLAY-unset}"')
        self.assertIn('display=[]', result.stdout)

    def test_host_claude_configuration_is_copied_before_up_returns(self):
        result = self.exec('sh', '-c', 'git config --global user.name; cd ~/.claude && cat skills/fixture/SKILL.md && sh statusline.sh && ls -A')
        self.assertIn('fixture-user', result.stdout)
        self.assertIn('fixture skill', result.stdout)
        self.assertIn('fixture-status', result.stdout)
        self.assertNotIn('.credentials.json', result.stdout)
        self.assertNotIn('settings.json', result.stdout)
        managed = json.loads(self.exec('cat', '/etc/claude-code/managed-settings.d/50-host.json').stdout)
        self.assertEqual(managed['statusLine']['command'], 'sh ~/.claude/statusline.sh')

    def test_agent_cannot_change_what_host_commands_run(self):
        probe = ('set -u; cd ~/.claude; '
                 'for op in "echo x >> statusline.sh" "rm -f statusline.sh" "mv statusline.sh moved" "mv skills moved" '
                 '"mv ~/.claude ~/.claude-moved" "echo {} > /etc/claude-code/managed-settings.d/50-host.json"; do '
                 'if sh -c "$op" 2>/dev/null; then echo "writable: $op"; exit 1; fi; done; '
                 'echo {} > own.json && mv own.json settings.json && rm settings.json && echo protected-ok')
        result = self.exec('sh', '-c', probe, check=False)
        self.assertIn('protected-ok', result.stdout, result.stdout + result.stderr)

    def test_whole_container_direct_egress_dns_ipv6_host_blocked(self):
        result = self.exec('python3', '/workspace/tests/fixtures/network_probe.py', self.host_ip, str(self.host_server.server_port))
        self.assertIn('direct-network-blocked', result.stdout)

    def test_agent_git_and_config_changes_do_not_reach_host(self):
        host_head = subprocess.check_output(['git', '-C', str(self.workspace), 'rev-parse', 'HEAD'], text=True)
        paths = ['.git/config', '.devcontainer/devcontainer.json', '.git/index']
        before = {name: (self.workspace / name).read_bytes() for name in paths}
        self.exec('sh', '-ec', """
            git switch -c agent/isolation
            git config isolation.probe agent-only
            printf '#!/bin/sh\\nexit 0\\n' > .git/hooks/pre-commit
            chmod +x .git/hooks/pre-commit
            printf '\\n' >> .devcontainer/devcontainer.json
            git add .devcontainer/devcontainer.json
            git -c user.name=fixture -c user.email=fixture@example.com commit -m isolation
        """)
        for name in paths:
            self.assertEqual((self.workspace / name).read_bytes(), before[name])
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.workspace), 'rev-parse', 'HEAD'], text=True), host_head)
        subprocess.run(['git', '-C', str(self.workspace), 'fetch', '--no-tags', 'agent',
                        'refs/heads/agent/isolation'], env=self.env, check=True, capture_output=True)
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.workspace), 'rev-parse', 'FETCH_HEAD'], text=True),
                         self.exec('git', 'rev-parse', 'HEAD').stdout)
        self.exec('git', 'switch', self.initial_branch)

    def test_initial_clone_and_readonly_host_inputs(self):
        self.assertEqual(self.exec('cat', 'packages/app/package.json').stdout, '{"private":true}\n')
        self.exec('sh', '-ec', 'echo writable > packages/app/node_modules/probe')
        self.assertEqual(self.exec('git', 'rev-parse', 'HEAD').stdout.strip(), self.initial_commit)
        self.assertEqual(self.exec('git', 'branch', '--show-current').stdout.strip(), self.initial_branch)
        self.assertEqual(self.exec('git', 'remote').stdout.strip(), '')
        remote = subprocess.check_output(['git', '-C', str(self.workspace), 'remote', 'get-url', 'agent'], text=True)
        self.assertTrue(remote.startswith('ext::'))
        for path in ('/run/host/generated/workspace.json', '/run/host/generated/images.txt', '/run/host/nix-env/probe'):
            self.assertNotEqual(self.exec('sh', '-c', 'echo x >> "$1"', 'sh', path, check=False).returncode, 0)
        config = json.loads(self.compose_run('config', '--format', 'json'))
        mounts = {m['target']: m for m in config['services']['agent']['volumes']}
        self.assertEqual(mounts['/workspace']['type'], 'volume')
        self.assertEqual(mounts['/workspace/node_modules']['type'], 'volume')
        self.assertFalse(any(p.startswith('/workspace/.git') for p in mounts))
        container = self.compose_run('ps', '-q', 'agent').strip()
        actual = json.loads(docker('inspect', container))[0]['Mounts']
        self.assertEqual(next(m['Type'] for m in actual if m['Destination'] == '/workspace'), 'volume')
        for mount in actual:
            if mount['Type'] == 'bind' and Path(mount['Source']).is_relative_to(self.workspace):
                self.assertFalse(mount['RW'])
                if mount['Destination'] == '/nix/store':
                    # The disabled Nix feature mounts this empty core directory.
                    self.assertEqual(mount['Source'], str(self.workspace / '.devcontainer/claude/features/nix-0'))
                else:
                    self.assertIn(mount['Destination'], ('/run/host/generated', '/run/host/nix-env'))

    def test_read_only_dependency_bind(self):
        # The entrypoint and the initial clone accepted it during setUpClass.
        self.assertEqual(self.exec('cat', 'demos/frontend/node_modules/dependency').stdout, 'host-dependency\n')
        result = self.exec('sh', '-c', 'echo agent > demos/frontend/node_modules/dependency', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Read-only file system', result.stderr)
        self.exec('sh', '-ec', 'echo cache > demos/frontend/node_modules/.cache/probe')
        self.assertEqual((self.frontend_dependencies / 'dependency').read_text(), 'host-dependency\n')
        self.assertFalse((self.frontend_dependencies / '.cache/probe').exists())

    def test_existing_clone_and_remote_preserved_after_recreation(self):
        self.exec('git', 'switch', '-c', 'agent/preserved')
        self.exec('sh', '-ec', 'echo dirty >> package-fixture; echo cache > node_modules/preserved')
        remote_before = subprocess.check_output(['git', '-C', str(self.workspace), 'remote', 'get-url', 'agent'], text=True)
        self.exec('python3', '/usr/local/bin/clone-workspace.py')
        # Use the real CLI so its labels, startup gate and generated overrides
        # are retained, exactly like user-requested container recreation.
        result = subprocess.run(['devcontainer', 'up', '--workspace-folder', str(self.workspace),
                                 '--remove-existing-container', '--default-user-env-probe', 'none'],
                                env=self.env, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            self.assertEqual(self.exec('git', 'branch', '--show-current').stdout.strip(), 'agent/preserved')
            self.assertEqual(self.exec('cat', 'package-fixture').stdout.strip(), 'dirty')
            self.assertEqual(self.exec('cat', 'node_modules/preserved').stdout.strip(), 'cache')
            self.assertEqual(subprocess.check_output(['git', '-C', str(self.workspace), 'remote', 'get-url', 'agent'], text=True), remote_before)
            subprocess.run(['git', '-C', str(self.workspace), 'fetch', 'agent', 'refs/heads/agent/preserved'],
                           env=self.env, check=True, capture_output=True)
        finally:
            self.exec('rm', '-f', 'package-fixture', 'node_modules/preserved')
            self.exec('git', 'switch', self.initial_branch)

    def test_host_push_and_fetch_work_with_proxy_stopped(self):
        self.compose_run('stop', 'proxy')
        try:
            subprocess.run(['git', '-C', str(self.workspace), 'push', 'agent', 'HEAD:refs/heads/host/base'],
                           env=self.env, check=True, capture_output=True)
            self.assertEqual(self.exec('git', 'rev-parse', 'host/base').stdout.strip(), self.initial_commit)
            subprocess.run(['git', '-C', str(self.workspace), 'fetch', 'agent'],
                           env=self.env, check=True, capture_output=True)
            self.assertEqual(subprocess.check_output(['git', '-C', str(self.workspace), 'rev-parse', 'agent/host/base'], text=True).strip(),
                             self.initial_commit)
        finally:
            self.compose_run('start', 'proxy')
            self.inject_credentials('api-key')

    def test_dind_bridge_and_host_network_cannot_egress(self):
        control = self.exec('docker', 'run', '--rm', 'busybox:1.37', 'sh', '-c',
                            'nc -l -p 18080 >/dev/null & pid=$!; sleep .1; echo ok | nc -w 1 127.0.0.1 18080; wait "$pid"')
        self.assertEqual(control.returncode, 0)
        for mode in ('bridge', 'host'):
            with self.subTest(mode=mode):
                probe = '''set -eu
for target in '1.1.1.1 443' '8.8.8.8 53' '172.17.0.1 HOSTPORT' 'DINDNET.1 HOSTPORT' 'HOSTIP HOSTPORT'; do
  if timeout 2 sh -c 'echo | nc -w 1 $1 $2' -- $target; then echo escaped; exit 1; fi
done
if timeout 3 nslookup example.com 127.0.0.11; then echo dns-escaped; exit 1; fi
if timeout 3 nslookup example.com 8.8.8.8; then echo dns-escaped; exit 1; fi
if timeout 2 sh -c 'echo | nc -w 1 2001:4860:4860::8888 443'; then echo ipv6-escaped; exit 1; fi
echo nested-network-blocked
'''.replace('HOSTPORT', str(self.host_server.server_port)).replace('HOSTIP', self.host_ip).replace('DINDNET', self.dind_net)
                result = self.exec('docker', 'run', '--rm', '--network', mode, 'busybox:1.37', 'sh', '-c', probe)
                self.assertIn('nested-network-blocked', result.stdout)

    def test_offline_image_build_and_execution(self):
        image = 'cdc-offline-fixture:' + uuid.uuid4().hex[:12]
        self.exec('docker', 'build', '--network=none', '--pull=false', '-t', image,
                  '/workspace/tests/fixtures/offline-build')
        result = self.exec('docker', 'run', '--rm', image)
        self.assertEqual(result.stdout.strip(), 'offline-build-ok')

    def test_docker_published_port_and_agent_restart(self):
        for iteration in range(2):
            if iteration:
                self.compose_run('restart', 'agent')
                # What `devcontainer up` would run after the restart.
                for script in ('copy-host-config.py', 'load-images.py'):
                    subprocess.run([sys.executable, str(self.workspace / '.devcontainer/claude/scripts' / script)],
                                   env=self.env, check=True, timeout=120)
                result = self.exec('sh', '-c', json.loads((CORE / 'template/devcontainer.json').read_text())['postStartCommand'])
                self.assertNotIn('warning', result.stderr)
            result = self.exec('bash', '/workspace/tests/fixtures/bridge_probe.sh', environment={'FIXTURE_PORT': self.port})
            self.assertIn('bridge-probe-ok', result.stdout)
        self.assertNotIn('rror', self.exec('cat', '/run/dind-bridge/serve.log').stdout)

    def test_background_injection_reads_the_credentials_config(self):
        config = self.directory / 'config/claude-devcontainer'
        config.mkdir(parents=True, exist_ok=True)
        (config / 'credentials').write_text('GITHUB_TOKEN=$(echo synthetic-config-token)\n'
                                            'ANTHROPIC_API_KEY=synthetic-config-token\n')
        self.compose_run('exec', '-T', 'proxy', 'rm', '-f', '/run/proxy-credentials/credentials.json')
        result = subprocess.run([sys.executable, str(self.workspace / '.devcontainer/claude/scripts/inject-credentials.py'), '--background'],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for _ in range(30):
            status = self.exec('curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', 'https://api.github.com/user', check=False).stdout
            if status == '200':
                break
            time.sleep(1)
        else:
            self.fail('background injection did not happen: ' + (self.directory / 'state/claude-devcontainer/inject.log').read_text())
        item = json.loads(self.compose_run('exec', '-T', 'proxy', 'tail', '-n', '1', '/tmp/upstream.jsonl'))
        self.assertEqual(dict(item['headers'])['Authorization'],
                         'Basic ' + base64.b64encode(b'x-access-token:synthetic-config-token').decode())
        self.inject_credentials('api-key')

    def test_actual_claude_bash_uses_docker(self):
        result = self.exec('python3', '/workspace/tests/fixtures/claude_probe.py', environment={'FIXTURE_PORT': self.port})
        self.assertIn('actual-claude-docker-ok', result.stdout)

    def test_actual_claude_sees_secrets_masked(self):
        self.exec('sh', '-c', f'echo db.password={SECRET} > /tmp/app.properties')
        # --bare and no setting sources: the managed hooks and shell still mask.
        result = self.exec('python3', '/workspace/tests/fixtures/claude_probe.py', environment={
            'PROBE_COMMAND': 'cat /tmp/app.properties', 'PROBE_EXPECT': 'db.password=' + '*' * len(SECRET),
            'PROBE_FORBID': SECRET, 'PROBE_DONE': 'actual-claude-sumi-ok'})
        self.assertIn('actual-claude-sumi-ok', result.stdout)

    def test_agent_can_neither_read_the_secrets_nor_replace_the_broker(self):
        probe = ('set -u; test -S /run/sumi/mask.sock || { echo no-socket; exit 1; }; '
                 'for op in "rm -f /run/sumi/mask.sock" "touch /run/sumi/x" "rm -f /etc/claude-code/managed-settings.d/90-sumi.json"; do '
                 'if sh -c "$op" 2>/dev/null; then echo "writable: $op"; exit 1; fi; done; '
                 f'found=$(grep -rlsI -D skip {SECRET} / --exclude-dir=proc --exclude-dir=sys --exclude-dir=tmp --exclude-dir=workspace); '
                 'if [ -n "$found" ]; then echo "found in: $found"; exit 1; fi; echo isolated-ok')
        result = self.exec('sh', '-c', probe, check=False)
        self.assertIn('isolated-ok', result.stdout, result.stdout + result.stderr)

    def test_actual_testcontainers_and_ryuk_cleanup(self):
        source = os.environ.get('TESTCONTAINERS_NODE_MODULES')
        if not source:
            self.skipTest('actual Testcontainers not run: set TESTCONTAINERS_NODE_MODULES to pinned fixture dependencies')
        package = Path(source) / 'testcontainers/package.json'
        self.assertEqual(json.loads(package.read_text())['version'], '12.1.0')
        self.exec('npm', 'test', '--offline', '--silent')
        orphan = 'cdc-sdk-orphan-' + uuid.uuid4().hex[:12]
        result = self.exec('node', '/workspace/tests/fixtures/testcontainers_probe.mjs',
                           environment={'SDK_PACKAGE_JSON': '/workspace/package.json', 'SDK_ORPHAN_NAME': orphan, 'DEBUG': 'testcontainers*'})
        self.assertIn('actual-testcontainers-start-wait-port-exec-ok', result.stdout)
        for _ in range(100):
            result = self.exec('docker', 'ps', '-aq', '--filter', 'name=' + orphan)
            if not result.stdout.strip(): break
            time.sleep(.2)
        else: self.fail('Ryuk did not remove orphan')

    def test_z_proxy_stop_fails_closed(self):
        self.compose_run('stop', 'proxy')
        try:
            result = self.exec('curl', '--fail', '--max-time', '3', 'https://api.github.com/user', check=False)
            self.assertNotEqual(result.returncode, 0)
            result = self.exec('python3', '/workspace/tests/fixtures/network_probe.py', self.host_ip, str(self.host_server.server_port))
            self.assertIn('direct-network-blocked', result.stdout)
        finally:
            self.compose_run('start', 'proxy')


if __name__ == '__main__':
    unittest.main()
