import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
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
            # Not the user's own claude.toml, whose proxy.hosts decide the remotes.
            'XDG_CONFIG_HOME': str(self.root / 'config'),
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

    def test_host_mode_writes_the_snapshot_to_the_state_directory(self):
        state = self.root / 'state'
        with patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_CONFIG_DIR': str(state)}):
            data = source.write(self.host)
        self.assertTrue((state / '.generated/workspace.json').is_file())
        self.assertTrue((state / '.generated' / data['seed']).is_dir())

    def test_first_start_uses_exact_host_commit(self):
        (self.host / 'package.json').write_text('later\n')
        self.commit('host advances after metadata was written')
        self.git(self.host, 'switch', '-c', 'later-host-branch')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), 'main')
        self.assertEqual(self.git(self.agent, 'remote'), '')
        self.assertFalse((self.agent / '.git/claude-bootstrap.json').exists())

    def test_first_start_copies_host_branch_name(self):
        self.git(self.host, 'switch', '-c', 'feature/local-work')
        self.git(self.host, 'tag', 'feature/local-work')
        source.write(self.host)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), 'feature/local-work')
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)

    def test_retry_does_not_reset_an_advanced_starting_branch(self):
        clone.clone(self.metadata, self.agent)
        marker = self.agent / '.git/claude-bootstrap.json'
        marker.write_text(self.metadata.read_text())
        self.git(self.agent, 'commit', '--allow-empty', '-m', 'agent work')
        agent_commit = self.git(self.agent, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'branch changed'):
            clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), agent_commit)

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

    def test_only_the_current_snapshot_is_kept(self):
        old = json.loads(self.metadata.read_text())['seed']
        self.git(self.host, 'commit', '--allow-empty', '-m', 'second commit')
        data = source.write(self.host)
        self.assertEqual(sorted(p.name for p in self.metadata.parent.glob('workspace-*.git')), [data['seed']])
        self.assertNotEqual(old, data['seed'])

    def test_retry_whose_snapshot_was_replaced_starts_from_the_current_one(self):
        # Interrupted before the fetch; the host has moved on since.
        marker = self.agent / '.git/claude-bootstrap.json'
        marker.parent.mkdir(parents=True)
        marker.write_text(self.metadata.read_text())
        self.git(self.host, 'commit', '--allow-empty', '-m', 'second commit')
        data = source.write(self.host)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), data['commit'])
        self.assertFalse(marker.exists())

    def test_retry_after_the_fetch_needs_no_snapshot(self):
        clone.clone(self.metadata, self.agent)
        marker = self.agent / '.git/claude-bootstrap.json'
        marker.write_text(self.metadata.read_text())
        self.git(self.host, 'commit', '--allow-empty', '-m', 'second commit')
        source.write(self.host)
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)
        self.assertFalse(marker.exists())

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
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), '')

    def test_retry_after_branch_checkout_preserves_uncommitted_work(self):
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.git(self.agent, 'update-ref', 'refs/heads/main', self.start)
        # Simulate a crash after checkout but before removing the marker.
        marker = self.agent / '.git/claude-bootstrap.json'
        marker.write_text(self.metadata.read_text())
        (self.agent / 'package.json').write_text('unfinished work')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), 'main')
        self.assertEqual((self.agent / 'package.json').read_text(), 'unfinished work')
        self.assertFalse(marker.exists())

    def test_invalid_branch_metadata_is_rejected_before_initialization(self):
        data = json.loads(self.metadata.read_text())
        for branch in ('--detach', '@{-1}', 'bad..name', 'HEAD', 'refs/../bad'):
            data['branch'] = branch
            self.metadata.write_text(json.dumps(data))
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                clone.clone(self.metadata, self.agent)
        self.assertFalse((self.agent / '.git').exists())

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

    def test_remotes_the_proxy_carries_are_inherited_for_fetch(self):
        for name, url in [('origin', 'git@github.com:upstream/project.git'),
                          ('fork', 'https://user:token@GitHub.com/me/project.git'),
                          ('mirror', 'ssh://git@github.com/upstream/mirror.git'),
                          ('other', 'https://gitlab.com/upstream/project.git'),
                          ('port', 'https://github.com:8443/upstream/project.git'),
                          ('local', '/srv/project.git'), ('agent', 'ext::host-only-transport'),
                          ('host', 'https://github.com/upstream/host.git')]:
            self.git(self.host, 'remote', 'add', name, url)
        source.write(self.host, {'github.com'})
        remotes = self.metadata.parent / 'remotes.json'
        self.assertEqual(json.loads(remotes.read_text()), {
            'fork': 'https://github.com/me/project.git', 'mirror': 'https://github.com/upstream/mirror.git',
            'origin': 'https://github.com/upstream/project.git'})
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'remote', 'add', 'fork', 'https://github.com/agent/kept.git')
        clone.add_remotes(self.agent, remotes)
        self.assertEqual(self.git(self.agent, 'remote', 'get-url', 'origin'), 'https://github.com/upstream/project.git')
        self.assertEqual(self.git(self.agent, 'remote', 'get-url', 'fork'), 'https://github.com/agent/kept.git')
        self.assertEqual(sorted(self.git(self.agent, 'remote').split()), ['fork', 'mirror', 'origin'])
        # Without the host's file, as from an older host, nothing changes.
        clone.add_remotes(self.agent, self.root / 'absent.json')
        for data in ({'host': 'https://github.com/o/r.git'}, {'origin': 'https://u:p@github.com/o/r.git'},
                     {'origin': 'ext::sh -c id'}, {'-x': 'https://github.com/o/r.git'}, ['origin']):
            remotes.write_text(json.dumps(data))
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'Invalid host remotes'):
                clone.add_remotes(self.agent, remotes)

    def test_remotes_are_not_inherited_without_their_host_or_the_setting(self):
        self.git(self.host, 'remote', 'add', 'origin', 'https://github.com/upstream/project.git')
        source.write(self.host)
        self.assertEqual(json.loads((self.metadata.parent / 'remotes.json').read_text()), {})
        (self.host / '.devcontainer/claude.toml').write_text(
            '[git]\ninherit_remotes = false\n[proxy.hosts."github.com"]\nroutes = ["/"]\n')
        self.assertEqual(source.proxy_hosts(self.host), set())
        (self.host / '.devcontainer/claude.toml').write_text('[proxy.hosts."github.com"]\nroutes = ["/"]\n')
        self.assertEqual(source.proxy_hosts(self.host), {'github.com'})

    def add_submodule(self, url):
        """A submodule lib at a commit that is not its repository's tip, with url in .gitmodules."""
        sub = self.root / 'sub'
        self.git(self.root, 'init', '-q', '-b', 'main', str(sub))
        (sub / 'file').write_text('recorded\n')
        self.git(sub, 'add', 'file')
        self.git(sub, 'commit', '-qm', 'recorded')
        self.git(self.host, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(sub), 'lib')
        recorded = self.git(self.host, 'rev-parse', ':lib')
        self.git(self.host, 'config', '-f', '.gitmodules', 'submodule.lib.url', url)
        self.git(self.host, 'add', '.gitmodules')
        self.git(self.host, 'commit', '-qm', 'submodule')
        (sub / 'file').write_text('later\n')
        self.git(sub, 'commit', '-qam', 'later')
        self.git(self.host / 'lib', '-c', 'protocol.file.allow=always', 'fetch', '-q', 'origin')
        return recorded

    def test_submodules_are_checked_out_from_the_host(self):
        recorded = self.add_submodule('git@github.com:upstream/lib.git')
        source.write(self.host, {'github.com'})
        modules = json.loads((self.metadata.parent / 'submodules.json').read_text())
        self.assertEqual([(m['name'], m['path'], m['commit'], m['url']) for m in modules],
                         [('lib', 'lib', recorded, 'https://github.com/upstream/lib.git')])
        clone.clone(self.metadata, self.agent)
        clone.init_submodules(self.agent, self.metadata.parent / 'submodules.json')
        lib = self.agent / 'lib'
        self.assertEqual((lib / 'file').read_text(), 'recorded\n')
        self.assertEqual(self.git(lib, 'rev-parse', 'HEAD'), recorded)
        self.assertEqual(self.git(self.agent, 'status', '--porcelain'), '')
        self.assertEqual(self.git(lib, 'remote', 'get-url', 'origin'), 'https://github.com/upstream/lib.git')
        self.assertEqual(self.git(self.agent, 'config', 'submodule.lib.url'), 'https://github.com/upstream/lib.git')
        self.assertEqual(self.git(lib, 'for-each-ref', '--format=%(refname)'), '')
        # Once checked out, a later start leaves it alone.
        (lib / 'file').write_text('agent work\n')
        clone.init_submodules(self.agent, self.metadata.parent / 'submodules.json')
        self.assertEqual((lib / 'file').read_text(), 'agent work\n')

    def test_submodules_without_a_host_checkout_or_with_a_moved_gitlink_stay_empty(self):
        self.add_submodule('https://github.com/upstream/lib.git')
        # The agent's HEAD records another commit than the host copied.
        source.write(self.host, set())
        modules = self.metadata.parent / 'submodules.json'
        self.assertIsNone(json.loads(modules.read_text())[0]['url'], 'a host outside proxy.hosts')
        clone.clone(self.metadata, self.agent)
        data = json.loads(modules.read_text())
        data[0]['commit'] = '0' * 40
        data[0]['seed'] = f"submodule-{'0' * 16}-{'0' * 40}.git"
        modules.write_text(json.dumps(data))
        clone.init_submodules(self.agent, modules)
        self.assertEqual(list((self.agent / 'lib').iterdir()), [])
        # A host that has not checked it out has nothing to copy.
        shutil.rmtree(self.host / 'lib')
        (self.host / 'lib').mkdir()
        source.write(self.host, set())
        self.assertEqual(json.loads(modules.read_text()), [])
        self.assertEqual(list(self.metadata.parent.glob('submodule-*.git')), [], 'old snapshots are removed')

    def test_invalid_submodules_metadata_is_refused(self):
        clone.clone(self.metadata, self.agent)
        modules = self.root / 'submodules.json'
        entry = {'name': 'lib', 'path': 'lib', 'commit': self.start,
                 'seed': f'submodule-{"0" * 16}-{self.start}.git', 'url': None}
        for change in ({'path': '../outside'}, {'path': '/abs'}, {'seed': '../x.git'}, {'url': 'ext::sh'},
                       {'commit': 'HEAD'}, {'extra': 1}):
            modules.write_text(json.dumps([dict(entry, **change)]))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Invalid host submodules'):
                clone.init_submodules(self.agent, modules)

    def test_host_remote_names_the_read_only_host_git_once(self):
        clone.clone(self.metadata, self.agent)
        clone.add_host_remote(self.agent, self.root / 'absent')
        self.assertEqual(self.git(self.agent, 'remote'), '')
        clone.add_host_remote(self.agent, self.host / '.git')
        self.assertEqual(self.git(self.agent, 'remote', 'get-url', 'host'), str(self.host / '.git'))
        (self.host / 'package.json').write_text('{}\n')
        self.commit('host change')
        self.git(self.agent, 'fetch', '-q', 'host')
        self.assertEqual(self.git(self.agent, 'rev-parse', 'host/main'), self.git(self.host, 'rev-parse', 'HEAD'))
        self.git(self.agent, 'remote', 'set-url', 'host', 'https://example.com/own.git')
        clone.add_host_remote(self.agent, self.host / '.git')
        self.assertEqual(self.git(self.agent, 'remote', 'get-url', 'host'), 'https://example.com/own.git')

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

    def read_only_mount(self):
        """A mount whose fchown fails with EROFS, as on a read-only bind."""
        mount = self.agent / 'packages/app/node_modules'
        mount.mkdir(parents=True)
        def fchown(fd, uid, gid):
            if os.path.samestat(os.fstat(fd), mount.stat()):
                raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        return mount, fchown

    def test_prepare_leaves_read_only_mount_of_the_agent(self):
        # The kernel refuses fchown on a read-only mount even to its current owner.
        mount, fchown = self.read_only_mount()
        with patch.object(mounts, 'dependency_mounts', return_value=[mount]), \
                patch.object(mounts.os, 'fchown', side_effect=fchown) as called:
            mounts.prepare(self.agent, os.getuid(), os.getgid())
        called.assert_not_called()

    def test_prepare_changes_other_owners(self):
        mount = self.agent / 'packages/app/node_modules'
        mount.mkdir(parents=True)
        with patch.object(mounts, 'dependency_mounts', return_value=[mount]), \
                patch.object(mounts.os, 'fchown') as called:
            mounts.prepare(self.agent, os.getuid() + 1, os.getgid())
        # The workspace, packages, app and node_modules.
        self.assertEqual([c.args[1:] for c in called.call_args_list], [(os.getuid() + 1, os.getgid())] * 4)

    def test_prepare_explains_read_only_mount_of_another_owner(self):
        mount, fchown = self.read_only_mount()
        with patch.object(mounts, 'dependency_mounts', return_value=[mount]), \
                patch.object(mounts.os, 'fchown', side_effect=fchown):
            with self.assertRaisesRegex(ValueError, f'{mount} is read-only.*agent.uid and agent.gid'):
                mounts.prepare(self.agent, os.getuid() + 1, os.getgid())

