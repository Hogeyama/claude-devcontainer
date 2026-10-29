"""The host command, with stand-ins for devcontainer and Docker."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / '.devcontainer/claude'
COMMAND = CORE / 'scripts/claude-devcontainer'
FAKE = '''#!{python}
import json, os, sys
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps({{'argv': [os.path.basename(sys.argv[0]), *sys.argv[1:]],
                          'env': {{k: v for k, v in os.environ.items() if k.startswith('CLAUDE_DEVCONTAINER_')}}}}) + '\\n')
if os.path.basename(sys.argv[0]) == 'docker' and sys.argv[1] == 'ps':
    print(os.environ.get('FAKE_PROJECTS', ''), end='')
if os.path.basename(sys.argv[0]) == 'docker' and 'exec' in sys.argv and os.environ.get('FAKE_AGENT_STOPPED'):
    sys.exit('service "agent" is not running')
'''


class HostCommandTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.top = Path(directory.name)
        self.repo = self.top / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        binary = self.top / 'bin'
        binary.mkdir()
        for name in ('devcontainer', 'docker'):
            (binary / name).write_text(FAKE.format(python=sys.executable))
            (binary / name).chmod(0o755)
        self.log = self.top / 'calls'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_DEVCONTAINER_')}
        self.env.update(PATH=f'{binary}:{os.environ["PATH"]}', FAKE_LOG=str(self.log),
                        XDG_STATE_HOME=str(self.top / 'state'))

    def run_command(self, *args, cwd=None, check=True):
        return subprocess.run([sys.executable, str(COMMAND), *args], cwd=cwd or self.repo, env=self.env,
                              capture_output=True, text=True, check=check)

    def project(self):
        return 'cdc-' + hashlib.sha256(os.fsencode(self.repo.resolve())).hexdigest()[:16]

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_up_starts_with_the_core_config_and_names_the_state(self):
        self.run_command('up')
        up = [call for call in self.calls() if call['argv'][:2] == ['devcontainer', 'up']]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0]['argv'][2:], ['--workspace-folder', str(self.repo.resolve()),
                                             '--config', str(CORE / 'host/devcontainer.json')])
        env = up[0]['env']
        self.assertEqual(env['CLAUDE_DEVCONTAINER_WORKSPACE'], str(self.repo.resolve()))
        self.assertTrue(env['CLAUDE_DEVCONTAINER_CONFIG_DIR'].startswith(str(self.top / 'state')))
        self.assertTrue(Path(env['CLAUDE_DEVCONTAINER_CONFIG_DIR']).is_dir())
        self.assertRegex(env['CLAUDE_DEVCONTAINER_WORKSPACE_ID'], r'^[0-9a-f]{16}$')
        self.assertEqual(env['CLAUDE_DEVCONTAINER_CORE'], str(CORE))
        self.assertFalse((self.repo / '.devcontainer').exists())

    def test_no_argument_opens_claude_after_starting(self):
        self.run_command()
        argv = [call['argv'] for call in self.calls() if call['argv'][0] == 'devcontainer']
        self.assertEqual([a[1] for a in argv], ['up', 'exec'])
        self.assertEqual(argv[1][-1], 'claude')

    def test_code_server_points_to_the_host_settings(self):
        self.env['XDG_CONFIG_HOME'] = str(self.top / 'config')
        result = self.run_command('code-server', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.top / 'config/claude-devcontainer/claude.toml'), result.stderr)
        self.assertIn('claude-devcontainer down', result.stderr)

    def test_code_server_explains_an_agent_created_without_it(self):
        config = self.top / 'config/claude-devcontainer'
        config.mkdir(parents=True)
        (config / 'claude.toml').write_text('[codeServer]\nenabled = true\n')
        # This repository's own environment, which compose.sh finds.
        self.env.update(XDG_CONFIG_HOME=str(self.top / 'config'), FAKE_PROJECTS=self.project() + '\n')
        result = self.run_command('code-server', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('created without codeServer.enabled', result.stderr)
        self.assertIn('claude-devcontainer down', result.stderr)

    def test_code_server_tells_a_stopped_agent_apart(self):
        config = self.top / 'config/claude-devcontainer'
        config.mkdir(parents=True)
        (config / 'claude.toml').write_text('[codeServer]\nenabled = true\n')
        self.env.update(XDG_CONFIG_HOME=str(self.top / 'config'), FAKE_PROJECTS=self.project() + '\n',
                        FAKE_AGENT_STOPPED='1')
        result = self.run_command('code-server', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('the agent is not running', result.stderr)
        self.assertNotIn('created without codeServer.enabled', result.stderr)

    def test_code_server_starts_the_environment_first(self):
        self.env['XDG_CONFIG_HOME'] = str(self.top / 'config')
        self.run_command('code-server', check=False)
        argv = [call['argv'] for call in self.calls() if call['argv'][0] == 'devcontainer']
        self.assertEqual([a[1] for a in argv], ['up'])

    def test_code_server_prepare_starts_nothing(self):
        self.env['XDG_CONFIG_HOME'] = str(self.top / 'config')
        self.run_command('code-server', 'prepare', check=False)
        self.assertFalse(self.log.exists() and any(call['argv'][0] == 'devcontainer' for call in self.calls()))

    def test_runs_from_a_subdirectory(self):
        (self.repo / 'nested').mkdir()
        self.run_command('up', cwd=self.repo / 'nested')
        up = [call for call in self.calls() if call['argv'][:2] == ['devcontainer', 'up']]
        self.assertEqual(up[0]['argv'][3], str(self.repo.resolve()))

    def test_refuses_outside_git(self):
        outside = self.top / 'outside'
        outside.mkdir()
        result = self.run_command('up', cwd=outside, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Git', result.stderr)

    def test_refuses_a_repository_mode_environment_of_the_same_folder(self):
        self.env['FAKE_PROJECTS'] = 'repo_devcontainer\n'
        result = self.run_command('up', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('repo_devcontainer', result.stderr)
        self.assertFalse(any(call['argv'][0] == 'devcontainer' for call in self.calls()))

    def test_refuses_its_environment_from_another_core(self):
        self.env['FAKE_PROJECTS'] = f'{self.project()}\t/old/core/host/devcontainer.json\n'
        result = self.run_command('up', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/old/core/host/devcontainer.json', result.stderr)
        self.assertIn('claude-devcontainer down', result.stderr)
        self.assertFalse(any(call['argv'][0] == 'devcontainer' for call in self.calls()))

    def test_keeps_its_environment_from_this_core(self):
        self.env['FAKE_PROJECTS'] = f'{self.project()}\t{CORE / "host/devcontainer.json"}\n'
        self.run_command('up')
        self.assertTrue(any(call['argv'][:2] == ['devcontainer', 'up'] for call in self.calls()))

    def test_down_removes_the_services_of_an_earlier_core(self):
        self.env['FAKE_PROJECTS'] = self.project() + '\n'
        self.run_command('down', check=False)
        compose = [call['argv'] for call in self.calls() if call['argv'][:2] == ['docker', 'compose']]
        self.assertTrue(compose)
        self.assertEqual(compose[-1][-2:], ['down', '--remove-orphans'])

    def test_exec_runs_in_agent_and_preserves_command_arguments(self):
        self.env['FAKE_PROJECTS'] = self.project() + '\n'
        self.run_command('exec', 'sh', '-c', 'printf "%s" "hello world"')
        compose = [call['argv'] for call in self.calls() if call['argv'][:2] == ['docker', 'compose']]
        self.assertEqual(compose[-1][-7:], ['exec', '--user', 'node', 'agent', 'sh', '-c', 'printf "%s" "hello world"'])
        self.assertFalse(any(call['argv'][0] == 'devcontainer' for call in self.calls()))


class InstallTest(unittest.TestCase):
    def test_installs_the_committed_core_and_links_the_command(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / 'data'))
            subprocess.run(['sh', str(CORE / 'scripts/install-host.sh')], env=env, check=True,
                           capture_output=True, text=True)
            data = home / 'data/claude-devcontainer'
            current = data / 'current'
            self.assertTrue(current.is_symlink())
            self.assertTrue((current / 'host/compose.yaml').is_file())
            self.assertTrue(os.access(current / 'scripts/claude-devcontainer', os.X_OK))
            link = home / '.local/bin/claude-devcontainer'
            self.assertEqual(link.resolve(), (current / 'scripts/claude-devcontainer').resolve())


if __name__ == '__main__':
    unittest.main()
