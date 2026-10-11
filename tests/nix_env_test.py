"""What nix-env.py keeps of a dev shell for the agent's Bash; no nix needed."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nix_env', ROOT / '.devcontainer/claude/scripts/nix-env.py')
nix_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nix_env)


def exported(**variables):
    return {name: {'type': 'exported', 'value': value} for name, value in variables.items()}


def run(dev_env, before='', after=''):
    """Source env_rc(dev_env) in Bash between the two snippets; return the output."""
    script = before + '\n' + nix_env.env_rc(dev_env) + after
    return subprocess.run(['bash', '--norc', '--noprofile', '-c', script],
                          stdout=subprocess.PIPE, text=True, check=True, env={}).stdout


class EnvRcTest(unittest.TestCase):
    def test_keeps_the_dev_shell_variables(self):
        dev_env = {'bashFunctions': {'genericBuild': ''}, 'variables': {
            **exported(PLAYWRIGHT_BROWSERS_PATH='/nix/store/b', PYTHONPATH='/nix/store/p',
                       NIX_LDFLAGS='-rpath /nix/store/x'),
            'name': {'type': 'var', 'value': 'not exported'},
        }}
        self.assertEqual(run(dev_env, after='echo $PLAYWRIGHT_BROWSERS_PATH $PYTHONPATH $NIX_LDFLAGS'),
                         '/nix/store/b /nix/store/p -rpath /nix/store/x\n')

    def test_drops_what_only_a_build_needs(self):
        dev_env = {'bashFunctions': {}, 'variables': exported(
            TMPDIR='/build', TZ='UTC', SOURCE_DATE_EPOCH='315532800', SHELL='/nix/store/bash',
            SSL_CERT_FILE='/no-cert-file.crt', out='/nix/store/out', name='nix-shell-env',
            __structuredAttrs='', shellHook='echo hello')}
        self.assertEqual(nix_env.env_rc(dev_env), '')

    def test_prepends_to_the_search_paths(self):
        dev_env = {'bashFunctions': {}, 'variables': exported(PATH='/nix/store/a/bin', XDG_DATA_DIRS='/nix/store/a/share')}
        self.assertEqual(run(dev_env, before='PATH=/usr/bin', after='echo $PATH; echo ${XDG_DATA_DIRS-unset}'),
                         '/nix/store/a/bin:/usr/bin\n/nix/store/a/share\n')

    def test_quotes_values(self):
        dev_env = {'bashFunctions': {}, 'variables': exported(FOO="it's $HOME `x`")}
        self.assertEqual(run(dev_env, after='printf %s "$FOO"'), "it's $HOME `x`")


if __name__ == '__main__':
    unittest.main()
