#!/usr/bin/env python3
"""Discover dependency mounts; prepare only their ancestor directories as root."""
import os
from pathlib import Path
import pwd
import re
import sys


def dependency_mounts(workspace, mountinfo=Path('/proc/self/mountinfo')):
    """Actual node_modules mounts, never ordinary directories or their contents."""
    mounts = []
    for line in Path(mountinfo).read_text().splitlines():
        target = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
        if target.name == 'node_modules' and target.is_relative_to(workspace):
            mounts.append(target)
    return mounts


def prepare(workspace, uid, gid):
    workspace = Path(workspace)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open(workspace, flags)
    def own(fd):
        os.fchown(fd, uid, gid)
    try:
        own(root)
        for mount in dependency_mounts(workspace):
            current = os.dup(root)
            try:
                # Open each component relative to a held directory descriptor.
                # Never follow an agent-controlled symlink or walk its contents.
                for part in mount.relative_to(workspace).parts:
                    child = os.open(part, flags, dir_fd=current)
                    os.close(current)
                    current = child
                    own(current)
            finally:
                os.close(current)
    finally:
        os.close(root)


if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('error: mount preparation is an entrypoint-only root operation.')
    node = pwd.getpwnam('node')
    prepare('/workspace', node.pw_uid, node.pw_gid)
