#!/usr/bin/env python3
"""Copy the host's configuration into this workspace's agent's home.

The copy goes one way only: nothing in the agent's home reaches the host, where
an agent-written hook or memory would run or be read outside the container.
Copied are settings.json, the entries of ~/.claude in ENTRIES, and the files
under ~/.claude that statusLine and hooks commands name. Everything in
DOTFILES is then mirrored onto the home directory (e.g. DOTFILES/.gitconfig
becomes ~/.gitconfig). Symbolic links are followed on the host, so they may
point anywhere the host can read (e.g. /nix/store).

The agent must not be able to drop the host's hooks or change what they run, so
settings.json becomes a managed settings file, and the rest of ~/.claude is
copied as root into a sticky directory (see compose.yaml). Claude Code runs
managed hooks even with disableAllHooks or --bare.

Without arguments, copy now. With --background, as the Dev Containers
initializeCommand runs it before the containers start, archive now and hand the
archive to a detached process that copies it once the agent is up.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402
import host_runtime  # noqa: E402
from host_runtime import log  # noqa: E402
import settings  # noqa: E402

# The workspace root (scripts/layout.py).
ROOT = layout.workspace_root()
SOURCE = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer'
DOTFILES = CONFIG / 'dotfiles'
# DOTFILES' former name, refused so that its files are not silently left out.
LEGACY_DOTFILES = CONFIG / 'extra'
LOG = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer/copy-config.log'
# Configuration, not state. An allowlist, because ~/.claude also holds
# credentials and history, and new kinds of state appear with new versions.
# settings.json is handled separately as a managed settings file.
ENTRIES = ('CLAUDE.md', 'skills', 'agents', 'commands', 'output-styles')
# Never copied, nor anything under them, even when a command names it or
# DOTFILES links to all of ~/.claude. sumi/ is where sumi's README keeps
# secrets.txt; the secrets files actually in use are found by secrets_files().
SECRETS = ('.claude/.credentials.json', '.claude/sumi')
TARGET = '/home/node'
MANAGED = '/etc/claude-code/managed-settings.d/50-host.json'
# The agent's postStartCommand waits for this.
MARKER = '/run/claude-devcontainer/host-config-copied'
# How long the copy watches for the agent: `up` builds the images after
# initializeCommand starts it, which takes minutes the first time.
WAIT_SECONDS = 600
# Only the newest copy for a workspace goes on: an older one, left from an
# earlier `up`, holds the host's configuration as it was then.
WATCHER = LOG.parent / f'copy-config-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]}.pid'
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


def sumi_secrets():
    """The secrets file of sumi.secrets. ValueError if the settings are invalid:
    without them, the file to leave out is unknown, and nothing may be copied."""
    return settings.load(ROOT)['sumi']['secrets']


def secrets_files(settings, container):
    """Return the secrets files sumi reads, on the host and for the container, resolved."""
    commands = []
    for groups in (settings.get('hooks') or {}).values():
        for group in groups if isinstance(groups, list) else ():
            for hook in group.get('hooks', ()) if isinstance(group, dict) else ():
                if isinstance(hook, dict) and SUMI.search(str(hook.get('command', ''))):
                    args = hook.get('args')
                    commands.append([str(arg) for arg in args] if isinstance(args, list) else str(hook['command']))
    commands.append(str((settings.get('env') or {}).get('CLAUDE_CODE_SHELL_PREFIX', '')))
    files = [container]
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

    sumi.enabled in claude.toml brings the container's own; a broken copy of the
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


def archive(source=SOURCE, dotfiles=DOTFILES):
    """Return a tar of the configuration to extract into the agent's home."""
    names = [Path(name) for name in ENTRIES]
    container = sumi_secrets()
    excluded = secrets_files({}, container)
    try:
        settings = json.loads((source / 'settings.json').read_text())
        names += referenced(settings)
        excluded = secrets_files(settings, container)
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
        if dotfiles.is_dir():
            for path in sorted(dotfiles.iterdir()):
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


def check_legacy_dotfiles(legacy=LEGACY_DOTFILES, dotfiles=DOTFILES):
    if legacy.exists() or legacy.is_symlink():
        sys.exit(f'error: {legacy} is now {dotfiles}. Nothing was copied; run\n'
                 f'  mv {shlex.quote(str(legacy))} {shlex.quote(str(dotfiles))}')


def docker(*arguments, data=None):
    return subprocess.run(['docker', *arguments], input=data, stdout=subprocess.PIPE, check=True).stdout


def find_agent():
    """Return the ID of this workspace's running agent, or None."""
    return host_runtime.containers(ROOT).get('agent')


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
# The rest of DOTFILES lies outside ~/.claude and stays the agent's. Copy it as
# node: root here lacks CAP_FOWNER, so it cannot update the agent's files.
chown -R node:node "$staging"
for path in "$staging"/* "$staging"/.[!.]*; do
    [ -e "$path" ] && setpriv --reuid=node --regid=node --init-groups cp -a -- "$path" {TARGET}/
done
rm -rf "$staging"
# The marker directory belongs to node: even a pre-existing symlink must never
# make root touch or chown its target. Drop privileges before accessing it.
setpriv --reuid=node --regid=node --init-groups \\
    --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \\
    touch -- {MARKER}
'''


def copy(agent, data):
    docker('exec', '-i', '--user', '0', agent, 'sh', '-c', COPY_SCRIPT, 'sh', *ENTRIES, data=data)


def wait_and_copy(data):
    # Copy again whenever the agent changes: `up` may recreate an agent that
    # was running when this started.
    WATCHER.write_text(str(os.getpid()))
    copied = None
    for _ in host_runtime.poll(WAIT_SECONDS):
        if WATCHER.read_text() != str(os.getpid()):
            log('a newer copy took over.')
            return
        try:
            agent = find_agent()
            if agent and agent != copied:
                copy(agent, data)
                copied = agent
                log('copied into', agent[:12])
        except subprocess.CalledProcessError:
            pass  # Not ready yet.
    if not copied:
        log(f'no agent started within {WAIT_SECONDS}s.')


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == '--wait':
        return wait_and_copy(sys.stdin.buffer.read())
    check_legacy_dotfiles()
    data = archive()
    if mode == '--background':
        host_runtime.background(__file__, '--wait', data, LOG)
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
    except ValueError as error:
        sys.exit(f'error: {error}. Nothing was copied: the settings name the secrets file to leave out.')
