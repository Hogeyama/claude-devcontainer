"""Settings agree between real Compose config and host initialization; no daemon needed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / '.devcontainer/claude'
FLAG = 'CLAUDE_DEVCONTAINER_DIND'
# The real pick-networks.py asks Docker (pick_networks_test.py covers it); this
# writes what it would.
PICK_NETWORKS = '''from pathlib import Path
out = Path('.devcontainer/.generated/networks.env')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text('CLAUDE_DEVCONTAINER_PROXY_NET=10.231.90\\nCLAUDE_DEVCONTAINER_DIND_NET=10.231.91\\n')
'''
# compose.sh finds the environment with `docker ps`; this answers it, and runs
# `down` as `config`, which needs no daemon, to show what compose would use.
DOCKER = '''#!/usr/bin/env python3
import os, sys
if sys.argv[1] == 'ps':
    print('cdc-settings')
    sys.exit()
args = [word for arg in sys.argv[1:] for word in (['config', '--format', 'json'] if arg == 'down' else [arg])]
os.execv(os.environ['REAL_DOCKER'], ['docker', *args])
'''


def environment():
    return {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_DEVCONTAINER_', 'COMPOSE_'))}


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI required')
class SettingsIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cdc-settings-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'remote', 'add', 'origin', 'https://github.com/o/r.git'], check=True)
        self.config = self.root / '.devcontainer'
        scripts = self.config / 'claude/scripts'
        scripts.mkdir(parents=True)
        (self.config / 'claude/proxy').mkdir()
        (self.config / 'claude/agent').mkdir()
        shutil.copy(CORE / 'agent/workspace_mounts.py', self.config / 'claude/agent/workspace_mounts.py')
        shutil.copy(CORE / 'compose.yaml', self.config / 'claude/compose.yaml')
        shutil.copy(CORE / 'template/compose.yaml', self.config / 'compose.yaml')
        shutil.copy(CORE / 'proxy/policy.py', self.config / 'claude/proxy/policy.py')
        for script in ('initialize.sh', 'settings.py', 'compose.sh', 'workspace-source.py', 'git-agent.py'):
            shutil.copy(CORE / 'scripts' / script, scripts / script)
        # Replace background side effects, keeping the real initialization
        # script and its decision to load images.
        for helper in ('copy-host-config.py', 'inject-credentials.py', 'load-images.py'):
            path = scripts / helper
            path.write_text('#!/bin/sh\n' + ('echo load-images\n' if helper == 'load-images.py' else 'exit 0\n'))
            path.chmod(0o755)
        (scripts / 'pick-networks.py').write_text(PICK_NETWORKS)
        self.env = environment()
        state = tempfile.TemporaryDirectory(prefix='cdc-settings-state-')
        self.addCleanup(state.cleanup)
        self.env['XDG_STATE_HOME'] = state.name

    def write(self, name, text):
        (self.config / name).write_text(text)

    def initialize(self, check=True):
        return subprocess.run(['sh', str(self.config / 'claude/scripts/initialize.sh')], cwd=self.root, env=self.env,
                              capture_output=True, text=True, check=check)

    def compose_config(self, *options):
        compose = subprocess.run(['docker', 'compose', *options, '-f', str(self.config / 'compose.yaml'),
                                  'config', '--format', 'json'], cwd=self.root, env=self.env,
                                 capture_output=True, text=True, check=True)
        return json.loads(compose.stdout)

    def test_sumi_relative_path_matches_compose(self):
        secrets = self.root / '.secrets/sumi'
        secrets.mkdir(parents=True)
        (secrets / 'secrets.txt').write_text('test-only-secret\n')
        self.write('claude.toml', '[sumi]\nenabled = true\nsecrets = "../.secrets/sumi/secrets.txt"\n')
        self.initialize()
        sumi = self.compose_config()['services']['sumi']
        self.assertEqual(next(m['source'] for m in sumi['volumes'] if m['target'] == '/secrets'), str(secrets))
        self.assertIn('/secrets/secrets.txt', sumi['command'])

    def test_compose_and_initialize_use_the_same_settings(self):
        cases = [('defaults', None, None, '0'), ('workspace', 'true', None, '1'), ('host', 'true', 'false', '0'),
                 ('host only', None, 'true', '1')]
        for name, workspace, host, expected in cases:
            with self.subTest(name=name):
                for file, value in (('claude.toml', workspace), ('claude.local.toml', host)):
                    self.write(file, '' if value is None else f'[docker]\nenabled = {value}\n')
                self.assertEqual(self.initialize().stdout.strip(), 'load-images' if expected == '1' else '')
                services = self.compose_config()['services']
                self.assertEqual(services['agent']['environment'][FLAG], expected)
                self.assertEqual(services['dind']['scale'], int(expected))

    def test_code_server_opt_in(self):
        for value, expected in [('false', '0'), ('true', '1')]:
            with self.subTest(value=value):
                self.write('claude.toml', f'[codeServer]\nenabled = {value}\n')
                self.initialize()
                services = self.compose_config()['services']
                agent = services['agent']
                self.assertEqual(agent['environment']['CLAUDE_DEVCONTAINER_CODE_SERVER'], expected)
                self.assertEqual(agent['build']['args']['INSTALL_CODE_SERVER'], expected)
                self.assertEqual(agent['build']['args']['INSTALL_TODE'], '0')
                self.assertEqual(agent['sysctls']['net.ipv4.conf.all.route_localnet'], expected)
                self.assertNotIn('ports', agent)
                proxy = services['code-server-proxy']
                self.assertEqual(proxy['scale'], int(expected))
                self.assertEqual(proxy['ports'][0]['host_ip'], '127.0.0.1')
                self.assertNotIn('volumes', proxy)

    def test_code_server_extensions_reach_build_only(self):
        self.write('claude.toml', '[codeServer]\nenabled = true\nextensions = ["ms-python.python"]\n'
                   '[tode]\nenabled = true\nextensions = ["golang.go"]\n')
        self.write('claude.local.toml', '[codeServer]\nextensions = ["vscodevim.vim@1.27.3"]\n')
        self.initialize()
        agent = self.compose_config()['services']['agent']
        self.assertEqual(agent['build']['args']['CODE_SERVER_EXTENSIONS'], 'vscodevim.vim@1.27.3')
        self.assertEqual(agent['build']['args']['TODE_EXTENSIONS'], 'golang.go')
        self.assertNotIn('CLAUDE_DEVCONTAINER_CODE_SERVER_EXTENSIONS', agent['environment'])

    def test_web_search_opt_in_reaches_only_the_proxy(self):
        for value, expected in [(None, '0'), ('true', '1'), ('false', '0')]:
            with self.subTest(value=value):
                self.write('claude.toml', '' if value is None else f'[proxy]\nweb_search = {value}\n')
                self.initialize()
                services = self.compose_config()['services']
                name = 'CLAUDE_DEVCONTAINER_WEB_SEARCH'
                self.assertEqual(services['proxy']['environment'][name], expected)
                self.assertNotIn(name, services['agent']['environment'])

    def test_settings_outside_the_files_stop_initialization(self):
        self.env[FLAG] = '1'
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(f'{FLAG} in the environment', failed.stderr)
        del self.env[FLAG]
        self.write('.env', f'{FLAG}=1\n')
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(f'{FLAG} in .devcontainer/.env', failed.stderr)
        # compose reads this form as well.
        self.write('.env', f'{FLAG}: 1\n')
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(f'{FLAG} in .devcontainer/.env', failed.stderr)
        # compose sets this one in the agent; it is not a setting.
        self.write('.env', '')
        self.env['CLAUDE_DEVCONTAINER_PROXY_ADDRESS'] = '10.231.90.10'
        self.initialize()

    def test_chosen_subnets_are_the_lowest_setting(self):
        cases = [('chosen', None, None, '10.231.90'), ('workspace', '10.231.80', None, '10.231.80'),
                 ('host', '10.231.80', '10.231.81', '10.231.81')]
        for name, workspace, host, expected in cases:
            with self.subTest(name=name):
                for file, value in (('claude.toml', workspace), ('claude.local.toml', host)):
                    self.write(file, '' if value is None else f'[subnets]\nproxy = "{value}"\n')
                self.initialize()
                services = self.compose_config()['services']
                self.assertEqual(services['proxy']['networks']['proxy-internal']['ipv4_address'], expected + '.10')
                self.assertEqual(services['dind']['networks']['dind-internal']['ipv4_address'], '10.231.91.10')

    def test_dummy_tokens_reach_the_agent(self):
        self.write('claude.toml', '[proxy.hosts."api.github.com"]\nroutes = ["GET /user"]\nenv = ["GH_TOKEN"]\n')
        self.initialize()
        environment = self.compose_config()['services']['agent']['environment']
        self.assertEqual(environment['GH_TOKEN'], 'managed-by-proxy')
        self.assertEqual(environment['CLAUDE_CODE_OAUTH_TOKEN'], 'managed-by-proxy')

    def test_compose_sh(self):
        binary = self.root / 'bin'
        binary.mkdir()
        (binary / 'docker').write_text(DOCKER)
        (binary / 'docker').chmod(0o755)
        self.env.update(PATH=f'{binary}:{self.env["PATH"]}', REAL_DOCKER=shutil.which('docker'))
        script = str(self.config / 'claude/scripts/compose.sh')

        def run(*args):
            return subprocess.run([script, *args], cwd=self.root, env=self.env, capture_output=True, text=True)
        # As after a first `devcontainer up`.
        subprocess.run([sys.executable, '-c', PICK_NETWORKS], cwd=self.root, check=True)
        # It writes .generated/ from claude.toml as initializeCommand would.
        self.write('claude.toml', '[docker]\nenabled = true\n')
        shown = run('down')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)['services']['dind']['scale'], 1)
        # With invalid settings, the previous ones; never the environment's or
        # .env's, which the settings refuse.
        self.write('claude.toml', '[docker]\nenabled = false\n')
        run('down')
        self.env[FLAG] = '1'
        self.write('.env', f'{FLAG}=1\n')
        shown = run('down')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn('warning: the settings are invalid', shown.stderr)
        self.assertEqual(json.loads(shown.stdout)['services']['dind']['scale'], 0)
        refused = run('up', '-d')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('fix the settings first', refused.stderr)

    def test_workspace_mounts_are_isolated(self):
        self.initialize()
        config = self.compose_config()
        mounts = {m['target']: m for m in config['services']['agent']['volumes']}
        self.assertEqual(mounts['/workspace']['type'], 'volume')
        self.assertEqual(mounts['/workspace/node_modules']['type'], 'volume')
        self.assertFalse(any(p.startswith('/workspace/.git') for p in mounts))
        self.assertNotIn('/workspace/.devcontainer', mounts)
        for path in ('/run/host/generated', '/run/host/nix-env'):
            self.assertTrue(mounts[path]['read_only'])
        options = config['volumes']['node-modules']['driver_opts']
        self.assertEqual(options['type'], 'overlay')
        self.assertIn('lowerdir=' + str(self.root / 'node_modules'), options['o'])

    def test_shared_initialization_and_switch_preserve_clone_and_git_state(self):
        self.initialize()
        before = (self.root / '.git/config').read_bytes()
        generated = self.config / '.generated'
        metadata = (generated / 'workspace.json').read_bytes()
        clone = self.compose_config()
        self.write('claude.local.toml', '[git]\nisolation = "shared"\n')
        self.initialize()
        shared = self.compose_config()
        agent = shared['services']['agent']
        mounts = [m for m in agent['volumes'] if m['target'] == '/workspace']
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]['type'], 'bind')
        self.assertEqual(mounts[0]['source'], str(self.root))
        self.assertFalse(mounts[0].get('bind', {}).get('create_host_path', False))
        self.assertEqual(agent['environment']['CLAUDE_DEVCONTAINER_GIT_ISOLATION'], 'shared')
        self.assertEqual(agent['labels']['dev.claude-devcontainer.git-isolation'], 'shared')
        self.assertEqual((generated / 'workspace.json').read_bytes(), metadata)
        self.assertEqual((self.root / '.git/config').read_bytes(), before)
        # Compose omits unused volume declarations; Docker keeps the detached
        # volume. Returning to clone below must resolve the original name.
        self.assertEqual(shared['volumes']['node-modules'], clone['volumes']['node-modules'])
        self.write('claude.local.toml', '[git]\nisolation = "clone"\n')
        self.initialize()
        self.assertEqual(self.compose_config(), clone)

    def test_shared_mounts_resolve_through_compose(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        self.initialize()
        config = self.compose_config()
        agent = config['services']['agent']
        self.assertEqual(agent['environment']['CLAUDE_DEVCONTAINER_GIT_ISOLATION'], 'shared')
        self.assertNotIn('CLAUDE_DEVCONTAINER_GIT_SHARED_WRITABLE', agent['environment'])
        self.assertEqual(agent['labels']['dev.claude-devcontainer.git-isolation'], 'shared')
        mounts = {m['target']: m for m in agent['volumes']}
        self.assertEqual((mounts['/workspace']['type'], mounts['/workspace']['source']), ('bind', str(self.root)))
        git = mounts['/workspace/.git']
        self.assertEqual((git['type'], git['source'], git.get('read_only', False)), ('volume', 'agent-git', False))
        self.assertTrue(git['volume']['nocopy'])
        self.assertEqual(config['volumes']['agent-git']['name'], config['name'] + '_agent-git')
        for target in ('/run/host/git', '/workspace/.devcontainer'):
            self.assertTrue(mounts[target]['read_only'])
        self.assertEqual(mounts['/run/host/git']['source'], str(self.root / '.git'))
        # Seeded and registered as clone is.
        self.assertTrue((self.config / '.generated/workspace.json').exists())
        self.assertIn('remote.agent.url', subprocess.run(['git', '-C', str(self.root), 'config', '--list'],
                                                        capture_output=True, text=True).stdout)

    def test_shared_rejects_overridden_git_protections(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        compose = self.config / 'compose.yaml'
        original = compose.read_text()
        for target in ('/workspace/.git', '/run/host/git', '/run/host/git/hooks'):
            with self.subTest(target=target):
                compose.write_text(original + '\nservices:\n  agent:\n    volumes:\n'
                                   f'      - extra:{target}\nvolumes:\n  extra:\n')
                failed = self.initialize(check=False)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn('shared protection', failed.stderr)

    def test_shared_checks_extra_mount_parents_and_keeps_consumer_mounts(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        compose = self.config / 'compose.yaml'
        compose.write_text(compose.read_text() + '\nservices:\n  agent:\n    volumes:\n'
                           '      - extra:/workspace/packages/app/node_modules\nvolumes:\n  extra:\n')
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('existing, non-symlink parent', failed.stderr)
        parent = self.root / 'packages/app'
        parent.mkdir(parents=True)
        before = parent.stat()
        self.initialize()
        self.assertEqual((parent.stat().st_uid, parent.stat().st_mode), (before.st_uid, before.st_mode))
        mounts = self.compose_config()['services']['agent']['volumes']
        self.assertTrue(any(m['target'] == '/workspace/packages/app/node_modules' for m in mounts))
        self.assertEqual(sum(m['target'] == '/workspace' for m in mounts), 1)

    def test_shared_requires_updated_compose_include(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        compose = self.config / 'compose.yaml'
        compose.write_text(compose.read_text().replace('[claude/compose.yaml, .generated/workspace.compose.yaml]',
                                                       'claude/compose.yaml'))
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('update it from claude/template/compose.yaml', failed.stderr)

    def test_shared_secret_masks_survive_compose_merge(self):
        (self.root / '.secrets').mkdir()
        (self.root / '.env').write_text('SECRET=fixture\n')
        self.write('claude.local.toml', '[git]\nisolation = "shared"\nshared_hidden_paths = [".secrets", ".env"]\n')
        self.initialize()
        mounts = {m['target']: m for m in self.compose_config()['services']['agent']['volumes']}
        self.assertTrue(mounts['/run/host/git']['read_only'])
        for name in ('.devcontainer', '.secrets', '.env'):
            self.assertTrue(mounts['/workspace/' + name]['read_only'])
        self.assertEqual(mounts['/workspace/.secrets']['type'], 'tmpfs')
        self.assertEqual(Path(mounts['/workspace/.env']['source']).read_bytes(), b'')

    def test_shared_rejects_symlink_mask_replacement_before_startup(self):
        (self.root / '.secrets').mkdir()
        self.write('claude.toml', '[git]\nisolation = "shared"\nshared_hidden_paths = [".secrets"]\n')
        self.initialize()
        (self.root / '.secrets').rmdir()
        (self.root / '.secrets').symlink_to(self.root / '.git')
        checked = subprocess.run(['python3', str(self.config / 'claude/scripts/settings.py'), 'check-shared-mounts'],
                                 env=self.env, capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn('must not follow a symlink', checked.stderr)

    def test_shared_rejects_mount_beneath_protection(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        compose = self.config / 'compose.yaml'
        compose.write_text(compose.read_text() + '\nservices:\n  agent:\n    volumes:\n'
                           '      - extra:/run/host/git/hooks\nvolumes:\n  extra:\n')
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Mount overrides shared protection', failed.stderr)

    def test_shared_rejects_short_tmpfs_beneath_protection(self):
        self.write('claude.toml', '[git]\nisolation = "shared"\n')
        compose = self.config / 'compose.yaml'
        compose.write_text(compose.read_text() + '\nservices:\n  agent:\n    tmpfs:\n'
                           '      - /run/host/git/hooks\n')
        failed = self.initialize(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Mount overrides shared protection', failed.stderr)


if __name__ == '__main__':
    unittest.main()
