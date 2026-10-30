"""How settings.py reads claude.toml and claude.local.toml and what it writes; no Docker needed."""
import copy
import importlib.util
import json
import os
import re
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
        self.assertEqual(loaded['git'], {'isolation': 'clone', 'shared_hidden_paths': [], 'inherit_remotes': True})
        self.assertEqual(loaded['agent'], {'uid': 1000, 'gid': 1000, 'extra_path': [], 'env': {}, 'share_projects': False, 'mounts': []})
        self.assertEqual(loaded['acp'], {'enabled': False, 'version': '0.90.0'})
        self.assertEqual(loaded['docker'], {'enabled': False, 'images': [], 'shared_base': ''})
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
        self.assertEqual(loaded['agent'], {'uid': 1234, 'gid': 2345, 'extra_path': ['/global/bin'], 'env': {}, 'share_projects': False, 'mounts': []})
        self.assertEqual(loaded['proxy']['ca'], settings.CONFIG / 'cert.pem')
        self.write('claude.local.toml', '[agent]\ngid = 3456\nextra_path = []\n[proxy]\nca = "local.pem"\n')
        loaded = self.load()
        self.assertEqual(loaded['agent'], {'uid': 1234, 'gid': 3456, 'extra_path': [], 'env': {}, 'share_projects': False, 'mounts': []})
        self.assertEqual(loaded['proxy']['ca'], self.root / '.devcontainer/local.pem')

    def test_local_merges_tables_and_replaces_values(self):
        self.write('claude.toml', '[docker]\nenabled = true\nimages = ["a:1", "b:1"]\n[agent]\nuid = 1001\n')
        self.write('claude.local.toml', '[docker]\nimages = ["c:1"]\n[agent]\ngid = 1002\n')
        loaded = self.load()
        self.assertEqual(loaded['docker'], {'enabled': True, 'images': ['c:1'], 'shared_base': ''})
        self.assertEqual(loaded['agent'], {'uid': 1001, 'gid': 1002, 'extra_path': [], 'env': {}, 'share_projects': False, 'mounts': []})

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
            ('[docker]\nenabled = true\nshared_base = "a/b"\n', r'docker.shared_base: .a/b. is not a name'),
            ('[docker]\nenabled = true\nshared_base = 1\n', r'docker.shared_base: expected a string'),
            ('[docker]\nenabled = true\nvolume = "a"\n', r'docker.volume: unknown setting'),
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

    def state(self):
        """A state directory outside the repository, as settings.py requires."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def test_dind_runs_over_the_shared_base(self):
        state = self.state()
        environ = {'XDG_STATE_HOME': str(state)}
        # Ignored without DinD, so that the global claude.toml can name it.
        self.write('claude.toml', '[docker]\nshared_base = "common"\n')
        out = self.generate(environ)
        self.assertNotIn('volumes', json.loads((out / 'workspace.compose.yaml').read_text()))
        self.assertEqual(self.env()['CLAUDE_DEVCONTAINER_DIND_SHARED'], "''")
        self.write('claude.toml', '[docker]\nenabled = true\nshared_base = "common"\n')
        self.generate(environ)
        self.assertEqual(self.env()['CLAUDE_DEVCONTAINER_DIND_SHARED'], "'common'")
        compose = json.loads((out / 'workspace.compose.yaml').read_text())
        self.assertEqual(compose['services']['dind'], {'volumes': [
            {'type': 'volume', 'source': 'dind-shared-common', 'target': '/home/rootless/.local/share/docker'}]})
        paths = settings.dind_paths('common', self.root, environ)
        self.assertEqual(paths['data'], state.resolve() / 'claude-devcontainer/dind-base/common/data')
        self.assertTrue(paths['upper'].is_relative_to(state.resolve() / 'claude-devcontainer/dind'))
        self.assertEqual(compose['volumes']['dind-shared-common']['driver_opts']['o'],
                         f'lowerdir={paths["data"]},upperdir={paths["upper"]},workdir={paths["work"]}')

    def test_share_projects_mounts_the_host_history_of_this_repository(self):
        config = self.state()
        environ = {'CLAUDE_CONFIG_DIR': str(config)}
        out = self.generate(environ)
        targets = [m['target'] for m in json.loads((out / 'workspace.compose.yaml').read_text())['services']['agent']['volumes']]
        self.assertNotIn(settings.AGENT_PROJECT, targets)
        self.write('claude.toml', '[agent]\nshare_projects = true\n')
        self.generate(environ)
        mounts = json.loads((out / 'workspace.compose.yaml').read_text())['services']['agent']['volumes']
        project = config / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(self.root.resolve()))
        self.assertIn({'type': 'bind', 'source': str(project), 'target': '/home/node/.claude/projects/-workspace',
                       'bind': {'create_host_path': False}}, mounts)
        self.assertTrue(project.is_dir())
        self.assertEqual(settings.host_project('/home/u/my repo.git', environ), config / 'projects/-home-u-my-repo-git')
        with self.assertRaisesRegex(ValueError, 'too long'):
            settings.host_project('/' + 'a' * 200, environ)

    def test_agent_mounts(self):
        (self.root / 'data').mkdir()
        (self.root / '.devcontainer/cache').mkdir()
        self.write('claude.toml', '[agent]\nmounts = ["../data:/mnt/data", "cache:/home/node/.cache/tool:rw", '
                                  '"~:/mnt/home:ro"]\n')
        out = self.generate()
        mounts = json.loads((out / 'workspace.compose.yaml').read_text())['services']['agent']['volumes']
        bind = {'create_host_path': False}
        for expected in ({'type': 'bind', 'source': str(self.root / 'data'), 'target': '/mnt/data', 'read_only': True, 'bind': bind},
                         {'type': 'bind', 'source': str(self.root / '.devcontainer/cache'),
                          'target': '/home/node/.cache/tool', 'read_only': False, 'bind': bind},
                         {'type': 'bind', 'source': str(Path.home()), 'target': '/mnt/home', 'read_only': True, 'bind': bind}):
            self.assertIn(expected, mounts)
        self.write('claude.toml', '[agent]\nmounts = ["../absent:/mnt/absent"]\n')
        with self.assertRaisesRegex(ValueError, 'absent does not exist on the host'):
            self.generate()

    def test_agent_cow_mounts(self):
        state = self.state()
        environ = {'XDG_STATE_HOME': str(state)}
        source = self.root / 'maven'
        source.mkdir()
        (self.root / 'file').write_text('')
        self.write('claude.toml', '[agent]\nmounts = ["../maven:/home/node/.m2/repository:cow"]\n')
        settings.prepare_cow_mounts(self.root, environ)
        out = self.generate(environ)
        compose = json.loads((out / 'workspace.compose.yaml').read_text())
        paths = settings.cow_paths(str(source), '/home/node/.m2/repository', self.root, environ)
        self.assertTrue(paths['upper'].is_dir() and paths['work'].is_dir())
        self.assertTrue(paths['upper'].is_relative_to(state.resolve() / 'claude-devcontainer/cow'))
        self.assertIn({'type': 'volume', 'source': paths['volume'], 'target': '/home/node/.m2/repository',
                       'volume': {'nocopy': True}}, compose['services']['agent']['volumes'])
        self.assertEqual(compose['volumes'], {paths['volume']: {'driver_opts': {
            'type': 'overlay', 'device': 'overlay',
            'o': f'lowerdir={source},upperdir={paths["upper"]},workdir={paths["work"]}'}}})
        # Another source or target is another volume, as Docker keeps a volume's options.
        self.assertNotEqual(settings.cow_paths(str(source), '/mnt/other', self.root, environ)['volume'], paths['volume'])
        self.write('claude.toml', '[agent]\nmounts = ["../file:/mnt/file:cow"]\n')
        with self.assertRaisesRegex(ValueError, 'must be a directory'):
            self.generate(environ)
        (self.root / 'with space').mkdir()
        self.write('claude.toml', '[agent]\nmounts = ["../with space:/mnt/x:cow"]\n')
        with self.assertRaisesRegex(ValueError, 'cannot contain whitespace'):
            self.generate(environ)

    def test_agent_mounts_errors(self):
        for value, message in [('/a', 'expected SOURCE:TARGET'), ('/a:/b:x', 'expected SOURCE:TARGET'),
                               ('/a:/b:ro:x', 'expected SOURCE:TARGET'), (':/b', 'source path'),
                               ('/a,b:/b', 'source path'), ('/a:b', 'absolute, normalized'),
                               ('/a:/b/../c', 'absolute, normalized'), ('/a:/', 'absolute, normalized'),
                               ('/a:/workspace', 'only :cow'), ('/a:/workspace/x', 'only :cow'),
                               ('/a:/workspace/x:rw', 'only :cow'), ('/workspace/.git/x:cow', 'only :cow'),
                               ('/workspace/.devcontainer:cow', 'only :cow'), ('/workspace/x', 'expected SOURCE'),
                               ('/workspace/x:rw', 'absolute, normalized'),
                               ('/a:/run/host/git', 'outside'), ('/a:/home/node/.claude/projects', 'outside'),
                               ('/a:/usr/local/bin/entrypoint.sh', 'outside'), ('/a:/nix/store', 'outside')]:
            with self.subTest(value=value):
                self.write('claude.toml', f'[agent]\nmounts = ["{value}"]\n')
                with self.assertRaisesRegex(ValueError, 'agent.mounts: .*' + message):
                    self.load()
        self.write('claude.toml', '[agent]\nmounts = "/a:/b"\n')
        with self.assertRaisesRegex(ValueError, 'agent.mounts: expected a list'):
            self.load()

    def test_prepare_dind_checks_the_base_uid(self):
        state = self.state()
        environ = {'XDG_STATE_HOME': str(state)}
        settings.prepare_dind(self.root, environ)
        self.assertEqual(list(state.iterdir()), [])
        self.write('claude.toml', '[docker]\nenabled = true\nshared_base = "common"\n[agent]\nuid = 1001\n')
        settings.prepare_dind(self.root, environ)
        paths = settings.dind_paths('common', self.root, environ)
        self.assertTrue(all(paths[key].is_dir() for key in ('data', 'upper', 'work')))
        self.assertEqual(paths['uid'].read_text(), '1001\n')
        settings.prepare_dind(self.root, environ)
        self.write('claude.toml', '[docker]\nenabled = true\nshared_base = "common"\n')
        with self.assertRaisesRegex(ValueError, 'belongs to agent.uid = 1001, not 1000'):
            settings.prepare_dind(self.root, environ)

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

    def test_host_mode_copies_the_global_agent_setup_to_its_state(self):
        # The host command's build context is the state directory
        # (host/compose.yaml); the repository's own agent-setup.sh stays out.
        state = self.root / 'state'
        (self.root / '.devcontainer/agent-setup.sh').write_text('echo repository\n')
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'agent-setup.sh').write_text('echo global\n')
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_CONFIG_DIR': str(state)}):
            settings.generate(self.root, {})
        self.assertEqual((state / '.generated/global-agent-setup.sh').read_text(), 'echo global\n')
        self.assertFalse((state / 'agent-setup.sh').exists())

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
            'CLAUDE_DEVCONTAINER_DIND': "'1'", 'CLAUDE_DEVCONTAINER_DIND_SHARED': "''", 'CLAUDE_DEVCONTAINER_NIX': "'0'",
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

    def test_no_dependency_overlay_is_built_in(self):
        state = self.state()
        out = self.generate({'XDG_STATE_HOME': str(state)})
        settings.prepare_cow_mounts(self.root, {'XDG_STATE_HOME': str(state)})
        self.assertFalse((self.root / 'node_modules').exists())
        self.assertNotIn('volumes', json.loads((out / 'workspace.compose.yaml').read_text()))
        self.assertFalse(any('NODE' in name for name in self.env()))

    def test_workspace_cow_mounts_take_the_repository_path(self):
        state = self.state()
        environ = {'XDG_STATE_HOME': str(state)}
        # Global, so that it applies to every repository that has the directory.
        settings.CONFIG.mkdir()
        (settings.CONFIG / 'claude.toml').write_text('[agent]\nmounts = ["/workspace/node_modules:cow"]\n')
        out = self.generate(environ)
        self.assertNotIn('volumes', json.loads((out / 'workspace.compose.yaml').read_text()), 'none without it')
        (self.root / 'node_modules').mkdir()
        settings.prepare_cow_mounts(self.root, environ)
        self.generate(environ)
        compose = json.loads((out / 'workspace.compose.yaml').read_text())
        paths = settings.cow_paths(str(self.root.resolve() / 'node_modules'), '/workspace/node_modules', self.root, environ)
        self.assertTrue(paths['upper'].is_dir())
        self.assertIn({'type': 'volume', 'source': paths['volume'], 'target': '/workspace/node_modules',
                       'volume': {'nocopy': True}}, compose['services']['agent']['volumes'])
        self.assertIn(f'lowerdir={self.root.resolve() / "node_modules"},', compose['volumes'][paths['volume']]['driver_opts']['o'])

    def test_cow_state_with_option_delimiters_or_in_the_repository_is_refused(self):
        for state in ('/tmp/a,b', '/tmp/a:b', str(self.root / 'state')):
            with self.subTest(state=state), self.assertRaises(ValueError):
                settings.cow_paths('/src', '/workspace/node_modules', self.root, {'XDG_STATE_HOME': state})

if __name__ == '__main__':
    unittest.main()
