"""How settings.py reads claude.toml and claude.local.toml and what it writes; no Docker needed."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.devcontainer/claude/scripts/settings.py'
spec = importlib.util.spec_from_file_location('settings', SCRIPT)
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)

GITHUB = '''
[proxy.hosts."api.github.com"]
routes = ["GET /user"]
inject = { Authorization = { basic = "x-access-token:${GITHUB_TOKEN}" } }
env = ["GH_TOKEN"]
'''


class SettingsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / '.devcontainer').mkdir()
        patcher = mock.patch.object(settings, 'CONFIG', self.root / 'user-config')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_user_path_resolves_profile_links_and_quotes_shell_values(self):
        settings.CONFIG.mkdir()
        store_bin = self.root / 'store/package with $literal/bin'
        store_bin.mkdir(parents=True)
        profile = self.root / 'profile'
        profile.symlink_to(store_bin.parent)
        (settings.CONFIG / 'claude.toml').write_text(
            '[agent]\nextra_path = ' + json.dumps([str(profile / 'bin'), '/container-only/bin']) + '\n')
        out = self.generate()
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                                 '. "$1"; printf %s "$PATH"', 'bash', str(out / 'shell-path.sh')],
                                env={'PATH': '/usr/bin:/bin'}, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout, f'{store_bin}:/container-only/bin:/usr/bin:/bin')
        (settings.CONFIG / 'claude.toml').unlink()
        self.generate()
        self.assertEqual((out / 'shell-path.sh').read_text(), '')

    def test_host_mode_reads_only_the_global_settings(self):
        state = self.root / 'state'
        self.write('claude.toml', '[docker]\nenabled = true\n')
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'claude.toml').write_text('[agent]\nuid = 1234\n')
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_CONFIG_DIR': str(state)}):
            loaded = settings.generate(self.root, {})
        self.assertFalse(loaded['docker']['enabled'])
        self.assertEqual(loaded['agent']['uid'], 1234)
        self.assertTrue((state / '.generated/settings.env').is_file())
        self.assertFalse((self.root / '.devcontainer/.generated').exists())

    def test_host_mode_shared_mounts_skip_devcontainer(self):
        shutil.rmtree(self.root / '.devcontainer')
        (self.root / '.git').mkdir()
        state = self.root / 'state'
        loaded = copy.deepcopy(settings.DEFAULTS)
        loaded['git']['isolation'] = 'shared'
        loaded['sumi']['secrets'] = Path('/nonexistent/secrets.txt')
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_CONFIG_DIR': str(state)}):
            mounts = settings.shared_mounts(loaded, self.root)
        targets = [mount['target'] for mount in mounts]
        self.assertNotIn('/workspace/.devcontainer', targets)
        self.assertIn('/workspace/.git', targets)

    def test_user_path_rejects_invalid_settings(self):
        settings.CONFIG.mkdir()
        for content in ('paths = []', 'extra_path = "bin"', 'extra_path = [1]',
                        'extra_path = ["relative/bin"]', 'extra_path = ["/bin:/other"]'):
            with self.subTest(content=content):
                (settings.CONFIG / 'claude.toml').write_text('[agent]\n' + content)
                with self.assertRaises(ValueError):
                    self.generate()

    def write(self, name, text):
        (self.root / '.devcontainer' / name).write_text(text)

    def load(self):
        return settings.load(self.root)

    def generate(self, environ=None):
        settings.generate(self.root, {} if environ is None else environ)
        return self.root / '.devcontainer/.generated'

    def env(self):
        lines = (self.root / '.devcontainer/.generated/settings.env').read_text().splitlines()
        return dict(line.split('=', 1) for line in lines if not line.startswith('#'))

    def test_defaults(self):
        loaded = self.load()
        self.assertEqual(loaded['git'], {'isolation': 'clone', 'shared_hidden_paths': []})
        self.assertEqual(loaded['agent'], {'uid': 1000, 'gid': 1000, 'extra_path': [], 'env': {}})
        self.assertEqual(loaded['acp'], {'enabled': False, 'version': '0.90.0'})
        self.assertEqual(loaded['docker'], {'enabled': False, 'images': [], 'volume': ''})
        self.assertEqual(loaded['nix'], {'enabled': False, 'ref': 'HEAD', 'shell': ''})
        self.assertEqual(loaded['sumi']['secrets'], Path(settings.DEFAULTS['sumi']['secrets']))
        self.assertEqual(loaded['codeServer'], {'enabled': False, 'extensions': []})
        self.assertEqual(loaded['proxy'], {'ca': '', 'connectors': [], 'web_search': False, 'hosts': {}})
        # load() must not change the defaults it starts from.
        self.write('claude.toml', '[sumi]\nsecrets = "s.txt"\n')
        self.load()
        self.assertIsInstance(settings.DEFAULTS['sumi']['secrets'], str)

    def test_global_defaults_then_workspace_then_local(self):
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'claude.toml').write_text(
            '[agent]\nuid = 1234\ngid = 1234\nextra_path = ["/global/bin"]\n'
            '[proxy]\nca = "cert.pem"\n')
        self.write('claude.toml', '[agent]\ngid = 2345\n')
        loaded = self.load()
        self.assertEqual(loaded['agent'], {'uid': 1234, 'gid': 2345, 'extra_path': ['/global/bin'], 'env': {}})
        self.assertEqual(loaded['proxy']['ca'], settings.CONFIG / 'cert.pem')
        self.write('claude.local.toml', '[agent]\ngid = 3456\nextra_path = []\n[proxy]\nca = "local.pem"\n')
        loaded = self.load()
        self.assertEqual(loaded['agent'], {'uid': 1234, 'gid': 3456, 'extra_path': [], 'env': {}})
        self.assertEqual(loaded['proxy']['ca'], self.root / '.devcontainer/local.pem')

    def test_local_merges_tables_and_replaces_values(self):
        self.write('claude.toml', '[docker]\nenabled = true\nimages = ["a:1", "b:1"]\n[agent]\nuid = 1001\n')
        self.write('claude.local.toml', '[docker]\nimages = ["c:1"]\n[agent]\ngid = 1002\n')
        loaded = self.load()
        self.assertEqual(loaded['docker'], {'enabled': True, 'images': ['c:1'], 'volume': ''})
        self.assertEqual(loaded['agent'], {'uid': 1001, 'gid': 1002, 'extra_path': [], 'env': {}})

    def test_local_replaces_each_host_as_a_whole(self):
        self.write('claude.toml', GITHUB + '[proxy.hosts."other.example.com"]\nroutes = ["/"]\n')
        self.write('claude.local.toml', '[proxy.hosts."api.github.com"]\nroutes = ["POST /graphql"]\n')
        hosts = self.load()['proxy']['hosts']
        self.assertEqual(hosts['api.github.com'], {'routes': ['POST /graphql']})
        self.assertEqual(hosts['other.example.com'], {'routes': ['/']})

    def test_http_host(self):
        self.write('claude.toml', '[proxy.hosts."mirror.example.com"]\nroutes = ["GET /.+"]\nhttp = true\n')
        self.assertEqual(self.load()['proxy']['hosts'], {'mirror.example.com': {'routes': ['GET /.+'], 'http': True}})

    def test_errors_name_the_file_and_the_setting(self):
        cases = [
            ('[git]\nisolation = "worktree"\n', r'git.isolation: expected "clone" or "shared"'),
            ('[git]\nisolation = true\n', r'git.isolation: expected a string'),
            ('[git]\nshared_writable = true\n', r'git.shared_writable: unknown setting'),
            ('[agent]\nuser = 1\n', r'claude.local.toml: agent.user: unknown setting'),
            ('[dind]\nenabled = true\n', r'claude.local.toml: unknown table \[dind\]'),
            ('[tode]\nenabled = true\n', r'claude.local.toml: unknown table \[tode\]'),
            ('[docker]\nenabled = 1\n', r'docker.enabled: expected true or false'),
            ('[acp]\nenabled = 1\n', r'acp.enabled: expected true or false'),
            ('[acp]\nenable = true\n', r'acp.enable: unknown setting'),
            ('[acp]\nversion = 90\n', r'acp.version: expected a string'),
            ('[acp]\nversion = "latest"\n', r'acp.version: expected an exact version'),
            ('[acp]\nversion = "^0.90.0"\n', r'acp.version: expected an exact version'),
            ('[acp]\nversion = "0.90.0 --force"\n', r'acp.version: expected an exact version'),
            ('[acp]\nenabled = true\n', r'acp.enabled: needs git.isolation = "shared"'),
            ('[git]\nisolation = "clone"\n[acp]\nenabled = true\n', r'acp.enabled: needs git.isolation'),
            ('[agent]\nuid = true\n', r'agent.uid: expected an integer'),
            ('[agent]\nuid = 0\n', r'agent.uid: must be positive'),
            ('[docker]\nimages = "busybox"\n', r'docker.images: expected a list'),
            ('[docker]\nimages = [1]\n', r'docker.images: expected a list'),
            ('[docker]\nimages = ["a b"]\n', r'docker.images:'),
            ('[docker]\nenabled = true\nvolume = "a/b"\n', r'docker.volume: .a/b. is not a Docker volume name'),
            ('[docker]\nenabled = true\nvolume = 1\n', r'docker.volume: expected a string'),
            ('[proxy]\nconnectors = ["drive"]\n', r'proxy.connectors:'),
            ('[proxy]\nweb_search = "true"\n', r'proxy.web_search: expected true or false'),
            ('[proxy]\nweb_search = 1\n', r'proxy.web_search: expected true or false'),
            ('[codeServer]\nenabled = true\nextensions = ["python"]\n', r'codeServer.extensions: .python. is not'),
            ('[codeServer]\nenabled = true\nextensions = ["a.b c"]\n', r'codeServer.extensions:'),
            ('[codeServer]\nextensions = ["ms-python.python"]\n', r'codeServer.extensions: needs codeServer.enabled'),
            ('[codeServer]\nenabled = true\nextensions = "a.b"\n', r'codeServer.extensions: expected a list'),
            ('[codeServer]\nvscode = "/usr/bin/code"\n', r'codeServer.vscode: unknown setting'),
            ('[subnets]\nproxy = "10.0.256"\n', r'subnets.proxy:'),
            ('[subnets]\nproxy = "10.0"\n', r'subnets.proxy:'),
            ('[nix]\nshell = "a\'b"\n', r"nix.shell: newlines and '"),
            ('[proxy.hosts."api.anthropic.com"]\nroutes = ["/"]\n', r'proxy.hosts."api.anthropic.com": authenticated'),
            ('[proxy.hosts."a.example.com"]\nroutes = ["get /"]\n', r'proxy.hosts."a.example.com".routes\[0\]'),
            ('[proxy.hosts."a.example.com"]\nroutes = ["/"]\nport = 80\n', r'unknown port'),
            ('[proxy.hosts."a.example.com"]\nroutes = ["/"]\nhttp = true\ninject = { X-A = "${A}" }\n',
             r'proxy.hosts."a.example.com".http: plain HTTP'),
            ('agent = 1\n', r'agent: expected a table'),
            ('[agent\n', r'claude.local.toml: '),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                self.write('claude.local.toml', text)
                with self.assertRaisesRegex(ValueError, message):
                    self.load()

    def test_unquoted_host_names_are_explained(self):
        self.write('claude.toml', '[proxy.hosts.api.github.com]\nroutes = ["/"]\n')
        with self.assertRaisesRegex(ValueError, r'proxy.hosts."api": unknown github\. Quote host names'):
            self.load()
        self.write('claude.toml', '[proxy.hosts."api.github.com"]\nroute = ["/"]\n')
        with self.assertRaisesRegex(ValueError, r'proxy.hosts."api.github.com": unknown route$'):
            self.load()

    def test_subnets_must_differ(self):
        self.write('claude.toml', '[subnets]\nproxy = "10.99.0"\ndind = "10.99.0"\n')
        with self.assertRaisesRegex(ValueError, 'must differ'):
            self.load()

    def test_dind_uid_outside_the_subordinate_ids(self):
        self.write('claude.toml', '[agent]\nuid = 100000\n')
        self.load()
        self.write('claude.toml', '[agent]\nuid = 100000\n[docker]\nenabled = true\n')
        with self.assertRaisesRegex(ValueError, 'agent.uid: 100000 is used by DinD'):
            self.load()

    def test_dind_volume_is_external_when_shared(self):
        # Ignored without DinD, so that the global claude.toml can name it.
        self.write('claude.toml', '[docker]\nvolume = "shared-dind"\n')
        out = self.generate()
        self.assertNotIn('volumes', json.loads((out / 'workspace.compose.yaml').read_text()))
        self.assertEqual(self.check_dind_volume(None, ''), [])
        self.write('claude.toml', '[docker]\nenabled = true\n')
        self.generate()
        self.assertNotIn('volumes', json.loads((out / 'workspace.compose.yaml').read_text()))
        self.write('claude.toml', '[docker]\nenabled = true\nvolume = "shared-dind"\n')
        self.generate()
        self.assertEqual(json.loads((out / 'workspace.compose.yaml').read_text())['volumes'],
                         {'dind-data': {'name': 'shared-dind', 'external': True}})

    def check_dind_volume(self, labels, users, own='own'):
        """check_dind_volume with Docker answering labels (None: no volume) and users."""
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ['docker', 'volume', 'inspect']:
                return subprocess.CompletedProcess(command, 1 if labels is None else 0, json.dumps(labels), '')
            if command[:3] == ['docker', 'ps', '-a']:
                return subprocess.CompletedProcess(command, 0, own + '\n', '')
            if command[:2] == ['docker', 'ps']:
                return subprocess.CompletedProcess(command, 0, users, '')
            return subprocess.CompletedProcess(command, 0, '', '')
        with mock.patch.object(settings.subprocess, 'run', run):
            settings.check_dind_volume(self.root)
        return calls

    def test_check_dind_volume(self):
        self.assertEqual(self.check_dind_volume(None, ''), [])
        self.write('claude.toml', '[docker]\nenabled = true\nvolume = "shared-dind"\n[agent]\nuid = 1001\n')
        calls = self.check_dind_volume(None, '')
        self.assertIn(['docker', 'volume', 'create', '--label', 'dev.claude-devcontainer.uid=1001', 'shared-dind'], calls)
        # This workspace's own DinD may hold it; a user-made volume has no label.
        self.check_dind_volume({'dev.claude-devcontainer.uid': '1001'}, 'own-dind-1\town\n')
        self.check_dind_volume(None, '')
        with self.assertRaisesRegex(ValueError, 'in use by other-dind-1; stop that environment'):
            self.check_dind_volume({}, 'other-dind-1\tother\n')
        with self.assertRaisesRegex(ValueError, 'agent.uid = 1000, not 1001'):
            self.check_dind_volume({'dev.claude-devcontainer.uid': '1000'}, '')

    def test_dummy_env_names(self):
        for name in ['HTTP_PROXY', 'https_proxy', 'NO_PROXY', 'DOCKER_HOST', 'CLAUDE_CODE_OAUTH_TOKEN',
                     'TESTCONTAINERS_HOST_OVERRIDE', 'CLAUDE_DEVCONTAINER_DIND']:
            with self.subTest(name=name):
                self.write('claude.toml', f'[proxy.hosts."a.example.com"]\nroutes = ["/"]\nenv = ["{name}"]\n')
                with self.assertRaisesRegex(ValueError, f'env: {name} is set by compose'):
                    self.load()
        for env in ['"GH_TOKEN"', '["1X"]', '["A B"]']:
            with self.subTest(env=env):
                self.write('claude.toml', f'[proxy.hosts."a.example.com"]\nroutes = ["/"]\nenv = {env}\n')
                with self.assertRaisesRegex(ValueError, 'env: must list environment variable names'):
                    self.load()

    def test_agent_env_merges_by_name_and_is_literal(self):
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'claude.toml').write_text('[agent.env]\nEDITOR = "vim"\nLANGUAGE = "ja"\n')
        self.write('claude.toml', GITHUB + '[agent]\nenv = { JAVA_OPTS = "-Xmx2g -Dx=$HOME", LANGUAGE = "en" }\n')
        self.write('claude.local.toml', '[agent.env]\nEMPTY = ""\n')
        out = self.generate()
        self.assertEqual([line for line in (out / 'agent.env').read_text().splitlines() if not line.startswith('#')],
                         ['EDITOR="vim"', 'LANGUAGE="en"', 'JAVA_OPTS="-Xmx2g -Dx=$$HOME"', 'EMPTY=""',
                          'GH_TOKEN=managed-by-proxy'])
        if shutil.which('docker') and subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode == 0:
            (out / 'compose.yaml').write_text('services:\n  a:\n    image: x\n    env_file: [agent.env]\n')
            config = subprocess.run(['docker', 'compose', '-f', str(out / 'compose.yaml'), 'config', '--format', 'json'],
                                    capture_output=True, text=True, check=True, env={'PATH': os.environ['PATH']})
            # Not interpolated; config escapes the literal $ again in what it prints.
            self.assertEqual(json.loads(config.stdout)['services']['a']['environment']['JAVA_OPTS'], '-Xmx2g -Dx=$$HOME')

    def test_agent_env_errors(self):
        for text, message in [('[agent]\nenv = { HTTPS_PROXY = "x" }\n', 'HTTPS_PROXY is set by compose'),
                              ('[agent]\nenv = { CLAUDE_DEVCONTAINER_DIND = "1" }\n', 'is set by compose'),
                              ('[agent]\nenv = { "A B" = "x" }\n', 'is not an environment variable name'),
                              ('[agent]\nenv = { A = 1 }\n', r'agent.env.A: expected a string'),
                              ("[agent]\nenv = { A = \"it's\" }\n", r'agent.env.A: expected a string'),
                              ('[agent]\nenv = { A = "a\\nb" }\n', r'agent.env.A: expected a string'),
                              ('[agent]\nenv = ["A=x"]\n', r'agent.env: expected a table'),
                              (GITHUB + '[agent]\nenv = { GH_TOKEN = "x" }\n', 'GH_TOKEN is also the env of a host')]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self.write('claude.toml', text)
                self.load()

    @unittest.skipUnless(shutil.which('docker'), 'no Docker Compose')
    def test_agent_env_special_characters_round_trip_through_compose(self):
        values = {'TRAILING': 'path\\', 'BACKSLASHES': r'a\\b',
                  'DOLLARS': '$HOME ${HOME} $$', 'QUOTES': '"hello"',
                  'LITERAL_ESCAPE': r'\n', 'CARRIAGE_RETURN': 'a\rb', 'AFTER': 'ok'}
        out = self.generate()
        (out / 'agent.env').write_text(settings.agent_env({'agent': {'env': values}, 'proxy': {'hosts': {}}}))
        (out / 'compose.yaml').write_text('services:\n  a:\n    image: x\n    env_file: [agent.env]\n')
        result = subprocess.run(['docker', 'compose', '-f', str(out / 'compose.yaml'), 'config', '--format', 'json'],
                                capture_output=True, text=True, check=True, env={'PATH': os.environ['PATH']})
        actual = json.loads(result.stdout)['services']['a']['environment']
        self.assertEqual(actual, {name: value.replace('$', '$$') for name, value in values.items()})

    def test_global_agent_setup_is_copied_for_the_build(self):
        out = self.generate()
        self.assertFalse((out / 'global-agent-setup.sh').exists())
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'agent-setup.sh').write_text('apt-get install -y ripgrep\n')
        self.generate()
        self.assertEqual((out / 'global-agent-setup.sh').read_text(), 'apt-get install -y ripgrep\n')
        (settings.CONFIG / 'agent-setup.sh').unlink()
        self.generate()
        self.assertFalse((out / 'global-agent-setup.sh').exists())

    def test_paths_are_relative_to_devcontainer_and_expand_home(self):
        self.write('claude.toml', '[sumi]\nsecrets = "../.secrets/sumi/secrets.txt"\n[proxy]\nca = "ca.pem"\n')
        loaded = self.load()
        self.assertEqual(loaded['sumi']['secrets'], self.root / '.secrets/sumi/secrets.txt')
        self.assertEqual(loaded['proxy']['ca'], self.root / '.devcontainer/ca.pem')
        self.write('claude.toml', '[sumi]\nsecrets = "~/s/secrets.txt"\n')
        self.assertEqual(self.load()['sumi']['secrets'], Path.home() / 's/secrets.txt')

    def test_generate(self):
        (self.root / 'ca.pem').write_text('')
        (self.root / '.secrets').mkdir()
        (self.root / '.secrets/list.txt').write_text('secret\n')
        self.write('claude.toml', GITHUB + '''
[proxy.hosts."github.com"]
routes = ["/"]
env = ["GH_TOKEN", "GIT_TOKEN"]
[agent]
uid = 1001
[docker]
enabled = true
images = ["busybox:1.37", "postgres:16"]
[nix]
shell = "devShells.x86_64-linux.$foo"
[sumi]
enabled = true
secrets = "../.secrets/list.txt"
[codeServer]
enabled = true
extensions = ["ms-python.python", "golang.go@0.48.0"]
[proxy]
ca = "../ca.pem"
connectors = ["mcpsrv_01A", "mcprs_02B"]
[subnets]
dind = "10.99.1"
''')
        out = self.generate()
        self.assertEqual({k: v for k, v in self.env().items() if '_NODE_' not in k}, {
            'CLAUDE_DEVCONTAINER_GIT_ISOLATION': "'clone'",
            'CLAUDE_DEVCONTAINER_UID': "'1001'", 'CLAUDE_DEVCONTAINER_GID': "'1000'",
            'CLAUDE_DEVCONTAINER_DIND': "'1'", 'CLAUDE_DEVCONTAINER_NIX': "'0'",
            'CLAUDE_DEVCONTAINER_NIX_REF': "'HEAD'",
            # Single quotes keep compose from interpolating $.
            'CLAUDE_DEVCONTAINER_NIX_SHELL': "'devShells.x86_64-linux.$foo'",
            'CLAUDE_DEVCONTAINER_SUMI': "'1'",
            'CLAUDE_DEVCONTAINER_SUMI_DIR': f"'{self.root / '.secrets'}'",
            'CLAUDE_DEVCONTAINER_SUMI_FILE': "'list.txt'",
            'CLAUDE_DEVCONTAINER_CODE_SERVER': "'1'",
            'CLAUDE_DEVCONTAINER_CODE_SERVER_EXTENSIONS': "'ms-python.python golang.go@0.48.0'",
            'CLAUDE_DEVCONTAINER_ACP': "'0'", 'CLAUDE_DEVCONTAINER_ACP_VERSION': "'0.90.0'",
            'CLAUDE_DEVCONTAINER_PROXY_CA': f"'{self.root / 'ca.pem'}'",
            'CLAUDE_DEVCONTAINER_CONNECTORS': "'mcpsrv_01A,mcprs_02B'",
            'CLAUDE_DEVCONTAINER_WEB_SEARCH': "'0'",
            # Unset, pick-networks.py chooses the proxy's.
            'CLAUDE_DEVCONTAINER_DIND_NET': "'10.99.1'",
        })
        self.assertEqual((out / 'images.txt').read_text(), 'busybox:1.37\npostgres:16\n')
        self.assertEqual([line for line in (out / 'agent.env').read_text().splitlines() if not line.startswith('#')],
                         ['GH_TOKEN=managed-by-proxy', 'GIT_TOKEN=managed-by-proxy'])
        self.assertEqual(json.loads((out / 'proxy.json').read_text()), {'hosts': {
            'api.github.com': {'routes': ['GET /user'],
                               'inject': {'Authorization': {'basic': 'x-access-token:${GITHUB_TOKEN}'}}},
            'github.com': {'routes': ['/']}}})
        self.assertEqual((out / '.gitignore').read_text(), '*\n')

    def test_code_server_opt_in(self):
        for editor in (False, True):
            with self.subTest(editor=editor):
                self.write('claude.toml', f'[codeServer]\nenabled = {str(editor).lower()}\n')
                values = settings.variables(self.load())
                self.assertEqual(values['CLAUDE_DEVCONTAINER_CODE_SERVER'], str(int(editor)))
        self.write('claude.local.toml', '[codeServer]\nenabled = false\n')
        self.assertFalse(self.load()['codeServer']['enabled'])
        self.write('claude.local.toml', '[codeServer]\nenabled = "true"\n')
        with self.assertRaisesRegex(ValueError, 'codeServer.enabled: expected true or false'):
            self.load()

    def test_code_server_extension_override(self):
        self.write('claude.toml', '[codeServer]\nenabled = true\nextensions = ["ms-python.python"]\n')
        self.write('claude.local.toml', '[codeServer]\nextensions = ["vscodevim.vim@1.27.3", "a.b"]\n')
        self.generate()
        self.assertEqual(self.env()['CLAUDE_DEVCONTAINER_CODE_SERVER_EXTENSIONS'], "'vscodevim.vim@1.27.3 a.b'")
        self.write('claude.local.toml', '[codeServer]\nextensions = []\n')
        self.assertEqual(self.load()['codeServer']['extensions'], [])

    def test_generate_with_defaults(self):
        out = self.generate()
        self.assertEqual(self.env()['CLAUDE_DEVCONTAINER_PROXY_CA'], "''")
        self.assertNotIn('CLAUDE_DEVCONTAINER_PROXY_NET', self.env())
        self.assertEqual((out / 'images.txt').read_text(), '')
        self.assertEqual(json.loads((out / 'proxy.json').read_text()), {'hosts': {}})

    def test_web_search_opt_in_and_local_override(self):
        for workspace, local, expected in [(None, None, '0'), ('true', None, '1'),
                                            ('true', 'false', '0'), (None, 'true', '1')]:
            with self.subTest(workspace=workspace, local=local):
                for name, value in [('claude.toml', workspace), ('claude.local.toml', local)]:
                    self.write(name, '' if value is None else f'[proxy]\nweb_search = {value}\n')
                self.generate()
                self.assertEqual(self.env()['CLAUDE_DEVCONTAINER_WEB_SEARCH'], f"'{expected}'")

    def test_generate_checks_the_files_named(self):
        self.write('claude.toml', '[sumi]\nenabled = true\nsecrets = "missing.txt"\n')
        with self.assertRaisesRegex(ValueError, r'sumi.enabled needs .*missing.txt'):
            self.generate()
        self.write('claude.toml', '[proxy]\nca = "missing.pem"\n')
        with self.assertRaisesRegex(ValueError, r'proxy.ca names .*missing.pem'):
            self.generate()

    def test_paths_with_a_quote_are_refused(self):
        (self.root / "it's").mkdir()
        (self.root / "it's/secrets.txt").write_text('x\n')
        self.write('claude.toml', '[sumi]\nenabled = true\nsecrets = "~/secrets.txt"\n')
        with mock.patch.dict(os.environ, HOME=str(self.root / "it's")):
            with self.assertRaisesRegex(ValueError, "sumi.secrets: .*newlines and '"):
                self.generate()

    def test_settings_outside_the_files_are_refused(self):
        with self.assertRaisesRegex(ValueError, r'CLAUDE_DEVCONTAINER_DIND in the environment.*env -u'):
            self.generate({'CLAUDE_DEVCONTAINER_DIND': '1'})
        with self.assertRaisesRegex(ValueError, r'CLAUDE_DEVCONTAINER_WEB_SEARCH in the environment.*env -u'):
            self.generate({'CLAUDE_DEVCONTAINER_WEB_SEARCH': '1'})
        self.write('.env', '# CLAUDE_DEVCONTAINER_UID=1\nexport CLAUDE_DEVCONTAINER_SUMI_DIR=/x\nOTHER=1\n')
        with self.assertRaisesRegex(ValueError, r'found CLAUDE_DEVCONTAINER_SUMI_DIR in .devcontainer/.env\.'):
            self.generate()
        for line in ['CLAUDE_DEVCONTAINER_DIND: 1', 'CLAUDE_DEVCONTAINER_DIND=', '  export  CLAUDE_DEVCONTAINER_DIND = 1',
                     'CLAUDE_DEVCONTAINER_DIND']:
            with self.subTest(line=line):
                self.write('.env', line + '\n')
                with self.assertRaisesRegex(ValueError, r'found CLAUDE_DEVCONTAINER_DIND in .devcontainer/.env\.'):
                    self.generate()
        (self.root / '.devcontainer/.env').write_text('COMPOSE_PROJECT_NAME=x\n# CLAUDE_DEVCONTAINER_DIND=1\n'
                                                      'CLAUDE_DEVCONTAINER_DIND_EXTRA=1\n')
        # Compose sets these in the containers; they are not settings.
        self.generate({'CLAUDE_DEVCONTAINER_PROXY_ADDRESS': '10.0.0.10', 'CLAUDE_DEVCONTAINER_DIND_ADDRESS': 'x'})

    def test_command_line(self):
        self.write('claude.toml', '[docker]\nenabled = "yes"\n')
        failed = subprocess.run([sys.executable, str(SCRIPT), 'proxy-json', str(self.root / '.devcontainer/claude.toml')],
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stderr, 'error: claude.toml: docker.enabled: expected true or false\n')
        self.write('claude.toml', GITHUB)
        printed = subprocess.run([sys.executable, str(SCRIPT), 'proxy-json', str(self.root / '.devcontainer/claude.toml')],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(list(json.loads(printed.stdout)['hosts']), ['api.github.com'])

    def test_old_python_is_refused(self):
        old = subprocess.run([sys.executable, '-c', 'import sys; sys.version_info = (3, 10); '
                              'sys.argv = ["settings.py", "generate"]; exec(open(sys.argv_path).read())'
                              .replace('sys.argv_path', repr(str(SCRIPT)))], capture_output=True, text=True)
        self.assertEqual(old.returncode, 1)
        self.assertIn('needs Python 3.11 or later', old.stderr)

    def test_template_is_valid(self):
        data = settings.read(ROOT / '.devcontainer/claude/template/claude.toml')
        settings.check(data, 'claude.toml')
        self.assertEqual(set(data['proxy']['hosts']), {'api.github.com', 'github.com'})
        self.assertEqual(data['proxy']['hosts']['api.github.com']['env'], ['GH_TOKEN'])

    def test_node_paths_are_repo_keyed_and_do_not_clear_state(self):
        with tempfile.TemporaryDirectory() as state:
            env = {'XDG_STATE_HOME': state}
            paths = settings.node_modules_paths(self.root, env)
            other = settings.node_modules_paths(self.root / 'other', env)
            self.assertEqual(paths['CLAUDE_DEVCONTAINER_NODE_LOWER'], str(self.root / 'node_modules'))
            self.assertNotEqual(paths['CLAUDE_DEVCONTAINER_NODE_UPPER'], other['CLAUDE_DEVCONTAINER_NODE_UPPER'])
            settings.prepare_node_modules(self.root, env)
            upper = Path(paths['CLAUDE_DEVCONTAINER_NODE_UPPER'])
            (upper / 'keep').write_text('keep')
            settings.prepare_node_modules(self.root, env)
            self.assertEqual((upper / 'keep').read_text(), 'keep')
            self.assertTrue((self.root / 'node_modules').is_dir())
            settings.generate(self.root, env)
            for name, value in paths.items():
                self.assertEqual(self.env()[name], "'" + value + "'")

    def test_overlay_option_delimiters_and_state_in_repo_are_refused(self):
        for state in ('/tmp/a,b', '/tmp/a:b', str(self.root / 'state')):
            with self.subTest(state=state), self.assertRaises(ValueError):
                settings.node_modules_paths(self.root, {'XDG_STATE_HOME': state})


if __name__ == '__main__':
    unittest.main()
