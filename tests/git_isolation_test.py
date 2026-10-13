"""Shared repository validation and ownership boundaries, without a Docker daemon."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = load('isolation_settings', '.devcontainer/claude/scripts/settings.py')
mounts = load('isolation_mounts', '.devcontainer/claude/agent/workspace_mounts.py')


class GitIsolationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-q')
        (self.root / '.devcontainer').mkdir()
        (self.root / '.devcontainer/claude.toml').write_text('[git]\nisolation = "shared"\n')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True)

    def test_local_override_and_generated_mode(self):
        self.assertEqual(settings.load(self.root)['git']['isolation'], 'shared')
        settings.generate(self.root, {})
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nisolation = "clone"\n')
        self.assertEqual(settings.load(self.root)['git']['isolation'], 'clone')
        for mode in ('shared', 'clone'):
            with self.assertRaisesRegex(ValueError, 'GIT_ISOLATION in the environment'):
                settings.generate(self.root, {'CLAUDE_DEVCONTAINER_GIT_ISOLATION': mode})

    def test_shared_rejects_linked_worktree_before_publishing_settings(self):
        self.git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '--allow-empty', '-qm', 'fixture')
        linked = self.root.parent / 'linked'
        self.git('worktree', 'add', '-b', 'linked', str(linked))
        (linked / '.devcontainer').mkdir()
        (linked / '.devcontainer/claude.toml').write_text('[git]\nisolation = "shared"\n')
        with self.assertRaisesRegex(ValueError, 'use "clone" for linked worktrees'):
            settings.generate(linked, {})
        self.assertFalse((linked / '.devcontainer/.generated').exists())

    def test_shared_rejects_external_git_objects(self):
        alternate = self.root / '.git/objects/info/alternates'
        alternate.write_text('/outside/objects\n')
        with self.assertRaisesRegex(ValueError, 'Unsupported Git storage'):
            settings.generate(self.root, {})
        alternate.unlink()
        (self.root / '.git/objects/linked').symlink_to('/outside')
        with self.assertRaisesRegex(ValueError, 'Unsupported Git storage'):
            settings.generate(self.root, {})

    def test_shared_chown_only_touches_dependency_mount_root(self):
        dependency = self.root / 'packages/app/node_modules'
        dependency.mkdir(parents=True)
        before = [(p, p.stat()) for p in [self.root, self.root / '.git', dependency.parent, dependency.parent.parent]]
        changed = []
        def record(fd, uid, gid):
            changed.append(os.fstat(fd).st_ino)
        with patch.object(mounts, 'dependency_mounts', return_value=[dependency]), patch.object(mounts.os, 'fchown', record):
            mounts.prepare(self.root, os.getuid() + 1, os.getgid(), 'shared')
        self.assertEqual(changed, [dependency.stat().st_ino])
        for path, status in before:
            self.assertEqual((path.stat().st_uid, path.stat().st_gid, path.stat().st_mode),
                             (status.st_uid, status.st_gid, status.st_mode))

    def test_shared_rejects_symlink_dependency_ancestors(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'packages').symlink_to(outside)
        with patch.object(mounts, 'dependency_mounts', return_value=[self.root / 'packages/node_modules']), \
                patch.object(mounts.os, 'fchown') as chown:
            with self.assertRaises(OSError):
                mounts.prepare(self.root, os.getuid() + 1, os.getgid(), 'shared')
        chown.assert_not_called()

    def test_shared_access_error_explains_uid_configuration(self):
        mounts.check_shared(self.root)
        with patch.object(mounts.os, 'access', return_value=False):
            with self.assertRaisesRegex(ValueError, 'agent.uid and agent.gid'):
                mounts.check_shared(self.root)

    def test_shared_only_prepares_overlay_roots(self):
        info = self.root / 'mountinfo'
        info.write_text('1 0 0:1 / /workspace/node_modules rw - overlay overlay rw\n'
                        '2 0 0:2 /host/deps /workspace/bind/node_modules rw - overlay overlay rw\n'
                        '3 0 0:3 / /workspace/volume/node_modules rw - ext4 /dev/sda rw\n')
        self.assertEqual(mounts.dependency_mounts(Path('/workspace'), info, overlay_only=True),
                         [Path('/workspace/node_modules')])

    def test_shared_rejects_absolute_core_worktree(self):
        self.git('config', 'core.worktree', str(self.root))
        with self.assertRaisesRegex(ValueError, 'Absolute core.worktree'):
            settings.generate(self.root, {})


if __name__ == '__main__':
    unittest.main()
