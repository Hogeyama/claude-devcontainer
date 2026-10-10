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
import shlex
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
# Never copied, nor anything under them, even when a command names it or EXTRA
# links to all of ~/.claude. sumi/ is where sumi's README keeps secrets.txt; the
# secrets files actually in use are found by secrets_files().
SECRETS = ('.claude/.credentials.json', '.claude/sumi')
TARGET = '/home/node'
MANAGED = '/etc/claude-code/managed-settings.d/50-host.json'
# The agent's postStartCommand waits for this.
MARKER = '/run/claude-devcontainer/host-config-copied'
WAIT_SECONDS = 60
REFERENCE = re.compile(r'(?:~|\$HOME|\$\{HOME\})/\.claude/([^\s\'"`;|&<>()]+)')
# A command that runs sumi, as `sumi init` writes it on the host.
SUMI = re.compile(r'(?:^|[/\s\'"])sumi(?:$|[\s\'"])')


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


def setting(name, default):
    """Return a setting from the environment, else from .devcontainer/.env, else from
    .devcontainer/claude.env, as compose reads them."""
    value = os.environ.get(name)
    for file in ('.env', 'claude.env'):
        # An explicit empty value overrides lower-priority files too; Compose's
        # ${NAME:-default} then selects the default.
        if value is not None:
            break
        try:
            for line in (ROOT / '.devcontainer' / file).read_text().splitlines():
                key, _, rest = line.partition('=')
                if key.strip() == name:
                    value = rest.strip().strip('"\'')
        except OSError:
            pass
    return value or default


def secrets_files(settings):
    """Return the secrets files sumi reads, on the host and for the container, resolved."""
    commands = []
    for groups in (settings.get('hooks') or {}).values():
        for group in groups if isinstance(groups, list) else ():
            for hook in group.get('hooks', ()) if isinstance(group, dict) else ():
                if isinstance(hook, dict) and SUMI.search(str(hook.get('command', ''))):
                    args = hook.get('args')
                    commands.append([str(arg) for arg in args] if isinstance(args, list) else str(hook['command']))
    commands.append(str((settings.get('env') or {}).get('CLAUDE_CODE_SHELL_PREFIX', '')))
    # Match the included compose.yaml's base for relative bind sources.
    files = [ROOT / '.devcontainer/claude'
             / Path(setting('CLAUDE_DEVCONTAINER_SUMI_DIR', str(Path.home() / '.claude/sumi')))
             / setting('CLAUDE_DEVCONTAINER_SUMI_FILE', 'secrets.txt')]
    for command in commands:
        try:
            words = command if isinstance(command, list) else shlex.split(command)
        except ValueError:
            continue
        for word, following in zip(words, words[1:] + ['']):
            if word == '--secrets-file' and following:
                files.append(Path(following))
            elif word.startswith('--secrets-file='):
                files.append(Path(word.partition('=')[2]))
    return frozenset(path.expanduser().resolve() for path in files)


def without_sumi(settings):
    """Remove the host's sumi hooks and shell, whose paths only exist on the host.

    CLAUDE_DEVCONTAINER_SUMI=1 brings the container's own; a broken copy of the
    host's would refuse every tool result.
    """
    hooks = settings.get('hooks')
    if isinstance(hooks, dict):
        for event, groups in list(hooks.items()):
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get('hooks'), list):
                    group['hooks'] = [hook for hook in group['hooks'] if not (
                        isinstance(hook, dict) and SUMI.search(str(hook.get('command', ''))))]
                    if not group['hooks']:
                        continue
                kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    env = settings.get('env')
    if isinstance(env, dict) and SUMI.search(str(env.get('CLAUDE_CODE_SHELL_PREFIX', ''))):
        del env['CLAUDE_CODE_SHELL_PREFIX']
        env.pop('CLAUDE_CODE_SHELL', None)
    return settings


def archive(source=SOURCE, extra=EXTRA):
    """Return a tar of the configuration to extract into the agent's home."""
    names = [Path(name) for name in ENTRIES if name != 'settings.json']
    excluded = secrets_files({})
    try:
        settings = json.loads((source / 'settings.json').read_text())
        names += referenced(settings)
        excluded = secrets_files(settings)
        settings = json.dumps(without_sumi(settings), indent=2).encode()
    except OSError:
        settings = None
    except (ValueError, AttributeError):
        # Not a JSON object: copy it as it is, for Claude Code to report.
        settings = (source / 'settings.json').read_bytes()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', dereference=True) as tar:
        if settings is not None:
            info = tarfile.TarInfo('.claude/settings.json')
            info.size, info.mode = len(settings), 0o600
            tar.addfile(info, io.BytesIO(settings))
        for name in names:
            add(tar, source / name, '.claude/' + str(name), excluded=excluded)
        # Later members overwrite earlier ones on extraction.
        if extra.is_dir():
            for path in sorted(extra.iterdir()):
                add(tar, path, path.name, excluded=excluded)
    return buffer.getvalue()


def add(tar, path, name, parents=(), excluded=frozenset()):
    """Add path, following links, and skip what cannot be read instead of failing."""
    if name in SECRETS or name.startswith(tuple(secret + '/' for secret in SECRETS)):
        return
    try:
        info = tar.gettarinfo(path, name)
        real = path.resolve()
    except OSError:
        return  # Missing, or a dangling link.
    if info is None or real in parents or real in excluded:
        return  # A socket or the like, a link back to a directory being added, or secrets.
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
            add(tar, child, name + '/' + child.name, (*parents, real), excluded)
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
