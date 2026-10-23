"""Which host files copy-host-config.py sends to the agent; no Docker needed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('copy_host_config', ROOT / '.devcontainer/claude/scripts/copy-host-config.py')
copy_host_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_host_config)


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'claude'
        self.dotfiles = self.root / 'dotfiles'
        self.elsewhere = self.root / 'store'
        for path in (self.source / 'skills/mine', self.source / 'projects/-workspace', self.elsewhere):
            path.mkdir(parents=True)

    def members(self):
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.dotfiles))) as tar:
            return {member.name: tar.extractfile(member).read().decode() if member.isfile() else None
                    for member in tar.getmembers()}

    def settings(self, value):
        (self.source / 'settings.json').write_text(json.dumps(value))

    def test_copies_configuration_but_not_state_or_credentials(self):
        self.settings({'model': 'opus'})
        (self.source / 'skills/mine/SKILL.md').write_text('skill')
        for name in ('.credentials.json', 'history.jsonl', 'projects/-workspace/session.jsonl'):
            (self.source / name).write_text('secret')
        self.assertEqual(set(self.members()), {'.claude/settings.json', '.claude/skills', '.claude/skills/mine', '.claude/skills/mine/SKILL.md'})

    def test_follows_links_on_the_host(self):
        (self.elsewhere / 'statusline.sh').write_text('echo status')
        (self.source / 'statusline.sh').symlink_to(self.elsewhere / 'statusline.sh')
        (self.source / 'skills/linked').symlink_to(self.elsewhere)
        (self.source / 'skills/dangling').symlink_to(self.root / 'missing')
        (self.source / 'skills/loop').symlink_to(self.source / 'skills')
        self.settings({'statusLine': {'type': 'command', 'command': 'bash ~/.claude/statusline.sh'}})
        members = self.members()
        self.assertEqual(members['.claude/statusline.sh'], 'echo status')
        self.assertEqual(members['.claude/skills/linked/statusline.sh'], 'echo status')
        self.assertNotIn('.claude/skills/dangling', members)
        self.assertNotIn('.claude/skills/loop', members)

    def test_copies_files_that_hooks_name_except_credentials_and_outside_paths(self):
        for name in ('hook.sh', 'other.sh', '.credentials.json'):
            (self.source / name).write_text(name)
        (self.root / 'outside.sh').write_text('outside')
        self.settings({'hooks': {'Stop': [{'hooks': [
            {'type': 'command', 'command': '"$HOME/.claude/hook.sh" && cat ~/.claude/.credentials.json'},
            {'type': 'command', 'command': 'sh ${HOME}/.claude/../outside.sh; cat ~/.claude//etc/hostname'}]}]}})
        members = self.members()
        self.assertEqual({name for name in members if not name.startswith('.claude/skills')},
                         {'.claude/settings.json', '.claude/hook.sh'})

    def test_drops_the_hosts_sumi_hooks_and_shell(self):
        sumi = {'type': 'command', 'command': '/home/me/.local/bin/sumi',
                'args': ['hook', '--agent', 'claude', 'post-tool', '--secrets-file', '/home/me/.claude/sumi/secrets.txt']}
        other = {'type': 'command', 'command': 'notify-send done'}
        self.settings({
            'hooks': {'PostToolUse': [{'hooks': [sumi]}],
                      'UserPromptSubmit': [{'hooks': [dict(sumi, args=['hook', '--agent', 'claude', 'prompt'])]}],
                      'Stop': [{'hooks': [sumi, other]}]},
            'env': {'KEEP': '1', 'CLAUDE_CODE_SHELL': '/nix/store/x-bash/bin/bash',
                    'CLAUDE_CODE_SHELL_PREFIX': "/bin/bash -c 'exec \"$@\"' 'sumi-prefix' '/home/me/.local/bin/sumi' 'run'"}})
        (self.source / 'sumi').mkdir()
        (self.source / 'sumi/secrets.txt').write_text('Tr0ub4dor')
        members = self.members()
        self.assertEqual(json.loads(members['.claude/settings.json']),
                         {'hooks': {'Stop': [{'hooks': [other]}]}, 'env': {'KEEP': '1'}})
        self.assertFalse([name for name in members if 'sumi' in name])

    def test_never_copies_sumi_secrets_even_when_named(self):
        (self.source / 'sumi').mkdir()
        (self.source / 'sumi/secrets.txt').write_text('Tr0ub4dor')
        self.settings({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'cat ~/.claude/sumi/secrets.txt'}]}]}})
        self.dotfiles.mkdir()
        (self.dotfiles / '.claude').symlink_to(self.source)
        self.assertFalse([name for name in self.members() if 'sumi' in name])

    def test_never_copies_the_secrets_files_sumi_uses_wherever_they_are(self):
        (self.source / 'keys').mkdir()
        for name in ('host.txt', 'container.txt', 'prefix.txt', 'other.txt'):
            (self.source / 'keys' / name).write_text(name)
        self.settings({
            'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': '/opt/sumi',
                                                  'args': ['hook', '--secrets-file', str(self.source / 'keys/host.txt')]}]}]},
            'env': {'CLAUDE_CODE_SHELL_PREFIX': f"bash -c 'exec \"$@\"' p /opt/sumi run --secrets-file {self.source}/keys/prefix.txt"}})
        self.dotfiles.mkdir()
        (self.dotfiles / '.claude').symlink_to(self.source)
        self.workspace(f'[sumi]\nsecrets = "{self.source}/keys/container.txt"\n')
        with mock.patch.object(copy_host_config, 'ROOT', self.root):
            members = self.members()
        self.assertEqual({name for name in members if name.startswith('.claude/keys/')}, {'.claude/keys/other.txt'})

    def workspace(self, shared, local=None):
        (self.root / '.devcontainer').mkdir(exist_ok=True)
        (self.root / '.devcontainer/claude.toml').write_text(shared)
        if local is not None:
            (self.root / '.devcontainer/claude.local.toml').write_text(local)

    def test_relative_sumi_secrets_are_excluded_from_copied_config(self):
        (self.source / 'keys').mkdir()
        (self.source / 'keys/secrets.txt').write_text('test-only-secret')
        (self.source / 'keys/other.txt').write_text('ordinary config')
        self.dotfiles.mkdir()
        (self.dotfiles / '.claude').symlink_to(self.source)
        # Relative to .devcontainer/; claude.local.toml overrides claude.toml.
        self.workspace('[sumi]\nsecrets = "../claude/keys/other.txt"\n', '[sumi]\nsecrets = "../claude/keys/secrets.txt"\n')
        with mock.patch.object(copy_host_config, 'ROOT', self.root):
            members = self.members()
        self.assertNotIn('.claude/keys/secrets.txt', members)
        self.assertIn('.claude/keys/other.txt', members)

    def test_invalid_settings_copy_nothing(self):
        # Which file sumi.secrets names is unknown, and dotfiles/ may link to it.
        (self.source / 'keys').mkdir()
        (self.source / 'keys/secrets.txt').write_text('test-only-secret')
        self.dotfiles.mkdir()
        (self.dotfiles / '.claude').symlink_to(self.source)
        self.workspace('[sumi]\nsecrets = "../claude/keys/secrets.txt"\n[docker]\nenabeld = true\n')
        with mock.patch.object(copy_host_config, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'enabeld'):
            self.members()
        script = self.root / '.devcontainer/claude/scripts'
        script.mkdir(parents=True)
        for name in ('copy-host-config.py', 'settings.py', 'layout.py'):
            shutil.copy(ROOT / '.devcontainer/claude/scripts' / name, script / name)
        (self.root / '.devcontainer/claude/proxy').mkdir()
        shutil.copy(ROOT / '.devcontainer/claude/proxy/policy.py', self.root / '.devcontainer/claude/proxy/policy.py')
        result = subprocess.run([sys.executable, str(script / 'copy-host-config.py'), '--background'],
                                env={**os.environ, 'CLAUDE_CONFIG_DIR': str(self.source),
                                     'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_STATE_HOME': str(self.root / 'state')},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Nothing was copied', result.stderr)
        self.assertFalse((self.root / 'state').exists())

    def test_dotfiles_overlay_the_home_directory(self):
        self.settings({'model': 'opus'})
        (self.dotfiles / '.claude/skills/added').mkdir(parents=True)
        (self.dotfiles / '.claude/skills/added/SKILL.md').write_text('added')
        (self.dotfiles / '.claude/settings.json').write_text('{"model": "sonnet"}')
        (self.dotfiles / '.gitconfig').write_text('[user]\n\tname = t\n')
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.dotfiles))) as tar:
            names = tar.getnames()
            settings = [tar.extractfile(member).read() for member in tar.getmembers() if member.name == '.claude/settings.json']
        self.assertIn('.claude/skills/added/SKILL.md', names)
        self.assertIn('.gitconfig', names)
        # Extraction keeps the last one.
        self.assertEqual(json.loads(settings[-1]), {'model': 'sonnet'})

    def test_dotfiles_linking_all_of_claude_still_leaves_credentials(self):
        (self.source / '.credentials.json').write_text('secret')
        self.dotfiles.mkdir()
        (self.dotfiles / '.claude').symlink_to(self.source)
        self.assertNotIn('.claude/.credentials.json', self.members())

    def test_the_former_name_of_dotfiles_is_refused(self):
        legacy = self.root / 'extra'
        copy_host_config.check_legacy_dotfiles(legacy, self.dotfiles)
        legacy.symlink_to(self.root / 'gone')
        with self.assertRaisesRegex(SystemExit, 'mv .*/extra .*/dotfiles'):
            copy_host_config.check_legacy_dotfiles(legacy, self.dotfiles)

    def test_read_only_files_stay_replaceable(self):
        (self.source / 'skills/mine/SKILL.md').write_text('skill')
        (self.source / 'skills/mine/SKILL.md').chmod(0o444)
        (self.source / 'skills/mine').chmod(0o555)
        self.addCleanup((self.source / 'skills/mine').chmod, 0o755)
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.dotfiles))) as tar:
            modes = {member.name: member.mode for member in tar.getmembers()}
        self.assertEqual(modes['.claude/skills/mine'] & 0o700, 0o700)
        self.assertEqual(modes['.claude/skills/mine/SKILL.md'] & 0o600, 0o600)

    def test_works_without_any_configuration(self):
        self.source.rename(self.root / 'gone')
        self.assertEqual(self.members(), {})


class WaitAndCopyTest(unittest.TestCase):
    """How the detached copy waits for the agent that `up` starts."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.watcher = Path(directory.name) / 'watcher.pid'
        self.clock = 0
        # (seconds since the start, agent) in order; the last one reached is running.
        self.timeline = []
        self.copies = []
        for name, value in [('WATCHER', self.watcher), ('find_agent', self.agent),
                            ('copy', lambda agent, data: self.copies.append(agent))]:
            patcher = mock.patch.object(copy_host_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in [('monotonic', lambda: self.clock), ('sleep', self.sleep)]:
            patcher = mock.patch.object(copy_host_config.time, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch('sys.stdout', io.StringIO())
        patcher.start()
        self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.clock += seconds

    def agent(self):
        running = [agent for start, agent in self.timeline if start <= self.clock]
        return running[-1] if running else None

    def test_waits_for_an_agent_that_starts_after_a_slow_build(self):
        # `up` builds the images after initializeCommand starts the copy.
        self.timeline = [(90, 'agent')]
        copy_host_config.wait_and_copy(b'archive')
        self.assertEqual(self.copies, ['agent'])

    def test_copies_again_into_an_agent_recreated_after_a_slow_build(self):
        self.timeline = [(0, 'old'), (90, None), (120, 'new')]
        copy_host_config.wait_and_copy(b'archive')
        self.assertEqual(self.copies, ['old', 'new'])

    def test_a_newer_copy_takes_over(self):
        # A later `up` starts another copy, with the host's configuration as it is then.
        def sleep(seconds):
            self.clock += seconds
            if self.clock >= 30:
                self.watcher.write_text('another process')
        self.timeline = [(90, 'agent')]
        with mock.patch.object(copy_host_config.time, 'sleep', sleep):
            copy_host_config.wait_and_copy(b'archive')
        self.assertEqual(self.copies, [])


if __name__ == '__main__':
    unittest.main()
