#!/usr/bin/env python3
"""Discover dependency mounts; prepare only their ancestor directories as root."""
import errno
import os
from pathlib import Path
import pwd
import re
import sys
import subprocess

# The modes that mount the host's working tree as /workspace.
SHARED_MODES = ('shared', 'worktree')
# Where worktree mode shows the agent the host's Git, read-only.
HOST_GIT = Path('/run/host/git')
MOUNTINFO = Path('/proc/self/mountinfo')


def mount_points(mountinfo=MOUNTINFO):
    """{target: (root within the source, filesystem type)} of this mount namespace."""
    points = {}
    for line in Path(mountinfo).read_text().splitlines():
        fields = line.split()
        target = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        points[target] = (fields[3], line.split(' - ', 1)[1].split()[0])
    return points


def dependency_mounts(workspace, mountinfo=MOUNTINFO, overlay_only=False):
    """Actual node_modules mounts, never ordinary directories or their contents."""
    mounts = []
    for target, (root, kind) in mount_points(mountinfo).items():
        if overlay_only and (kind != 'overlay' or root != '/'):
            continue
        if target.name == 'node_modules' and target.is_relative_to(workspace):
            mounts.append(target)
    return mounts


def prepare(workspace, uid, gid, isolation='clone', mountinfo=MOUNTINFO):
    if isolation not in ('clone', *SHARED_MODES):
        raise ValueError(f'Unknown git isolation: {isolation}')
    workspace = Path(workspace)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open(workspace, flags)
    def own(fd, path):
        status = os.fstat(fd)
        # A read-only mount refuses fchown even to its current owner.
        if (status.st_uid, status.st_gid) == (uid, gid):
            return
        try:
            os.fchown(fd, uid, gid)
        except OSError as error:
            if error.errno != errno.EROFS:
                raise
            raise ValueError(f'{path} is read-only and owned by {status.st_uid}:{status.st_gid}, not by the agent '
                             f'({uid}:{gid}); set agent.uid and agent.gid in claude.local.toml to its owner on the host.') from None
    try:
        if isolation == 'clone':
            own(root, workspace)
        if isolation == 'worktree':
            # Docker creates the agent's Git volume root-owned. Without that
            # mount, .git would be the host's, bound read-write beneath it.
            if Path(workspace / '.git') not in mount_points(mountinfo):
                raise ValueError(f'{workspace / ".git"} is not the agent-git volume; '
                                 'regenerate settings and recreate the container.')
            git = os.open('.git', flags, dir_fd=root)
            try:
                own(git, workspace / '.git')
            finally:
                os.close(git)
        for mount in dependency_mounts(workspace, mountinfo, overlay_only=isolation in SHARED_MODES):
            current = os.dup(root)
            path = workspace
            try:
                # Open each component relative to a held directory descriptor.
                # Never follow an agent-controlled symlink or walk its contents.
                for part in mount.relative_to(workspace).parts:
                    child = os.open(part, flags, dir_fd=current)
                    os.close(current)
                    current = child
                    path /= part
                    if isolation == 'clone' or path == mount:
                        own(current, path)
            finally:
                os.close(current)
    finally:
        os.close(root)


def check_shared(workspace, isolation='shared', mountinfo=MOUNTINFO):
    """As node, before Git runs: the host's tree is usable and its protections are in place."""
    workspace = Path(workspace)
    if not os.access(workspace, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError(f'{workspace} is not writable to {os.getuid()}:{os.getgid()}; '
                         'set agent.uid and agent.gid in claude.local.toml to the host owner.')
    host_git = HOST_GIT if isolation == 'worktree' else workspace / '.git'
    for path in (workspace / '.devcontainer', host_git):
        if not os.statvfs(path).f_flag & os.ST_RDONLY or os.access(path, os.W_OK):
            raise ValueError(f'{path} must be mounted read-only in {isolation} mode; regenerate settings and recreate the container.')
    paths = [workspace / '.devcontainer', host_git, host_git / 'objects', host_git / 'refs']
    paths += [host_git / name for name in ('HEAD', 'config', 'index') if (host_git / name).exists()]
    for path in paths:
        access = os.R_OK | (os.X_OK if path.is_dir() else 0)
        if not os.access(path, access):
            raise ValueError(f'{path} is not accessible to {os.getuid()}:{os.getgid()}; '
                             'set agent.uid and agent.gid in claude.local.toml to the host owner.')
    if isolation == 'worktree':
        git = workspace / '.git'
        if (git not in mount_points(mountinfo) or os.statvfs(git).f_flag & os.ST_RDONLY
                or not os.access(git, os.R_OK | os.W_OK | os.X_OK)):
            raise ValueError(f'{git} must be the writable agent-git volume in worktree mode; '
                             'check agent.uid and agent.gid, regenerate settings and recreate the container.')
        # Git is usable once clone-workspace.py has initialized the volume.
        return
    result = subprocess.run(['git', '-C', str(workspace), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != str(workspace):
        raise ValueError(f'shared Git is unavailable: {result.stderr.strip()}; check agent.uid and agent.gid.')


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--check-shared']:
            check_shared('/workspace', os.environ.get('CLAUDE_DEVCONTAINER_GIT_ISOLATION', 'shared'))
        else:
            if os.geteuid() != 0:
                sys.exit('error: mount preparation is an entrypoint-only root operation.')
            node = pwd.getpwnam('node')
            prepare('/workspace', node.pw_uid, node.pw_gid, os.environ.get('CLAUDE_DEVCONTAINER_GIT_ISOLATION', 'clone'))
    except ValueError as error:
        sys.exit(f'error: {error}')
