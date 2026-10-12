"""Bash startup with an exported dev shell; no nix installation required."""
import os
from pathlib import Path
import tempfile
import unittest
from proxy_integration_test import docker

AGENT_IMAGE = os.environ.get('AGENT_TEST_IMAGE', 'claude-devcontainer-agent:task2')


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class AgentShellIntegrationTest(unittest.TestCase):
    def test_dev_shell_tools_are_available_in_bash_shells(self):
        with tempfile.TemporaryDirectory(prefix='cdc-shell-') as directory:
            root = Path(directory)
            root.chmod(0o755)
            (root / 'bin').mkdir()
            tool = root / 'bin/dev-shell-tool'
            tool.write_text('#!/bin/sh\necho dev-shell-ok\n')
            tool.chmod(0o755)
            (root / 'env.rc').write_text('export PATH="/run/host/nix-env/bin:$PATH"\n')
            for enabled in ('0', '1'):
                for args in (['-c'], ['-lc'], ['-ic'], ['-lic'], ['--norc', '-ic']):
                    with self.subTest(nix=enabled, shell=args):
                        # Nix's Bash has no Debian system bashrc. VS Code's init
                        # file sources ~/.bashrc itself; exercise that path too.
                        script = '. ~/.bashrc; ' if '--norc' in args else ''
                        script += 'if command -v dev-shell-tool >/dev/null; then dev-shell-tool; else echo no-dev-shell; fi'
                        result = docker('run', '--rm', '--network', 'none', '--user', 'node',
                                        '-e', 'CLAUDE_DEVCONTAINER_NIX=' + enabled,
                                        '-v', directory + ':/run/host/nix-env:ro',
                                        '--entrypoint', '/bin/bash', AGENT_IMAGE, *args, script)
                        self.assertEqual(result.strip(), 'dev-shell-ok' if enabled == '1' else 'no-dev-shell')


if __name__ == '__main__':
    unittest.main()
