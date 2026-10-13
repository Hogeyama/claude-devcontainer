"""Shared repository validation and ownership boundaries, without a Docker daemon."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

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
        with patch.object(mounts.os, 'access', return_value=False):
            with self.assertRaisesRegex(ValueError, 'agent.uid and agent.gid'):
                mounts.check_shared(self.root)

    def test_shared_requires_read_only_management_mounts(self):
        with self.assertRaisesRegex(ValueError, 'must be mounted read-only'):
            mounts.check_shared(self.root)
        def access(path, mode):
            return not (Path(path) != self.root and mode & os.W_OK)
        with patch.object(mounts.os, 'access', side_effect=access), \
                patch.object(mounts.os, 'statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            mounts.check_shared(self.root)
        # chmod alone is insufficient, even when W_OK is false.
        with patch.object(mounts.os, 'access', side_effect=access):
            with self.assertRaisesRegex(ValueError, 'must be mounted read-only'):
                mounts.check_shared(self.root)

    def generated_mounts(self):
        settings.generate(self.root, {})
        data = json.loads((self.root / '.devcontainer/.generated/workspace.compose.yaml').read_text())
        return {m['target']: m for m in data['services']['agent']['volumes']}

    def test_shared_protects_whole_management_directories(self):
        generated = self.generated_mounts()
        for name in ('.git', '.devcontainer'):
            mount = generated['/workspace/' + name]
            self.assertEqual(mount, {'type': 'bind', 'source': str(self.root / name),
                                    'target': '/workspace/' + name, 'read_only': True,
                                    'bind': {'create_host_path': False}})
        self.assertFalse(generated['/workspace'].get('read_only', False))
        self.assertNotIn('/workspace/.secrets', generated)

    def enable_shared_writes(self):
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nshared_writable = true\n')

    def test_shared_writable_protects_existing_sensitive_paths_without_creating_state(self):
        self.enable_shared_writes()
        (self.root / '.git/config.worktree').write_text('[user]\nname = fixture\n')
        for name in ('rebase-merge', 'rebase-apply', 'sequencer', 'modules', 'worktrees'):
            (self.root / '.git' / name).mkdir()
        todo = self.root / '.git/rebase-merge/git-rebase-todo'
        todo.write_text('exec echo trusted\n')
        before = sorted(str(p.relative_to(self.root / '.git')) for p in (self.root / '.git').rglob('*'))
        generated = self.generated_mounts()
        self.assertFalse(generated['/workspace/.git']['read_only'])
        self.assertTrue(generated['/workspace/.devcontainer']['read_only'])
        for name in ('config', 'config.worktree', 'hooks', 'info', 'objects/info',
                     'rebase-merge', 'rebase-apply', 'sequencer', 'modules', 'worktrees'):
            mount = generated['/workspace/.git/' + name]
            self.assertTrue(mount['read_only'])
            self.assertEqual(mount['source'], str(self.root / '.git' / name))
            self.assertFalse(mount['bind']['create_host_path'])
        for name in ('HEAD', 'index', 'objects', 'refs', 'commondir', 'gitdir'):
            self.assertNotIn('/workspace/.git/' + name, generated)
        self.assertEqual(before, sorted(str(p.relative_to(self.root / '.git')) for p in (self.root / '.git').rglob('*')))
        self.assertEqual(todo.read_text(), 'exec echo trusted\n')

    def test_shared_writable_does_not_invent_rebase_state(self):
        self.enable_shared_writes()
        generated = self.generated_mounts()
        for name in ('rebase-merge', 'rebase-apply', 'sequencer', 'config.worktree', 'commondir'):
            self.assertNotIn('/workspace/.git/' + name, generated)
            self.assertFalse((self.root / '.git' / name).exists())
        self.git('status', '--short')

    def test_shared_writable_startup_checks_sensitive_mounts_and_writable_git(self):
        protected = {self.root / '.devcontainer', *mounts.git_protected_paths(self.root)}
        def access(path, mode):
            return not (Path(path) in protected and mode & os.W_OK)
        def statvfs(path):
            return SimpleNamespace(f_flag=os.ST_RDONLY if Path(path) in protected else 0)
        with patch.object(mounts.os, 'access', side_effect=access), \
                patch.object(mounts.os, 'statvfs', side_effect=statvfs):
            mounts.check_shared(self.root, writable=True)
            protected.remove(self.root / '.git/hooks')
            with self.assertRaisesRegex(ValueError, 'hooks must be mounted read-only'):
                mounts.check_shared(self.root, writable=True)
            protected.add(self.root / '.git/hooks')
            protected.add(self.root / '.git')
            with self.assertRaisesRegex(ValueError, 'must be writable with git.shared_writable'):
                mounts.check_shared(self.root, writable=True)

    def test_shared_writable_environment_cannot_override_toml(self):
        with self.assertRaisesRegex(ValueError, 'GIT_SHARED_WRITABLE in the environment'):
            settings.generate(self.root, {'CLAUDE_DEVCONTAINER_GIT_SHARED_WRITABLE': '1'})

    def test_clone_ignores_shared_writable(self):
        (self.root / '.devcontainer/claude.local.toml').write_text(
            '[git]\nisolation = "clone"\nshared_writable = true\n')
        self.assertEqual(self.generated_mounts(), {'/workspace': {'type': 'volume', 'source': 'workspace',
                                                               'target': '/workspace'}})

    def test_shared_masks_configured_paths_and_sumi(self):
        (self.root / 'vault').mkdir()
        (self.root / 'vault/secret').write_text('hidden')
        (self.root / 'private').mkdir()
        (self.root / 'private/sumi.txt').write_text('hidden')
        (self.root / '.env').write_text('hidden')
        (self.root / '.devcontainer/claude.local.toml').write_text(
            '[git]\nshared_hidden_paths = [".env", "vault", "vault/secret"]\n'
            '[sumi]\nsecrets = "../private/sumi.txt"\n')
        generated = self.generated_mounts()
        self.assertEqual(generated['/workspace/vault'],
                         {'type': 'tmpfs', 'target': '/workspace/vault', 'read_only': True,
                          'tmpfs': {'size': 4096, 'mode': 0o555}})
        self.assertNotIn('/workspace/vault/secret', generated)
        for target in ('/workspace/.env', '/workspace/private/sumi.txt'):
            mount = generated[target]
            self.assertEqual(mount['type'], 'bind')
            self.assertTrue(mount['read_only'])
            self.assertFalse(mount['bind']['create_host_path'])
            self.assertEqual(Path(mount['source']).read_bytes(), b'')
        self.assertEqual((self.root / '.env').read_text(), 'hidden')

    def test_shared_rejects_missing_extra_and_reserved_paths(self):
        for path in ('missing', '.git/config', '.devcontainer', '.devcontainer/.generated/key',
                     '.devcontainer/.nix-env'):
            with self.subTest(path=path):
                (self.root / '.devcontainer/claude.local.toml').write_text(
                    '[git]\nshared_hidden_paths = ' + json.dumps([path]) + '\n')
                with self.assertRaises(ValueError):
                    settings.generate(self.root, {})

    def test_hidden_paths_must_stay_inside_workspace(self):
        for path in ('', '.', '..', 'private/../../key', '/tmp/secret', 'line\nbreak'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'shared_hidden_paths'):
                settings.check({'git': {'shared_hidden_paths': [path]}}, 'test')

    def test_shared_rejects_secret_symlinks_even_when_dangling(self):
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nshared_hidden_paths = [".secrets"]\n')
        for target in (self.root.parent / 'absent', self.root / '.git'):
            with self.subTest(target=target):
                link = self.root / '.secrets'
                link.symlink_to(target)
                try:
                    with self.assertRaisesRegex(ValueError, 'must not follow a symlink'):
                        settings.generate(self.root, {})
                finally:
                    link.unlink()
        (self.root / 'private').symlink_to(self.root.parent)
        (self.root / '.devcontainer/claude.local.toml').write_text(
            '[sumi]\nsecrets = "../private/absent"\n')
        with self.assertRaisesRegex(ValueError, 'must not follow a symlink'):
            settings.generate(self.root, {})

    def test_clone_has_no_masks_even_if_secret_is_a_symlink(self):
        (self.root / '.secrets').symlink_to(self.root.parent)
        (self.root / '.devcontainer/claude.local.toml').write_text(
            '[git]\nisolation = "clone"\nshared_hidden_paths = [".secrets"]\n')
        self.assertEqual(self.generated_mounts(), {'/workspace': {'type': 'volume', 'source': 'workspace',
                                                               'target': '/workspace'}})

    def test_mask_paths_escape_compose_interpolation(self):
        (self.root / '$TOKEN').write_text('hidden')
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nshared_hidden_paths = ["$TOKEN"]\n')
        self.assertIn('/workspace/$$TOKEN', self.generated_mounts())

    def test_sumi_host_alias_into_workspace_is_hidden(self):
        (self.root / 'secret').write_text('hidden')
        alias = self.root.parent / 'sumi-alias'
        alias.symlink_to(self.root / 'secret')
        (self.root / '.devcontainer/claude.local.toml').write_text(f'[sumi]\nsecrets = "{alias}"\n')
        self.assertIn('/workspace/secret', self.generated_mounts())

    def test_no_implicit_secret_paths_and_local_list_can_be_cleared(self):
        (self.root / '.secrets').mkdir()
        self.assertNotIn('/workspace/.secrets', self.generated_mounts())
        (self.root / '.devcontainer/claude.toml').write_text(
            '[git]\nisolation = "shared"\nshared_hidden_paths = [".secrets"]\n')
        self.assertIn('/workspace/.secrets', self.generated_mounts())
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nshared_hidden_paths = []\n')
        self.assertNotIn('/workspace/.secrets', self.generated_mounts())

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