class WorktreeAttachTest(unittest.TestCase):
    """The agent's own Git under the host's working tree, built without writing its files."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = self.root / 'host'
        config = self.root / 'gitconfig'
        config.write_text('[user]\n name = fixture\n email = fixture@example.com\n'
                          '[maintenance]\n auto = false\n')
        environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.host.mkdir()
        self.git(self.host, 'init', '-b', 'main')
        (self.host / 'tracked').write_text('committed\n')
        self.git(self.host, 'add', 'tracked')
        self.git(self.host, 'commit', '-m', 'initial')
        self.start = self.git(self.host, 'rev-parse', 'HEAD')
        self.metadata = self.host / '.devcontainer/.generated/workspace.json'
        source.write(self.host)
        # As in the container: the host's files, with the agent's Git volume
        # over .git and the host's .git read-only elsewhere.
        self.workspace = self.root / 'workspace'
        shutil.copytree(self.host, self.workspace, ignore=shutil.ignore_patterns('.git', '.devcontainer'))
        (self.workspace / '.git').mkdir()
        self.host_git = self.host / '.git'
        (self.workspace / 'tracked').write_text('host dirty\n')
        (self.workspace / 'untracked').write_text('host untracked\n')

    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def attach(self):
        clone.attach(self.metadata, self.workspace, self.host_git)

    def files(self):
        return {p.name: p.read_text() for p in self.workspace.iterdir() if p.is_file()}

    def test_first_start_builds_head_and_index_without_touching_files(self):
        before = self.files()
        self.attach()
        self.assertEqual(self.files(), before)
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.workspace, 'branch', '--show-current'), 'main')
        self.assertEqual(self.git(self.workspace, 'diff', '--name-only'), 'tracked')
        self.assertEqual(self.git(self.workspace, 'ls-files', '--others'), 'untracked')
        self.assertEqual(self.git(self.workspace, 'rev-parse', '--show-toplevel'), str(self.workspace))
        self.assertFalse((self.workspace / '.git/claude-bootstrap.json').exists())
        self.assertFalse((self.workspace / '.git/objects/info/alternates').exists())

    def test_detached_host_head_is_followed(self):
        self.git(self.host, 'switch', '--detach')
        source.write(self.host)
        self.attach()
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.workspace, 'branch', '--show-current'), '')

    def test_interrupted_initialization_resumes(self):
        marker = self.workspace / '.git/claude-bootstrap.json'
        marker.write_text(self.metadata.read_text())
        self.metadata.write_text('{}')
        self.attach()
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), self.start)
        self.assertFalse(marker.exists())
        self.assertEqual((self.workspace / 'tracked').read_text(), 'host dirty\n')

    def test_restart_follows_host_commits_and_branch_when_nothing_is_pending(self):
        self.attach()
        (self.host / 'tracked').write_text('host dirty\n')
        self.git(self.host, 'switch', '-c', 'feature')
        self.git(self.host, 'commit', '-am', 'host commit')
        advanced = self.git(self.host, 'rev-parse', 'HEAD')
        (self.workspace / 'tracked').write_text('host dirty\n')
        self.attach()
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), advanced)
        self.assertEqual(self.git(self.workspace, 'branch', '--show-current'), 'feature')
        self.assertEqual(self.git(self.workspace, 'status', '--porcelain'), '?? untracked')
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'main'), self.start)

    def test_restart_keeps_unaccepted_agent_commits(self):
        self.attach()
        self.git(self.workspace, 'add', 'tracked')
        self.git(self.workspace, 'commit', '-m', 'agent work')
        pending = self.git(self.workspace, 'rev-parse', 'HEAD')
        self.git(self.host, 'commit', '--allow-empty', '-m', 'host moved on')
        with patch('sys.stderr') as stderr:
            self.attach()
        self.assertIn('not accepted yet', str(stderr.write.call_args_list))
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), pending)

    def test_restart_keeps_staged_changes(self):
        self.attach()
        self.git(self.workspace, 'add', 'tracked')
        self.git(self.host, 'commit', '--allow-empty', '-m', 'host moved on')
        with patch('sys.stderr') as stderr:
            self.attach()
        self.assertIn('staged changes', str(stderr.write.call_args_list))
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.workspace, 'diff', '--cached', '--name-only'), 'tracked')

    def test_agent_can_fetch_host_commits_from_the_read_only_git(self):
        self.attach()
        self.git(self.workspace, 'commit', '--allow-empty', '-m', 'agent work')
        self.git(self.host, 'commit', '--allow-empty', '-m', 'host work')
        self.git(self.workspace, 'fetch', str(self.host_git), 'main')
        self.assertEqual(self.git(self.workspace, 'rev-parse', 'FETCH_HEAD'), self.git(self.host, 'rev-parse', 'HEAD'))

    def test_incomplete_git_without_marker_is_refused(self):
        (self.workspace / '.git/HEAD').write_text('ref: refs/heads/main\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.attach()

    def test_masked_tracked_paths_are_skipped_in_status_and_add(self):
        (self.host / 'secret').write_text('real\n')
        (self.host / 'vault').mkdir()
        (self.host / 'vault/key').write_text('real\n')
        self.git(self.host, 'add', 'secret', 'vault')
        self.git(self.host, 'commit', '-m', 'tracked secrets')
        source.write(self.host)
        (self.metadata.parent / 'hidden.txt').write_text('secret\nvault\n')
        # As the container sees them: an empty file and an empty directory.
        (self.workspace / 'secret').write_text('')
        (self.workspace / 'vault').mkdir()
        self.attach()
        self.assertEqual(self.git(self.workspace, 'status', '--porcelain', '--', 'secret', 'vault'), '')
        self.assertEqual(sorted(self.git(self.workspace, 'ls-files', '-v', '--', 'secret', 'vault').splitlines()),
                         ['S secret', 'S vault/key'])
        result = subprocess.run(['git', '-C', str(self.workspace), 'add', 'secret'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.workspace, 'diff', '--cached', '--name-only'), '')
        # Still skipped after a restart that follows a host commit.
        self.git(self.host, 'commit', '--allow-empty', '-m', 'host moved on')
        self.attach()
        self.assertTrue(self.git(self.workspace, 'ls-files', '-v', '--', 'secret').startswith('S '))
