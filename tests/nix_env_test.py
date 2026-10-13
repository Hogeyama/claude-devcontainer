"""How nix-env.py turns the dev shell into the env.rc the agent's Bash reads; no nix needed."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nix_env', ROOT / '.devcontainer/claude/agent/nix-env.py')
nix_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nix_env)

BASE = {'PATH': os.environ['PATH'], 'HOME': '/home/node', 'KEPT': 'as is', 'GONE': 'here'}


def run(env_rc, before='', after=''):
    """Source env_rc in Bash between the two snippets; return the output."""
    script = before + '\n' + env_rc + after
    return subprocess.run(['bash', '--norc', '--noprofile', '-c', script],
                          stdout=subprocess.PIPE, text=True, check=True, env={'PATH': BASE['PATH']}).stdout


class DevShellTest(unittest.TestCase):
    """Sourcing the script `nix print-dev-env` prints, as `nix develop` does."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.log = self.root / 'nix-env.log'

    def dev_shell(self, script, base=BASE, timeout=10):
        path = self.root / 'dev-env.sh'
        path.write_text(script)
        return nix_env.dev_shell(path, base, self.workspace, self.log, timeout)

    def test_runs_shell_hook_in_the_workspace(self):
        # devshell sets its variables only in shellHook, and PRJ_ROOT from $PWD.
        dev_env, status = self.dev_shell("shellHook='export PRJ_ROOT=$PWD; echo welcome'\neval \"$shellHook\"\n")
        self.assertEqual((dev_env['PRJ_ROOT'], status), (str(self.workspace), 0))
        self.assertEqual(self.log.read_text(), 'welcome\n')

    def test_is_in_a_nix_shell(self):
        dev_env, _ = self.dev_shell('')
        self.assertEqual(dev_env['IN_NIX_SHELL'], 'impure')

    def test_reads_no_input(self):
        dev_env, _ = self.dev_shell('if read -r line; then export INPUT=line; else export INPUT=eof; fi\n')
        self.assertEqual(dev_env['INPUT'], 'eof')

    def test_does_not_read_bash_env(self):
        old = self.root / 'env.rc'
        old.write_text('export OLD=1\n')
        dev_env, _ = self.dev_shell('', base=dict(BASE, BASH_ENV=str(old)))
        self.assertNotIn('OLD', dev_env)

    def test_removes_the_temporary_directory(self):
        dev_env, _ = self.dev_shell('export NIX_BUILD_TOP="$(mktemp -d -t nix-shell.XXXXXX)"\n'
                                    'export TMPDIR="$NIX_BUILD_TOP"\n')
        self.assertFalse(Path(dev_env['TMPDIR']).exists())

    def test_failing_shell_hook_keeps_its_environment(self):
        dev_env, status = self.dev_shell('export DONE=1\nfalse\n')
        self.assertEqual((dev_env['DONE'], status), ('1', 1))

    def test_shell_that_exits_has_no_environment(self):
        self.assertEqual(self.dev_shell('exit 3\n'), (None, 3))

    def test_shell_hook_that_hangs_times_out(self):
        self.assertEqual(self.dev_shell('sleep 30\n', timeout=0.5), (None, None))


class EnvRcTest(unittest.TestCase):
    """What env.rc keeps of the dev shell's environment."""

    def test_writes_what_the_dev_shell_changed(self):
        dev_env = dict(BASE, JAVA_HOME='/nix/store/jdk')
        del dev_env['GONE']
        env_rc = nix_env.env_rc(BASE, dev_env)
        self.assertNotIn('KEPT', env_rc)
        self.assertEqual(run(env_rc, before='export GONE=here', after='echo "$JAVA_HOME ${GONE-unset}"'),
                         '/nix/store/jdk unset\n')

    def test_drops_what_only_a_build_or_that_one_shell_needs(self):
        dev_env = dict(BASE, TMPDIR='/tmp/nix-shell.x', TZ='UTC', SOURCE_DATE_EPOCH='315532800',
                       SHELL='/nix/store/bash', SSL_CERT_FILE='/no-cert-file.crt', out='/x/outputs/out',
                       name='nix-shell-env', __structuredAttrs='', PWD='/workspace', SHLVL='2',
                       _='/usr/bin/env', **{'BASH_FUNC_f%%': '() { :; }'})
        self.assertEqual(nix_env.env_rc(dict(BASE, BASH_ENV='/etc/bash-env.sh'), dev_env), '')

    def test_prepends_where_the_dev_shell_prepended(self):
        dev_env = dict(BASE, PATH=f'/nix/store/a/bin:{BASE["PATH"]}')
        self.assertEqual(run(nix_env.env_rc(BASE, dev_env), before='PATH="/agent/bin:$PATH"', after='echo "$PATH"'),
                         f'/nix/store/a/bin:/agent/bin:{BASE["PATH"]}\n')

    def test_quotes_values(self):
        dev_env = dict(BASE, FOO="it's $HOME `x`")
        self.assertEqual(run(nix_env.env_rc(BASE, dev_env), after='printf %s "$FOO"'), "it's $HOME `x`")


class MainTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dev_env = self.root / 'dev-env.sh'
        self.env_rc = self.root / 'nix-env.rc'
        for name, value in [('DEV_ENV', self.dev_env), ('ENV_RC', self.env_rc), ('LOG', self.root / 'nix-env.log'),
                            ('WORKSPACE', self.root), ('TIMEOUT_SECONDS', 0.5)]:
            patcher = mock.patch.object(nix_env, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {'PATH': os.environ['PATH']}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def main(self, script):
        self.dev_env.write_text(script)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            nix_env.main()
        return stderr.getvalue()

    def test_writes_env_rc_for_node_alone(self):
        self.assertEqual(self.main('export JAVA_HOME=/nix/store/jdk\n'), '')
        self.assertEqual(run(self.env_rc.read_text(), after='echo $JAVA_HOME'), '/nix/store/jdk\n')
        self.assertEqual(self.env_rc.stat().st_mode & 0o777, 0o600)

    def test_warns_of_a_failing_shell_hook(self):
        self.assertIn('warning', self.main('export JAVA_HOME=/nix/store/jdk\nfalse\n'))
        self.assertIn('JAVA_HOME', self.env_rc.read_text())

    def test_writes_nothing_when_the_dev_shell_does_not_finish(self):
        self.assertIn('warning', self.main('sleep 30\n'))
        self.assertFalse(self.env_rc.exists())


if __name__ == '__main__':
    unittest.main()
