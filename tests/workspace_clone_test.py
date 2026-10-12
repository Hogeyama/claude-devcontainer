import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / '.devcontainer/claude/agent'))
mounts = load('workspace_mounts', '.devcontainer/claude/agent/workspace_mounts.py')
source = load('workspace_source', '.devcontainer/claude/scripts/workspace-source.py')
clone = load('clone_workspace', '.devcontainer/claude/agent/clone-workspace.py')


class WorkspaceCloneTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host, self.agent = [self.root / n for n in ('host', 'agent')]
        config = self.root / 'gitconfig'
        config.write_text('[user]\n name = fixture\n email = fixture@example.com\n'
                          '[maintenance]\n auto = false\n')
        environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.host.mkdir()
        self.git(self.host, 'init', '-b', 'main')
        (self.host / 'package.json').write_text('{"scripts":{"test":"echo safe"}}\n')
        self.commit('initial')
        self.start = self.git(self.host, 'rev-parse', 'HEAD')
        self.metadata = self.host / '.devcontainer/.generated/workspace.json'
        source.write(self.host)

    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git(self.host, 'add', 'package.json')
        self.git(self.host, 'commit', '-m', message)

    def test_first_start_uses_exact_host_commit(self):
        (self.host / 'package.json').write_text('later\n')
        self.commit('remote advances after metadata was written')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.agent, 'remote'), '')
        self.assertFalse((self.agent / '.git/claude-bootstrap.json').exists())

    def test_completed_clone_survives_new_metadata(self):
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'switch', '-c', 'agent/task')
        (self.agent / 'package.json').write_text('unfinished\n')
        (self.agent / 'untracked').write_text('keep\n')
        self.metadata.write_text('{}')  # Must not be read on an ordinary restart.
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), 'agent/task')
        self.assertEqual((self.agent / 'package.json').read_text(), 'unfinished\n')
        self.assertEqual((self.agent / 'untracked').read_text(), 'keep\n')

    def test_unpublished_commit_clones_without_remote(self):
        (self.host / 'package.json').write_text('not yet pushed\n')
        self.commit('local only')
        source.write(self.host)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'),
                         self.git(self.host, 'rev-parse', 'HEAD'))

    def test_nonempty_workspace_is_preserved(self):
        self.agent.mkdir()
        (self.agent / 'keep').write_text('data')
        with self.assertRaises(ValueError):
            clone.clone(self.metadata, self.agent)
        self.assertEqual((self.agent / 'keep').read_text(), 'data')

    def test_index_flags_do_not_cross_remote_and_review_id_is_stable(self):
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'switch', '-c', 'agent/task')
        (self.agent / 'package.json').write_text('changed\n')
        self.git(self.agent, 'add', 'package.json')
        self.git(self.agent, 'commit', '-m', 'candidate')
        self.git(self.agent, 'update-index', '--assume-unchanged', 'package.json')
        (self.agent / 'package.json').write_text('concealed uncommitted change\n')
        self.assertEqual(self.git(self.agent, 'diff'), '')
        self.git(self.host, 'fetch', '--no-tags', str(self.agent), 'refs/heads/agent/task')
        candidate = self.git(self.host, 'rev-parse', 'FETCH_HEAD')
        self.assertIn('+changed', self.git(self.host, 'diff', self.start, candidate))
        self.assertTrue(self.git(self.host, 'ls-files', '-v', 'package.json').startswith('H '))
        self.git(self.agent, 'update-index', '--no-assume-unchanged', 'package.json')
        self.git(self.agent, 'add', 'package.json')
        self.git(self.agent, 'commit', '-m', 'later')
        self.git(self.host, 'fetch', str(self.agent), 'refs/heads/agent/task')
        self.git(self.host, 'merge', '--ff-only', candidate)
        self.assertEqual(self.git(self.host, 'rev-parse', 'HEAD'), candidate)
        self.assertEqual((self.host / 'package.json').read_text(), 'changed\n')

    def test_mounts_allow_only_node_modules_and_ancestors(self):
        mount = self.agent / 'packages/app/node_modules'
        mount.mkdir(parents=True)
        (mount / 'dependency').write_text('keep')
        with patch.object(clone, 'dependency_mounts', return_value=[mount]):
            clone.clone(self.metadata, self.agent)
        self.assertEqual((mount / 'dependency').read_text(), 'keep')
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)

    def test_plain_node_modules_directory_is_not_a_mount(self):
        (self.agent / 'node_modules').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'not empty'):
            clone.clone(self.metadata, self.agent)

    def test_unrelated_files_beside_mount_are_refused(self):
        mount = self.agent / 'packages/app/node_modules'
        mount.mkdir(parents=True)
        (mount.parent / 'keep').write_text('keep')
        with patch.object(clone, 'dependency_mounts', return_value=[mount]):
            with self.assertRaisesRegex(ValueError, 'not empty'):
                clone.clone(self.metadata, self.agent)

    def test_tracked_mount_path_and_ancestor_symlink_are_refused(self):
        for tracked in ('packages/app/node_modules/file', 'packages'):
            with self.subTest(tracked=tracked):
                self.git(self.host, 'reset', '--hard', self.start)
                import shutil
                shutil.rmtree(self.host / 'packages', ignore_errors=True)
                path = self.host / tracked
                path.parent.mkdir(parents=True, exist_ok=True)
                if tracked == 'packages':
                    path.symlink_to('elsewhere')
                else:
                    path.write_text('tracked')
                self.git(self.host, 'add', 'packages')
                self.git(self.host, 'commit', '-m', 'conflict')
                source.write(self.host)
                agent = self.root / ('agent-' + str(len(tracked)))
                mount = agent / 'packages/app/node_modules'
                mount.mkdir(parents=True)
                with patch.object(clone, 'dependency_mounts', return_value=[mount]):
                    with self.assertRaisesRegex(ValueError, 'mount'):
                        clone.clone(self.metadata, agent)
                self.assertTrue(mount.is_dir())

    def test_missing_seed_can_resume_with_original_inputs(self):
        data = json.loads(self.metadata.read_text())
        seed = self.metadata.parent / data['seed']
        saved = seed.with_suffix('.saved')
        seed.rename(saved)
        with self.assertRaisesRegex(ValueError, 'local Git snapshot'):
            clone.clone(self.metadata, self.agent)
        self.metadata.write_text('{}')
        saved.rename(seed)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)

    def test_local_snapshot_is_independent_and_excludes_host_git_settings(self):
        self.git(self.host, 'config', 'credential.helper', 'secret-helper')
        self.git(self.host, 'config', 'core.hooksPath', '/host/private-hooks')
        self.git(self.host, 'update-index', '--assume-unchanged', 'package.json')
        (self.host / '.git/hooks/post-checkout').write_text('private-hook')
        # Regenerate at a different committed HEAD, with dirty files kept local.
        self.git(self.host, 'commit', '--allow-empty', '-m', 'not pushed')
        (self.host / 'package.json').write_text('dirty host work')
        (self.host / 'untracked').write_text('private host file')
        data = source.write(self.host)
        seed = self.metadata.parent / data['seed']
        self.assertFalse((seed / 'objects/info/alternates').exists())
        self.assertNotIn('secret-helper', (seed / 'config').read_text())
        self.assertNotIn('/host/', (seed / 'config').read_text())
        self.assertFalse((seed / 'hooks/post-checkout').exists())
        seed_inodes = {(p.stat().st_dev, p.stat().st_ino) for p in (seed / 'objects').rglob('*') if p.is_file()}
        host_inodes = {(p.stat().st_dev, p.stat().st_ino) for p in (self.host / '.git/objects').rglob('*') if p.is_file()}
        self.assertFalse(seed_inodes & host_inodes)
        (self.host / '.git').rename(self.host / 'git-unavailable')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), data['commit'])
        self.assertEqual((self.agent / 'package.json').read_text(), '{"scripts":{"test":"echo safe"}}\n')
        self.assertFalse((self.agent / 'untracked').exists())
        self.assertTrue(self.git(self.agent, 'ls-files', '-v', 'package.json').startswith('H '))
        self.assertFalse((self.agent / '.git/objects/info/alternates').exists())

    def test_linked_worktree_with_unpublished_detached_head(self):
        worktree = self.root / 'worktree'
        self.git(self.host, 'worktree', 'add', '--detach', str(worktree), 'HEAD')
        self.git(worktree, 'commit', '--allow-empty', '-m', 'worktree only')
        data = source.write(worktree)
        clone.clone(worktree / '.devcontainer/.generated/workspace.json', self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), data['commit'])

    def test_shallow_host_clone_can_initialize_without_remote(self):
        self.git(self.host, 'commit', '--allow-empty', '-m', 'second commit')
        shallow = self.root / 'shallow'
        self.git(self.host, 'clone', '--depth=1', self.host.as_uri(), str(shallow))
        self.assertEqual(self.git(shallow, 'rev-parse', '--is-shallow-repository'), 'true')
        data = source.write(shallow)
        clone.clone(shallow / '.devcontainer/.generated/workspace.json', self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), data['commit'])
        self.git(self.agent, 'fsck', '--no-dangling')

    def test_interrupted_init_and_checkout_conflict_preserve_files(self):
        marker = self.agent / '.git/claude-bootstrap.json'
        marker.parent.mkdir(parents=True)
        marker.write_text(self.metadata.read_text())
        (self.agent / 'package.json').write_text('my work')
        with self.assertRaises(subprocess.CalledProcessError):
            clone.clone(self.metadata, self.agent)
        self.assertEqual((self.agent / 'package.json').read_text(), 'my work')
        self.assertTrue(marker.exists())
        (self.agent / 'package.json').unlink()
        clone.clone(self.metadata, self.agent)
        self.assertFalse(marker.exists())

    def test_incomplete_repository_without_marker_is_refused(self):
        self.agent.mkdir()
        self.git(self.agent, 'init')
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            clone.clone(self.metadata, self.agent)

    def test_metadata_excludes_remotes_and_rejects_invalid_values(self):
        self.git(self.host, 'remote', 'add', 'origin', 'https://user:secret@github.com/example/repo.git')
        source.write(self.host)
        self.assertNotIn('secret', self.metadata.read_text())
        for data in ({}, {'commit': 'HEAD', 'seed': 'bad'},
                     {'commit': self.start, 'seed': 'workspace-' + self.start + '.git', 'url': 'https://github.com/o/r'}):
            self.metadata.write_text(json.dumps(data))
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError)):
                clone.clone(self.metadata, self.agent)
        self.assertFalse((self.agent / '.git').exists())

    def test_seed_path_cannot_escape_generated_directory(self):
        data = json.loads(self.metadata.read_text())
        for seed in ('../host/.git', '/tmp/repo.git', 'workspace-wrong.git'):
            data['seed'] = seed
            self.metadata.write_text(json.dumps(data))
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                clone.clone(self.metadata, self.agent)
        self.assertFalse((self.agent / '.git').exists())

    def test_host_remotes_do_not_cross_into_container(self):
        self.git(self.host, 'remote', 'add', 'origin', 'https://github.com/upstream/project.git')
        self.git(self.host, 'remote', 'add', 'agent', 'ext::host-only-transport')
        source.write(self.host)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'remote'), '')
        self.assertEqual(self.git(self.host, 'config', '--get', 'remote.origin.url'),
                         'https://github.com/upstream/project.git')

    def test_unborn_head_does_not_publish_metadata(self):
        empty = self.root / 'empty'
        empty.mkdir()
        self.git(empty, 'init')
        with self.assertRaises(ValueError):
            source.write(empty)
        self.assertFalse((empty / '.devcontainer/.generated/workspace.json').exists())

    def test_mountinfo_decoding(self):
        info = self.root / 'mountinfo'
        info.write_text('1 0 0:1 / /workspace/node_modules rw - overlay overlay rw\n'
                        '2 0 0:2 / /workspace/packages/a\\040b/node_modules rw - overlay overlay rw\n'
                        '3 0 0:3 / /elsewhere/node_modules rw - overlay overlay rw\n')
        self.assertEqual(clone.dependency_mounts(Path('/workspace'), info),
                         [Path('/workspace/node_modules'), Path('/workspace/packages/a b/node_modules')])

    def test_sha256_repository_uses_matching_object_format(self):
        host = self.root / 'sha256-host'
        host.mkdir()
        self.git(host, 'init', '--object-format=sha256')
        self.git(host, 'commit', '--allow-empty', '-m', 'sha256')
        data = source.write(host)
        self.assertEqual(len(data['commit']), 64)
        clone.clone(host / '.devcontainer/.generated/workspace.json', self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), data['commit'])

    def test_prepare_refuses_symlink_ancestors(self):
        self.agent.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        outside.chmod(0o500)
        (self.agent / 'packages').symlink_to(outside)
        with patch.object(mounts, 'dependency_mounts', return_value=[self.agent / 'packages/app/node_modules']):
            with self.assertRaises(OSError):
                mounts.prepare(self.agent, os.getuid(), os.getgid())
        self.assertEqual(outside.stat().st_mode & 0o777, 0o500)
