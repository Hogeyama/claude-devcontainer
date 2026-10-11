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
            (root / 'env.rc').write_text('export PATH="/workspace/.devcontainer/.nix-env/bin:$PATH"\n')
            for enabled in ('0', '1'):
                for args in (['-c'], ['-lc'], ['-ic'], ['-lic'], ['--norc', '-ic']):
                    with self.subTest(nix=enabled, shell=args):
                        # Nix's Bash has no Debian system bashrc. VS Code's init
                        # file sources ~/.bashrc itself; exercise that path too.
                        script = '. ~/.bashrc; ' if '--norc' in args else ''
                        script += 'if command -v dev-shell-tool >/dev/null; then dev-shell-tool; else echo no-dev-shell; fi'
                        result = docker('run', '--rm', '--network', 'none', '--user', 'node',
                                        '-e', 'CLAUDE_DEVCONTAINER_NIX=' + enabled,
                                        '-v', directory + ':/workspace/.devcontainer/.nix-env:ro',
                                        '--entrypoint', '/bin/bash', AGENT_IMAGE, *args, script)
                        self.assertEqual(result.strip(), 'dev-shell-ok' if enabled == '1' else 'no-dev-shell')

    def test_nested_bash_keeps_tmpdir(self):
        # nix print-dev-env ends with these lines; every Bash reads env.rc.
        with tempfile.TemporaryDirectory(prefix='cdc-shell-') as directory:
            root = Path(directory)
            root.chmod(0o755)
            (root / 'env.rc').write_text(
                'export NIX_BUILD_TOP="$(mktemp -d -t nix-shell.XXXXXX)"\n'
                'export TMP="$NIX_BUILD_TOP"\n'
                'export TMPDIR="$NIX_BUILD_TOP"\n'
                'export TEMP="$NIX_BUILD_TOP"\n'
                'export TEMPDIR="$NIX_BUILD_TOP"\n')
            for tmpdir in ([], ['-e', 'TMPDIR=/var/tmp']):
                with self.subTest(tmpdir=tmpdir):
                    result = docker('run', '--rm', '--network', 'none', '--user', 'node',
                                    '-e', 'CLAUDE_DEVCONTAINER_NIX=1', *tmpdir,
                                    '-v', directory + ':/workspace/.devcontainer/.nix-env:ro',
                                    '--entrypoint', '/bin/bash', AGENT_IMAGE, '-c',
                                    'bash -c \'bash -c "echo ${TMPDIR-unset} ${TMP-unset} ${NIX_BUILD_TOP-unset}"\';'
                                    ' ls -d /tmp/nix-shell.* /var/tmp/nix-shell.* 2>/dev/null | wc -l')
                    expected = '/var/tmp' if tmpdir else 'unset'
                    self.assertEqual(result.split(), [expected, 'unset', 'unset', '0'])


if __name__ == '__main__':
    unittest.main()
