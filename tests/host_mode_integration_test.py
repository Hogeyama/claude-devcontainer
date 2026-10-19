"""Compose reads the host mode's state directory; no daemon needed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / '.devcontainer/claude'


def environment():
    return {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_DEVCONTAINER_', 'COMPOSE_'))}


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI required')
class HostModeComposeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cdc-host-')
        self.addCleanup(directory.cleanup)
        self.top = Path(directory.name)
        self.repo = self.top / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        # A repository's own settings, which host mode must not read.
        (self.repo / '.devcontainer').mkdir()
        (self.repo / '.devcontainer/claude.toml').write_text('[docker]\nenabled = true\n')
        self.state = self.top / 'state'
        self.env = dict(environment(), XDG_CONFIG_HOME=str(self.top / 'config'),
                        XDG_STATE_HOME=str(self.top / 'xdg-state'),
                        CLAUDE_DEVCONTAINER_WORKSPACE=str(self.repo),
                        CLAUDE_DEVCONTAINER_CONFIG_DIR=str(self.state),
                        CLAUDE_DEVCONTAINER_WORKSPACE_ID='0123456789abcdef')
        subprocess.run([sys.executable, str(CORE / 'scripts/settings.py'), 'generate'],
                       env=self.env, check=True, cwd=self.repo)
        (self.state / '.generated/networks.env').write_text(
            'CLAUDE_DEVCONTAINER_PROXY_NET=10.231.90\nCLAUDE_DEVCONTAINER_DIND_NET=10.231.91\n')

    def config(self):
        result = subprocess.run(['docker', 'compose', '-f', str(CORE / 'host/compose.yaml'),
                                 'config', '--format', 'json'], env=self.env, check=True,
                                capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_project_is_named_after_the_workspace(self):
        self.assertEqual(self.config()['name'], 'cdc-0123456789abcdef')

    def test_state_directory_stands_in_for_devcontainer(self):
        config = self.config()
        agent = config['services']['agent']
        self.assertEqual(Path(agent['build']['additional_contexts']['workspace-config']), self.state)
        self.assertEqual(Path(config['services']['proxy']['build']['additional_contexts']['workspace-config']),
                         self.state)
        sources = {mount['target']: mount.get('source') for mount in agent['volumes']}
        self.assertEqual(Path(sources['/run/host/generated']), self.state / '.generated')
        self.assertEqual(Path(sources['/run/host/nix-env']), self.state / '.nix-env')

    def test_repository_settings_are_ignored(self):
        settings_env = (self.state / '.generated/settings.env').read_text()
        self.assertIn("CLAUDE_DEVCONTAINER_DIND='0'", settings_env)
        self.assertFalse((self.repo / '.devcontainer/.generated').exists())


if __name__ == '__main__':
    unittest.main()
