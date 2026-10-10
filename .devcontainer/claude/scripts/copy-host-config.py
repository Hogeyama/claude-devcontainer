#!/usr/bin/env python3
"""Copy the host's configuration into this workspace's agent's home.

The copy goes one way only: nothing in the agent's home reaches the host, where
an agent-written hook or memory would run or be read outside the container.
Copied are the entries of ~/.claude in ENTRIES, the files under ~/.claude that
statusLine and hooks commands name, and then everything in EXTRA, mirrored onto
the home directory (e.g. EXTRA/.gitconfig becomes ~/.gitconfig). Symbolic links
are followed on the host, so they may point anywhere the host can read (e.g.
/nix/store).

The agent must not be able to drop the host's hooks or change what they run, so
settings.json becomes a managed settings file, and the rest of ~/.claude is
copied as root into a sticky directory (see compose.yaml). Claude Code runs
managed hooks even with disableAllHooks or --bare.

Without arguments, copy now. With --background, as the Dev Containers
initializeCommand runs it before the containers start, archive now and hand the
archive to a detached process that copies it once the agent is up.
"""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time

# The workspace root: this script is .devcontainer/claude/scripts/ in it.
ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
EXTRA = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer/extra'
LOG = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer/copy-config.log'
# Configuration, not state. An allowlist, because ~/.claude also holds
# credentials and history, and new kinds of state appear with new versions.
ENTRIES = ('settings.json', 'CLAUDE.md', 'skills', 'agents', 'commands', 'output-styles')
# Never copied, even when a command names it or EXTRA links to all of ~/.claude.
SECRETS = ('.claude/.credentials.json',)
TARGET = '/home/node'
MANAGED = '/etc/claude-code/managed-settings.d/50-host.json'
# The agent's postStartCommand waits for this.
MARKER = '/run/claude-devcontainer/host-config-copied'
WAIT_SECONDS = 60
REFERENCE = re.compile(r'(?:~|\$HOME|\$\{HOME\})/\.claude/([^\s\'"`;|&<>()]+)')


def referenced(settings):
    """Return the paths under ~/.claude that statusLine and hooks commands name."""
    commands = []
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'command' and isinstance(item, str):
                    commands.append(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(settings.get('statusLine'))
    collect(settings.get('hooks'))
    paths = set()
    for command in commands:
        for match in REFERENCE.finditer(command):
            path = Path(match.group(1))
            if '..' not in path.parts and not path.is_absolute():
                paths.add(path)
    return sorted(paths)


def archive(source=SOURCE, extra=EXTRA):
    """Return a tar of the configuration to extract into the agent's home."""
    names = [Path(name) for name in ENTRIES]
    try:
        names += referenced(json.loads((source / 'settings.json').read_text()))
    except (OSError, ValueError, AttributeError):
        pass  # No settings, or not a JSON object: nothing more to copy.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', dereference=True) as tar:
        for name in names:
            add(tar, source / name, '.claude/' + str(name))
        # Later members overwrite earlier ones on extraction.
        if extra.is_dir():
            for path in sorted(extra.iterdir()):
                add(tar, path, path.name)
    return buffer.getvalue()


def add(tar, path, name, parents=()):
    """Add path, following links, and skip what cannot be read instead of failing."""
    if name in SECRETS:
        return
    try:
        info = tar.gettarinfo(path, name)
        real = path.resolve()
    except OSError:
        return  # Missing, or a dangling link.
    if info is None or real in parents:
        return  # A socket or the like, or a link back to a directory being added.
    # The agent extracts as node; keep the host's names out of the archive.
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    # Files from read-only places such as /nix/store must stay replaceable.
    info.mode |= 0o700 if info.isdir() else 0o600
    if info.isdir():
        tar.addfile(info)
        try:
            children = sorted(path.iterdir())
        except OSError:
            return
        for child in children:
            add(tar, child, name + '/' + child.name, (*parents, real))
    elif info.isfile():
        try:
            with path.open('rb') as content:
                tar.addfile(info, content)
        except OSError:
            pass


def docker(*arguments, data=None):
    return subprocess.run(['docker', *arguments], input=data, stdout=subprocess.PIPE, check=True).stdout


def find_agent():
    """Return the ID of this workspace's running agent, or None."""
    agents = docker('ps', '-q', '--filter', f'label=devcontainer.local_folder={ROOT}',
                    '--filter', 'label=com.docker.compose.service=agent').split()
    return agents[0].decode() if len(agents) == 1 else None


# Run as root in the agent, with the archive on stdin and ENTRIES as arguments.
COPY_SCRIPT = f'''set -eu
staging=$(mktemp -d)
tar -x -C "$staging" --no-same-owner
if [ -f "$staging/.claude/settings.json" ]; then
    install -m 644 "$staging/.claude/settings.json" {MANAGED}
    rm "$staging/.claude/settings.json"
else
    rm -f {MANAGED}
fi
cd {TARGET}/.claude
# Replace ENTRIES, so that what was removed on the host disappears here too.
rm -rf -- "$@"
if [ -d "$staging/.claude" ]; then
    chmod -R a+rX,go-w "$staging/.claude"
    for path in "$staging/.claude"/* "$staging/.claude"/.[!.]*; do
        [ -e "$path" ] || continue
        rm -rf -- "${{path##*/}}"
        mv -- "$path" .
    done
    rmdir "$staging/.claude"
fi
# The rest of EXTRA lies outside ~/.claude and stays the agent's. Copy it as
# node: root here lacks CAP_FOWNER, so it cannot update the agent's files.
chown -R node:node "$staging"
for path in "$staging"/* "$staging"/.[!.]*; do
    [ -e "$path" ] && setpriv --reuid=node --regid=node --init-groups cp -a -- "$path" {TARGET}/
done
rm -rf "$staging"
touch {MARKER}
chown node:node {MARKER}
'''


def copy(agent, data):
    entries = [name for name in ENTRIES if name != 'settings.json']
    docker('exec', '-i', '--user', '0', agent, 'sh', '-c', COPY_SCRIPT, 'sh', *entries, data=data)


def wait_and_copy(data):
    # Copy again whenever the agent changes: `up` may recreate an agent that
    # was running when this started.
    copied = None
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            agent = find_agent()
            if agent and agent != copied:
                copy(agent, data)
                copied = agent
                print(time.strftime('%F %T'), 'copied into', agent[:12], flush=True)
        except subprocess.CalledProcessError:
            pass  # Not ready yet.
        time.sleep(1)
    if not copied:
        print(time.strftime('%F %T'), f'no agent started within {WAIT_SECONDS}s.', flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == '--wait':
        return wait_and_copy(sys.stdin.buffer.read())
    data = archive()
    if mode == '--background':
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a') as log:
            child = subprocess.Popen([sys.executable, __file__, '--wait'], stdin=subprocess.PIPE,
                                     stdout=log, stderr=log, start_new_session=True)
        child.stdin.write(data)
        child.stdin.close()
        return
    agent = find_agent()
    if not agent:
        sys.exit('error: no running environment for this workspace. Run `devcontainer up`, then retry.')
    copy(agent, data)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
