"""Bash startup with the dev shell that nix-env.py loads; no nix installation required."""
import os
from pathlib import Path
import tempfile
import unittest
from proxy_integration_test import docker

AGENT_IMAGE = os.environ.get('AGENT_TEST_IMAGE', 'claude-devcontainer-agent:task2')
# The tmpfs compose.yaml gives the agent, where nix-env.py writes env.rc.
MARKERS = '/run/claude-devcontainer:uid=1000,gid=1000,mode=0700'
NIX_ENV = 'python3 /usr/local/bin/nix-env.py >/dev/null 2>&1'


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class AgentShellIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cdc-shell-')
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        root = Path(self.directory)
        root.chmod(0o755)
        (root / 'bin').mkdir()
        tool = root / 'bin/dev-shell-tool'
        tool.write_text('#!/bin/sh\necho dev-shell-ok\n')
        tool.chmod(0o755)
        # Like devshell, the dev shell sets PATH only in shellHook.
        (root / 'dev-env.sh').write_text("shellHook='export PATH=\"/run/host/nix-env/bin:$PATH\"'\n"
                                         'eval "$shellHook"\n')

    def run_agent(self, user, enabled, script, *args):
        return docker('run', '--rm', '--network', 'none', '--user', user,
                      '-e', 'CLAUDE_DEVCONTAINER_NIX=' + enabled,
                      '-v', self.directory + ':/run/host/nix-env:ro', '--tmpfs', MARKERS,
                      '--entrypoint', '/bin/sh', AGENT_IMAGE, '-c', script, 'sh', *args)

    def test_dev_shell_tools_are_available_in_bash_shells(self):
        for enabled in ('0', '1'):
            for args in (['-c'], ['-lc'], ['-ic'], ['-lic'], ['--norc', '-ic']):
                with self.subTest(nix=enabled, shell=args):
                    # Nix's Bash has no Debian system bashrc. VS Code's init
                    # file sources ~/.bashrc itself; exercise that path too.
                    script = '. ~/.bashrc; ' if '--norc' in args else ''
                    script += 'if command -v dev-shell-tool >/dev/null; then dev-shell-tool; else echo no-dev-shell; fi'
                    # wait-ready.sh runs nix-env.py once the container is up.
                    result = self.run_agent('node', enabled, NIX_ENV + ' && exec /bin/bash "$@"', *args, script)
                    self.assertEqual(result.strip(), 'dev-shell-ok' if enabled == '1' else 'no-dev-shell')

    def test_bash_run_as_root_ignores_the_dev_shell_node_wrote(self):
        result = self.run_agent('0', '1', 'setpriv --reuid=node --regid=node --init-groups sh -c "$1" && '
                                'exec /bin/bash -c "command -v dev-shell-tool || echo no-dev-shell"', NIX_ENV)
        self.assertEqual(result.strip(), 'no-dev-shell')


if __name__ == '__main__':
    unittest.main()
