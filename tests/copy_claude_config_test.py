"""Which host files copy-claude-config.py sends to the agent; no Docker needed."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('copy_claude_config', ROOT / '.devcontainer/claude/scripts/copy-claude-config.py')
copy_claude_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_claude_config)


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
        with tarfile.open(fileobj=io.BytesIO(copy_claude_config.archive(self.source, self.extra))) as tar:
            return {member.name: tar.extractfile(member).read().decode() if member.isfile() else None
                    for member in tar.getmembers()}

    def settings(self, value):
        (self.source / 'settings.json').write_text(json.dumps(value))

    def test_copies_configuration_but_not_state_or_credentials(self):
        self.settings({'model': 'opus'})
        (self.source / 'skills/mine/SKILL.md').write_text('skill')
        for name in ('.credentials.json', 'history.jsonl', 'projects/-workspace/session.jsonl'):
            (self.source / name).write_text('secret')
        self.assertEqual(set(self.members()), {'settings.json', 'skills', 'skills/mine', 'skills/mine/SKILL.md'})

    def test_follows_links_on_the_host(self):
        (self.elsewhere / 'statusline.sh').write_text('echo status')
        (self.source / 'statusline.sh').symlink_to(self.elsewhere / 'statusline.sh')
        (self.source / 'skills/linked').symlink_to(self.elsewhere)
        (self.source / 'skills/dangling').symlink_to(self.root / 'missing')
        (self.source / 'skills/loop').symlink_to(self.source / 'skills')
        self.settings({'statusLine': {'type': 'command', 'command': 'bash ~/.claude/statusline.sh'}})
        members = self.members()
        self.assertEqual(members['statusline.sh'], 'echo status')
        self.assertEqual(members['skills/linked/statusline.sh'], 'echo status')
        self.assertNotIn('skills/dangling', members)
        self.assertNotIn('skills/loop', members)

    def test_copies_files_that_hooks_name_except_credentials_and_outside_paths(self):
        for name in ('hook.sh', 'other.sh', '.credentials.json'):
            (self.source / name).write_text(name)
        (self.root / 'outside.sh').write_text('outside')
        self.settings({'hooks': {'Stop': [{'hooks': [
            {'type': 'command', 'command': '"$HOME/.claude/hook.sh" && cat ~/.claude/.credentials.json'},
            {'type': 'command', 'command': 'sh ${HOME}/.claude/../outside.sh'}]}]}})
        members = self.members()
        self.assertIn('hook.sh', members)
        for name in ('other.sh', '.credentials.json', '../outside.sh', 'outside.sh'):
            self.assertNotIn(name, members)

    def test_extra_overlays_the_copied_configuration(self):
        self.settings({'model': 'opus'})
        (self.extra / 'skills/added').mkdir(parents=True)
        (self.extra / 'skills/added/SKILL.md').write_text('added')
        (self.extra / 'settings.json').write_text('{"model": "sonnet"}')
        (self.extra / '.credentials.json').write_text('secret')
        with tarfile.open(fileobj=io.BytesIO(copy_claude_config.archive(self.source, self.extra))) as tar:
            names = tar.getnames()
            settings = [tar.extractfile(member).read() for member in tar.getmembers() if member.name == 'settings.json']
        self.assertIn('skills/added/SKILL.md', names)
        self.assertNotIn('.credentials.json', names)
        # Extraction keeps the last one.
        self.assertEqual(json.loads(settings[-1]), {'model': 'sonnet'})

    def test_read_only_files_stay_replaceable(self):
        (self.source / 'skills/mine/SKILL.md').write_text('skill')
        (self.source / 'skills/mine/SKILL.md').chmod(0o444)
        (self.source / 'skills/mine').chmod(0o555)
        self.addCleanup((self.source / 'skills/mine').chmod, 0o755)
        with tarfile.open(fileobj=io.BytesIO(copy_claude_config.archive(self.source, self.extra))) as tar:
            modes = {member.name: member.mode for member in tar.getmembers()}
        self.assertEqual(modes['skills/mine'] & 0o700, 0o700)
        self.assertEqual(modes['skills/mine/SKILL.md'] & 0o600, 0o600)

    def test_works_without_any_configuration(self):
        self.source.rename(self.root / 'gone')
        self.assertEqual(self.members(), {})


if __name__ == '__main__':
    unittest.main()
