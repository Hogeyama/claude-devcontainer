"""Shared repository validation and ownership boundaries, without a Docker daemon."""
import importlib.util
import json
import os
import shutil
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

    def test_shared_rejects_symlink_dependency_ancestors(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'packages').symlink_to(outside)
        with patch.object(mounts, 'dependency_mounts', return_value=[self.root / 'packages/node_modules']), \
                patch.object(mounts.os, 'fchown') as chown:
            with self.assertRaises(OSError):
                mounts.prepare(self.root, os.getuid(), os.getgid(), 'shared', self.mountinfo(self.root / '.git'))
        chown.assert_not_called()

    def test_shared_access_error_explains_uid_configuration(self):
        with patch.object(mounts.os, 'access', return_value=False):
            with self.assertRaisesRegex(ValueError, 'agent.uid and agent.gid'):
                mounts.check_shared(self.root)

    def generated_mounts(self):
        settings.generate(self.root, {})
        data = json.loads((self.root / '.devcontainer/.generated/workspace.compose.yaml').read_text())
        return {m['target']: m for m in data['services']['agent']['volumes']}

    def use_shared(self):
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nisolation = "shared"\n')

    def use_unsafe_shared(self):
        (self.root / '.devcontainer/claude.local.toml').write_text(
            '[git]\nisolation = "unsafe_shared"\n[acp]\nenabled = true\n')

    def test_unsafe_shared_preserves_git_and_protects_configuration(self):
        self.use_unsafe_shared()
        self.git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '--allow-empty', '-qm', 'fixture')
        (self.root / 'staged').write_text('pending')
        self.git('add', 'staged')
        before = {name: (self.root / '.git' / name).read_bytes() for name in ('HEAD', 'index', 'config')}
        generated = self.generated_mounts()
        self.assertEqual(generated['/workspace/.git']['source'], str(self.root / '.git'))
        self.assertEqual(generated['/workspace/.git/objects']['source'], str(self.root / '.git/objects'))
        self.assertNotIn('/run/host/git', generated)
        for name in ('hooks', 'info', 'objects/info', 'config', 'config.worktree'):
            self.assertTrue(generated['/workspace/.git/' + name]['read_only'])
        self.assertTrue(generated['/workspace/.devcontainer']['read_only'])
        self.assertEqual(before, {name: (self.root / '.git' / name).read_bytes() for name in before})
        self.assertEqual(self.git('diff', '--cached', '--name-only').stdout.strip(), 'staged')
        self.generated_mounts()  # Idempotent, including reserved config.worktree.

    def test_unsafe_shared_rejects_unprotected_hooks_and_config(self):
        self.use_unsafe_shared()
        for key, value in (('core.hooksPath', 'scripts'), ('include.path', '../other-config')):
            self.git('config', key, value)
            with self.assertRaisesRegex(ValueError, 'does not support'):
                self.generated_mounts()
            self.git('config', '--unset', key)

    def test_unsafe_shared_never_chowns_host_git(self):
        self.use_unsafe_shared()
        with patch.object(mounts.os, 'fchown') as chown:
            mounts.prepare(self.root, os.getuid() + 1, os.getgid(), 'unsafe_shared', self.mountinfo())
        chown.assert_not_called()

    def test_unsafe_shared_checks_every_protected_mount(self):
        self.use_unsafe_shared()
        self.generated_mounts()
        protected = {self.root / '.devcontainer'}
        protected.update(self.root / '.git' / name for name in ('hooks', 'info', 'objects/info', 'config', 'config.worktree'))
        def access(path, mode):
            return not (Path(path) in protected and mode & os.W_OK)
        def statvfs(path):
            return SimpleNamespace(f_flag=os.ST_RDONLY if Path(path) in protected else 0)
        with patch.object(mounts.os, 'access', side_effect=access), patch.object(mounts.os, 'statvfs', side_effect=statvfs):
            mounts.check_shared(self.root, 'unsafe_shared')
            protected.remove(self.root / '.git/config')
            with self.assertRaisesRegex(ValueError, 'must be mounted read-only'):
                mounts.check_shared(self.root, 'unsafe_shared')

    def test_shared_gives_the_agent_its_own_git_volume(self):
        self.use_shared()
        generated = self.generated_mounts()
        self.assertEqual(generated['/workspace'], {'type': 'bind', 'source': str(self.root), 'target': '/workspace',
                                                   'bind': {'create_host_path': False}})
        self.assertEqual(generated['/workspace/.git'], {'type': 'volume', 'source': 'agent-git',
                                                        'target': '/workspace/.git', 'volume': {'nocopy': True}})
        self.assertEqual(generated['/run/host/git'], {'type': 'bind', 'source': str(self.root / '.git'),
                                                      'target': '/run/host/git', 'read_only': True,
                                                      'bind': {'create_host_path': False}})
        self.assertTrue(generated['/workspace/.devcontainer']['read_only'])
        self.assertTrue((self.root / '.devcontainer/.generated/empty-secret').is_file())

    def test_shared_rejects_the_same_git_layouts_as_shared(self):
        self.use_shared()
        (self.root / '.git/objects/info/alternates').write_text('/outside/objects\n')
        with self.assertRaisesRegex(ValueError, 'git.isolation = "shared".*Unsupported Git storage'):
            settings.generate(self.root, {})

    def test_shared_writable_is_no_longer_a_setting(self):
        (self.root / '.devcontainer/claude.local.toml').write_text('[git]\nshared_writable = true\n')
        with self.assertRaisesRegex(ValueError, 'git.shared_writable: unknown setting'):
            settings.generate(self.root, {})
        (self.root / '.devcontainer/claude.local.toml').unlink()
        self.assertNotIn('CLAUDE_DEVCONTAINER_GIT_SHARED_WRITABLE', settings.variables(settings.load(self.root)))

    def mountinfo(self, *targets, overlay=()):
        """A mount table with these targets, as volumes unless listed in overlay."""
        info = self.root.parent / 'mountinfo'
        info.write_text(''.join(
            f'{n} 0 0:{n} / {target} rw - ' + ('overlay overlay rw\n' if target in overlay else 'ext4 /dev/sda rw\n')
            for n, target in enumerate(targets, 1)))
        return info

    def test_shared_prepare_owns_only_the_git_volume_and_dependency_roots(self):
        dependency = self.root / 'packages/app/node_modules'
        dependency.mkdir(parents=True)
        info = self.mountinfo(self.root / '.git', dependency, overlay=(dependency,))
        changed = []
        def record(fd, uid, gid):
            changed.append(os.fstat(fd).st_ino)
        with patch.object(mounts.os, 'fchown', record):
            mounts.prepare(self.root, os.getuid() + 1, os.getgid(), 'shared', info)
        self.assertEqual(changed, [(self.root / '.git').stat().st_ino, dependency.stat().st_ino])

    def test_shared_prepare_refuses_to_own_the_host_git(self):
        info = self.mountinfo(self.root / 'elsewhere')
        with patch.object(mounts.os, 'fchown') as chown:
            with self.assertRaisesRegex(ValueError, 'not the agent-git volume'):
                mounts.prepare(self.root, os.getuid() + 1, os.getgid(), 'shared', info)
        chown.assert_not_called()

    def test_shared_startup_checks_host_git_read_only_and_agent_git_writable(self):
        host_git = self.root.parent / 'host-git'
        shutil.copytree(self.root / '.git', host_git)
        read_only = {self.root / '.devcontainer', host_git}
        def access(path, mode):
            return not (Path(path) in read_only and mode & os.W_OK)
        def statvfs(path):
            return SimpleNamespace(f_flag=os.ST_RDONLY if Path(path) in read_only else 0)
        info = self.mountinfo(self.root / '.git')
        with patch.object(mounts, 'HOST_GIT', host_git), patch.object(mounts.os, 'access', side_effect=access), \
                patch.object(mounts.os, 'statvfs', side_effect=statvfs):
            mounts.check_shared(self.root, 'shared', info)
            with self.assertRaisesRegex(ValueError, 'agent-git volume'):
                mounts.check_shared(self.root, 'shared', self.mountinfo())
            read_only.add(self.root / '.git')
            with self.assertRaisesRegex(ValueError, 'agent-git volume'):
                mounts.check_shared(self.root, 'shared', info)
            read_only.remove(host_git)
            with self.assertRaisesRegex(ValueError, 'must be mounted read-only in shared mode'):
                mounts.check_shared(self.root, 'shared', info)

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
        self.assertEqual((self.root / '.devcontainer/.generated/hidden.txt').read_text(),
                         '.env\nvault\nprivate/sumi.txt\n')

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
