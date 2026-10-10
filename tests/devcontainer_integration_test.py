"""Whole-container, offline Docker, actual Claude Bash and SDK integration checks."""
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
# The same settings as .devcontainer/.env; AGENT_IMAGE must be built with this UID/GID.
AGENT_UID = os.environ.get('CLAUDE_DEVCONTAINER_UID', '1000')
AGENT_GID = os.environ.get('CLAUDE_DEVCONTAINER_GID', '1000')
DIND_NET = os.environ.get('CLAUDE_DEVCONTAINER_DIND_NET', '10.231.71')


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class DevcontainerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docker('info')
        docker('image', 'inspect', AGENT_IMAGE, PROXY_IMAGE, 'busybox:1.37', 'testcontainers/ryuk:0.14.0')
        cls.project = 'cdc-integration-' + uuid.uuid4().hex[:12]
        cls.directory = Path(tempfile.mkdtemp(prefix='cdc-integration-'))
        cls.addClassCleanup(shutil.rmtree, cls.directory)
        fixtures(cls.directory)
        auth_variables = ('GITHUB_TOKEN', 'GH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY',
                          'ANTHROPIC_AUTH_TOKEN')
        cls.env = {k: v for k, v in os.environ.items() if k not in auth_variables}
        # initializeCommand reads the credentials config; never the developer's own.
        cls.env.update(XDG_CONFIG_HOME=str(cls.directory / 'config'), XDG_STATE_HOME=str(cls.directory / 'state'))
        # initializeCommand copies the Claude Code configuration; never the developer's own.
        claude = cls.directory / 'claude'
        (claude / 'skills/fixture').mkdir(parents=True)
        (claude / 'skills/fixture/SKILL.md').write_text('fixture skill')
        (cls.directory / 'statusline.sh').write_text('echo fixture-status')
        (claude / 'statusline.sh').symlink_to(cls.directory / 'statusline.sh')
        (claude / 'settings.json').write_text(json.dumps({'statusLine': {'type': 'command', 'command': 'sh ~/.claude/statusline.sh'}}))
        (claude / '.credentials.json').write_text('synthetic-credentials')
        cls.env.update(CLAUDE_CONFIG_DIR=str(claude))
        cls.start_environment()
        denied = cls.exec('curl', '--max-time', '5', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                          'https://api.github.com/user')
        assert denied.stdout == '403', denied.stdout + denied.stderr
        assert cls.compose_run('exec', '-T', 'proxy', 'sh', '-c', 'test ! -e /tmp/upstream.jsonl') == ''
        cls.inject_credentials('api-key')
        # Copy, rather than mount, synthetic offline image archives into agent.
        archive = cls.directory / 'images.tar'
        subprocess.run(['docker', 'save', '-o', str(archive), 'busybox:1.37', 'testcontainers/ryuk:0.14.0'], check=True, capture_output=True, timeout=120)
        cls.compose_run('cp', str(archive), 'agent:/tmp/cdc-images.tar')
        cls.exec('docker', 'load', '-i', '/tmp/cdc-images.tar')
        cls.exec('rm', '/tmp/cdc-images.tar')
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
        shutil.copytree(CORE, cls.workspace / '.devcontainer/claude', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('devcontainer.json', 'compose.yaml', 'agent-setup.sh'):
            shutil.copy(CORE / 'template' / name, cls.workspace / '.devcontainer' / name)
        shutil.copytree(ROOT / 'tests', cls.workspace / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        git = ['git', '-C', str(cls.workspace), '-c', 'user.name=t', '-c', 'user.email=t@example.com']
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'fixture']):
            subprocess.run([*git, *args], check=True)
        # Delay the production entrypoint so that `up` must wait for postStartCommand.
        startup = cls.directory / 'entrypoint.sh'
        startup.write_text('#!/bin/sh\nsleep 3\nexec /usr/local/bin/entrypoint.sh "$@"\n')
        startup.chmod(0o755)
        override = cls.directory / 'override.json'
        override.write_text(json.dumps({'services': {
            'agent': {'image': AGENT_IMAGE, 'volumes': [str(cls.directory) + ':/startup-fixture:ro'],
                      'entrypoint': ['/startup-fixture/entrypoint.sh']},
            'proxy': {'image': PROXY_IMAGE, 'entrypoint': ['python', '/fixtures/proxy_server.py'],
                      'volumes': [str(cls.directory) + ':/fixtures:ro']}}}))
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
        environment = {**cls.env, 'GITHUB_TOKEN': 'synthetic-managed-token'}
        environment['CLAUDE_CODE_OAUTH_TOKEN' if mode == 'oauth' else 'ANTHROPIC_API_KEY'] = 'synthetic-managed-token'
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

    def test_host_claude_configuration_is_copied_before_up_returns(self):
        result = self.exec('sh', '-c', 'cd ~/.claude && cat skills/fixture/SKILL.md && sh statusline.sh && ls -A')
        self.assertIn('fixture skill', result.stdout)
        self.assertIn('fixture-status', result.stdout)
        self.assertNotIn('.credentials.json', result.stdout)

    def test_whole_container_direct_egress_dns_ipv6_host_blocked(self):
        result = self.exec('python3', '/workspace/tests/fixtures/network_probe.py', self.host_ip, str(self.host_server.server_port))
        self.assertIn('direct-network-blocked', result.stdout)

    def test_devcontainer_directory_is_read_only(self):
        probe = ('set -u; cd /workspace/.devcontainer; '
                 'for op in "touch new" "echo x >> claude/proxy/addon.py" "rm devcontainer.json" "mv claude moved"; do '
                 'if sh -c "$op" 2>/dev/null; then echo "writable: $op"; exit 1; fi; done; '
                 'touch /workspace/.write-check && rm /workspace/.write-check && echo read-only-ok')
        result = self.exec('sh', '-c', probe, check=False)
        self.assertIn('read-only-ok', result.stdout, result.stdout + result.stderr)

    def test_git_settings_are_read_only_but_commits_work(self):
        probe = ('set -u; cd /workspace; '
                 'for op in "git config core.fsmonitor evil" "echo x > .git/hooks/pre-commit" '
                 '"echo x >> .git/config" "mkdir .git/modules/x" "mv .git .git.old"; do '
                 'if sh -c "$op" 2>/dev/null; then echo "writable: $op"; exit 1; fi; done; '
                 'git checkout -qb agent-branch && echo change > agent-file && git add agent-file '
                 '&& git -c user.name=a -c user.email=a@example.com commit -qm agent && echo git-ok')
        result = self.exec('sh', '-c', probe, check=False)
        self.assertIn('git-ok', result.stdout, result.stdout + result.stderr)
        log = subprocess.run(['git', '-C', str(self.workspace), 'log', '-1', '--format=%s', 'agent-branch'],
                             capture_output=True, text=True, check=True)
        self.assertEqual(log.stdout.strip(), 'agent')

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
'''.replace('HOSTPORT', str(self.host_server.server_port)).replace('HOSTIP', self.host_ip).replace('DINDNET', DIND_NET)
                result = self.exec('docker', 'run', '--rm', '--network', mode, 'busybox:1.37', 'sh', '-c', probe)
                self.assertIn('nested-network-blocked', result.stdout)

    def test_offline_image_build_and_execution(self):
        image = 'cdc-offline-fixture:' + uuid.uuid4().hex[:12]
        self.exec('docker', 'build', '--network=none', '--pull=false', '-t', image,
                  '/workspace/tests/fixtures/offline-build')
        result = self.exec('docker', 'run', '--rm', image)
        self.assertEqual(result.stdout.strip(), 'offline-build-ok')

    def test_docker_published_port_namespace_and_agent_restart(self):
        for iteration in range(2):
            if iteration:
                self.compose_run('restart', 'agent')
                # What `devcontainer up` would run after the restart.
                self.exec('sh', '-c', json.loads((CORE / 'template/devcontainer.json').read_text())['postStartCommand'])
            for args in (['bash', '/workspace/tests/fixtures/bridge_probe.sh'],
                         ['unshare', '--user', '--map-root-user', '--net', 'python3', '/workspace/tests/fixtures/netns_exec.py', 'bash', '/workspace/tests/fixtures/bridge_probe.sh', 'sandbox']):
                result = self.exec(*args, environment={'FIXTURE_PORT': self.port})
                self.assertIn('bridge-probe-ok', result.stdout)
        self.assertNotIn('rror', self.exec('cat', '/run/dind-bridge/serve.log').stdout)

    def test_background_injection_reads_the_credentials_config(self):
        config = self.directory / 'config/claude-devcontainer'
        config.mkdir(parents=True)
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
        self.assertEqual(dict(item['headers'])['Authorization'], 'Bearer synthetic-config-token')
        self.inject_credentials('api-key')

    def test_actual_claude_bash_uses_bridge_in_distinct_namespace(self):
        result = self.exec('python3', '/workspace/tests/fixtures/claude_probe.py', environment={'FIXTURE_PORT': self.port})
        self.assertIn('actual-claude-sandbox-and-docker-ok', result.stdout)

    def test_actual_testcontainers_and_ryuk_cleanup(self):
        source = os.environ.get('TESTCONTAINERS_NODE_MODULES')
        if not source:
            self.skipTest('actual Testcontainers not run: set TESTCONTAINERS_NODE_MODULES to pinned fixture dependencies')
        package = Path(source) / 'testcontainers/package.json'
        self.assertEqual(json.loads(package.read_text())['version'], '12.1.0')
        self.compose_run('cp', source, 'agent:/tmp/cdc-sdk-modules')
        self.exec('sh', '-c', 'printf \'{"private":true}\' > /tmp/package.json; ln -sfn /tmp/cdc-sdk-modules /tmp/node_modules')
        orphan = 'cdc-sdk-orphan-' + uuid.uuid4().hex[:12]
        script = 'source "$CLAUDE_ENV_FILE"; exec node /workspace/tests/fixtures/testcontainers_probe.mjs'
        result = self.exec('unshare', '--user', '--map-root-user', '--net', 'python3', '/workspace/tests/fixtures/netns_exec.py', 'bash', '-c', script,
                           environment={'SDK_PACKAGE_JSON': '/tmp/package.json', 'SDK_ORPHAN_NAME': orphan, 'DEBUG': 'testcontainers*'})
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
