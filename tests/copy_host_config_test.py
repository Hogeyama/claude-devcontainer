"""Which host files copy-host-config.py sends to the agent; no Docker needed."""
import importlib.util
import io
import json
from pathlib import Path
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
        self.extra = self.root / 'extra'
        self.elsewhere = self.root / 'store'
        for path in (self.source / 'skills/mine', self.source / 'projects/-workspace', self.elsewhere):
            path.mkdir(parents=True)

    def members(self):
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.extra))) as tar:
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
        self.extra.mkdir()
        (self.extra / '.claude').symlink_to(self.source)
        self.assertFalse([name for name in self.members() if 'sumi' in name])

    def test_never_copies_the_secrets_files_sumi_uses_wherever_they_are(self):
        (self.source / 'keys').mkdir()
        for name in ('host.txt', 'container.txt', 'prefix.txt', 'other.txt'):
            (self.source / 'keys' / name).write_text(name)
        self.settings({
            'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': '/opt/sumi',
                                                  'args': ['hook', '--secrets-file', str(self.source / 'keys/host.txt')]}]}]},
            'env': {'CLAUDE_CODE_SHELL_PREFIX': f"bash -c 'exec \"$@\"' p /opt/sumi run --secrets-file {self.source}/keys/prefix.txt"}})
        self.extra.mkdir()
        (self.extra / '.claude').symlink_to(self.source)
        with mock.patch.dict('os.environ', CLAUDE_DEVCONTAINER_SUMI_DIR=str(self.source / 'keys'),
                             CLAUDE_DEVCONTAINER_SUMI_FILE='container.txt'):
            members = self.members()
        self.assertEqual({name for name in members if name.startswith('.claude/keys/')}, {'.claude/keys/other.txt'})

    def test_extra_overlays_the_home_directory(self):
        self.settings({'model': 'opus'})
        (self.extra / '.claude/skills/added').mkdir(parents=True)
        (self.extra / '.claude/skills/added/SKILL.md').write_text('added')
        (self.extra / '.claude/settings.json').write_text('{"model": "sonnet"}')
        (self.extra / '.gitconfig').write_text('[user]\n\tname = t\n')
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.extra))) as tar:
            names = tar.getnames()
            settings = [tar.extractfile(member).read() for member in tar.getmembers() if member.name == '.claude/settings.json']
        self.assertIn('.claude/skills/added/SKILL.md', names)
        self.assertIn('.gitconfig', names)
        # Extraction keeps the last one.
        self.assertEqual(json.loads(settings[-1]), {'model': 'sonnet'})

    def test_extra_linking_all_of_claude_still_leaves_credentials(self):
        (self.source / '.credentials.json').write_text('secret')
        self.extra.mkdir()
        (self.extra / '.claude').symlink_to(self.source)
        self.assertNotIn('.claude/.credentials.json', self.members())

    def test_read_only_files_stay_replaceable(self):
        (self.source / 'skills/mine/SKILL.md').write_text('skill')
        (self.source / 'skills/mine/SKILL.md').chmod(0o444)
        (self.source / 'skills/mine').chmod(0o555)
        self.addCleanup((self.source / 'skills/mine').chmod, 0o755)
        with tarfile.open(fileobj=io.BytesIO(copy_host_config.archive(self.source, self.extra))) as tar:
            modes = {member.name: member.mode for member in tar.getmembers()}
        self.assertEqual(modes['.claude/skills/mine'] & 0o700, 0o700)
        self.assertEqual(modes['.claude/skills/mine/SKILL.md'] & 0o600, 0o600)

    def test_works_without_any_configuration(self):
        self.source.rename(self.root / 'gone')
        self.assertEqual(self.members(), {})


if __name__ == '__main__':
    unittest.main()
