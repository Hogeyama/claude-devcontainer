"""Settings agree between real Compose config and host initialization; no daemon needed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / '.devcontainer/claude'
FLAG = 'CLAUDE_DEVCONTAINER_DIND'


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI required')
class SettingsIntegrationTest(unittest.TestCase):
    def test_sumi_relative_directory_matches_compose(self):
        with tempfile.TemporaryDirectory(prefix='cdc-settings-') as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            config = root / '.devcontainer'
            scripts = config / 'claude/scripts'
            scripts.mkdir(parents=True)
            shutil.copy(CORE / 'compose.yaml', config / 'claude/compose.yaml')
            shutil.copy(CORE / 'template/compose.yaml', config / 'compose.yaml')
            shutil.copy(CORE / 'scripts/initialize.sh', scripts / 'initialize.sh')
            secrets = root / '.secrets/sumi'
            secrets.mkdir(parents=True)
            (secrets / 'secrets.txt').write_text('test-only-secret\n')
            (config / 'claude.env').write_text(
                'CLAUDE_DEVCONTAINER_SUMI=1\n'
                'CLAUDE_DEVCONTAINER_SUMI_DIR=../../.secrets/sumi\n')
            for helper in ('copy-host-config.py', 'inject-credentials.py'):
                path = scripts / helper
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('CLAUDE_DEVCONTAINER_', 'COMPOSE_'))}
            compose = subprocess.run(['docker', 'compose', '-f', str(config / 'compose.yaml'),
                                      'config', '--format', 'json'], cwd=root, env=env,
                                     capture_output=True, text=True, check=True)
            mounts = json.loads(compose.stdout)['services']['sumi']['volumes']
            self.assertEqual(next(m['source'] for m in mounts if m['target'] == '/secrets'), str(secrets))
            initialized = subprocess.run(['sh', str(scripts / 'initialize.sh')], cwd=root, env=env,
                                         capture_output=True, text=True)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

    def test_compose_and_initialize_use_the_same_precedence(self):
        cases = [
            ('defaults', None, None, None, '0'),
            ('workspace', '1', None, None, '1'),
            ('host', '1', '0', None, '0'),
            ('environment', '0', '0', '1', '1'),
            ('empty host', '1', '', None, '0'),
            ('empty environment', '1', '1', '', '0'),
        ]
        for name, workspace, host, environment, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='cdc-settings-') as directory:
                root = Path(directory)
                (root / '.git').mkdir()
                config = root / '.devcontainer'
                scripts = config / 'claude/scripts'
                scripts.mkdir(parents=True)
                shutil.copy(CORE / 'compose.yaml', config / 'claude/compose.yaml')
                shutil.copy(CORE / 'template/compose.yaml', config / 'compose.yaml')
                shutil.copy(CORE / 'scripts/initialize.sh', scripts / 'initialize.sh')
                (config / 'claude.env').write_text('' if workspace is None else f'{FLAG}={workspace}\n')
                if host is not None:
                    (config / '.env').write_text(f'{FLAG}={host}\n')
                # Replace background side effects, keeping the real initialization
                # script and its decision to load images.
                for helper in ('copy-host-config.py', 'inject-credentials.py', 'load-images.py'):
                    path = scripts / helper
                    path.write_text('#!/bin/sh\n' + ('echo load-images\n' if helper == 'load-images.py' else 'exit 0\n'))
                    path.chmod(0o755)
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith(('CLAUDE_DEVCONTAINER_', 'COMPOSE_'))}
                if environment is not None:
                    env[FLAG] = environment
                compose = subprocess.run(['docker', 'compose', '-f', str(config / 'compose.yaml'),
                                          'config', '--format', 'json'], cwd=root, env=env,
                                         capture_output=True, text=True, check=True)
                services = json.loads(compose.stdout)['services']
                self.assertEqual(services['agent']['environment'][FLAG], expected)
                self.assertEqual(services['dind']['scale'], int(expected))
                initialized = subprocess.run(['sh', str(scripts / 'initialize.sh')], cwd=root, env=env,
                                             capture_output=True, text=True, check=True)
                self.assertEqual(initialized.stdout.strip(), 'load-images' if expected == '1' else '')


if __name__ == '__main__':
    unittest.main()
