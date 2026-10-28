#!/usr/bin/env python3
"""Launch the editor's Electron shell on Linux with Wayland or X11.

Electron connects directly to the workspace's code-server-proxy port.
Chromium's renderer sandbox and the shell's network and external-launch
restrictions remain enabled. There is no outer OS sandbox around Electron.
"""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
import layout  # noqa: E402

SHELL = HERE.parent / 'shell'
PROXY_PORT = 18086
TOKEN = 'CLAUDE_DEVCONTAINER_VSCODE_TOKEN'
# An Electron of the host's own to use instead, such as Nix's, whose libraries the downloaded one cannot find.
ELECTRON = 'CLAUDE_DEVCONTAINER_ELECTRON'
LAUNCHED = '.launched'
USERNS_RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
USERNS_ADVICE = ('AppArmor restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1), '
                 "which Chromium's sandbox needs; see code-server/README.md")


def fail(message):
    raise SystemExit('error: ' + message)


def electron_module():
    sys.path.insert(0, str(HERE.parent))
    import electron
    return electron


def electron_directory(environ=os.environ):
    """Where ../electron.py keeps this host's Electron, downloaded or not."""
    cache = Path(environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'claude-devcontainer/electron'
    electron = electron_module()
    return cache, electron.linux_platform()


def electron_override(environ=os.environ):
    """The executable that ELECTRON names, by path or as a command on PATH; None when unset."""
    name = environ.get(ELECTRON)
    if not name:
        return None
    found = shutil.which(name, path=environ.get('PATH'))
    if not found:
        fail(f'{ELECTRON}={name} is not an executable')
    return Path(found)


def ensure_electron():
    """The Electron executable: ELECTRON's, or ../electron.py's, downloaded on the host's network the first time."""
    if found := electron_override():
        return found
    electron = electron_module()
    cache, platform = electron_directory()
    return electron.ensure(cache, platform) / electron.executable(platform)


def userns_restricted(path=USERNS_RESTRICTION):
    """Whether AppArmor keeps unprivileged processes from creating user namespaces."""
    try:
        return path.read_text().strip() == '1'
    except OSError:
        return False


def wayland_socket(environ=os.environ):
    """The compositor's socket, which WAYLAND_DISPLAY names by name or path."""
    name = environ.get('WAYLAND_DISPLAY', 'wayland-0')
    runtime = environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    path = Path(name) if name.startswith('/') else Path(runtime) / name
    if not path.is_socket():
        fail(f'a Wayland session is required ({path} is not a socket); check WAYLAND_DISPLAY')
    return path


def display_backend(environ=os.environ):
    """Prefer an explicitly selected Wayland session, otherwise use X11."""
    if environ.get('WAYLAND_DISPLAY'):
        return 'wayland', str(wayland_socket(environ))
    if environ.get('DISPLAY'):
        return 'x11', environ['DISPLAY']
    fail('a Wayland or X11 session is required; set WAYLAND_DISPLAY or DISPLAY')


def workspace_root():
    # The repository, also in host mode, where the core is installed elsewhere.
    return layout.workspace_root()


def state_directory(root=None, environ=os.environ):
    """Persistent client state of one workspace: the shell's profile, and the log.

    Per workspace, so that two can be open at once without sharing a profile.
    """
    root = Path(root or workspace_root()).resolve()
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return state / 'claude-devcontainer/vscode' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24]


def shell_command(electron, shell, profile, port, folder, backend):
    """Connect to the host's proxy port, keeping Chromium's sandbox enabled."""
    command = [str(electron), str(shell), f'--proxy-port={port}', f'--folder={folder}',
               f'--user-data-dir={profile}', '--disable-gpu', f'--ozone-platform={backend}']
    if backend == 'wayland':
        command += ['--enable-features=WaylandWindowDecorations',
                    '--enable-wayland-ime', '--wayland-text-input-version=3']
    return command


def take_lock(state):
    """Keep one launcher per workspace while its Electron process runs.

    The lock is the open file, which a detached child inherits.
    """
    home = state / 'home'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(state / 'lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        fail("the agent's editor window is already open; close it first")
    return lock


def detach(state):
    """Fork: the child goes on as a session of its own, its output in client.log.

    The parent returns once the child reports that Electron is started, or exits with the child's status and the
    end of its log if it fails before that.
    """
    launched = state / 'home' / LAUNCHED
    launched.unlink(missing_ok=True)
    log = state / 'client.log'
    pid = os.fork()
    if pid == 0:
        os.setsid()
        with open(log, 'w') as out, open(os.devnull) as null:
            os.dup2(null.fileno(), 0)
            os.dup2(out.fileno(), 1)
            os.dup2(out.fileno(), 2)
        return True
    for _ in range(600):
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            sys.stderr.write(log.read_text()[-2000:])
            if userns_restricted():
                sys.stderr.write(USERNS_ADVICE + '\n')
            raise SystemExit(f'error: the editor did not start; see {log}')
        if launched.exists():
            print(f'the editor is opening; its output goes to {log}')
            return False
        time.sleep(0.1)
    raise SystemExit(f'error: the editor is taking too long to start; see {log}')


def report(electron, display):
    print(f'electron: {electron or "not downloaded yet; prepare or open downloads it"}\n'
          f'display: {display}')
    if userns_restricted():
        print('warning: ' + USERNS_ADVICE)


def open_window(args, state):
    electron = ensure_electron()
    # Retain the existing profile path when upgrading from the bwrap client.
    profile = state / 'home' / 'shell'
    with tempfile.TemporaryDirectory(prefix='code-server-') as runtime:
        shell = Path(runtime) / 'shell'
        shutil.copytree(SHELL, shell)
        command = shell_command(electron, shell, profile, args.proxy_port, args.folder, args.backend)
        environ = dict(os.environ)
        for name in ('ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'NODE_PATH'):
            environ.pop(name, None)
        if args.backend == 'wayland':
            environ.pop('DISPLAY', None)
        process = subprocess.Popen(command, cwd=state, env=environ)
        try:
            (state / 'home' / LAUNCHED).touch()
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description="Open the agent's workspace in the editor shell on Linux.")
    parser.add_argument('--folder', default='/workspace', help='the folder in the agent to open')
    parser.add_argument('--check', action='store_true', help='report what would be used, without opening a window')
    parser.add_argument('--prepare', action='store_true', help='download Electron, then report as --check')
    parser.add_argument('--proxy-port', type=int, default=PROXY_PORT,
                        help="the host port of this workspace's code-server-proxy")
    parser.add_argument('--detach', action='store_true',
                        help='return once Electron is started; its output goes to client.log in the state directory')
    args = parser.parse_args()
    try:
        if args.prepare:
            report(ensure_electron(), os.environ.get('WAYLAND_DISPLAY') or os.environ.get('DISPLAY') or 'not set')
            return
        if not args.check and not os.environ.get(TOKEN):
            fail(f'no connection token in {TOKEN}; open the editor with code-server.sh')
        args.backend, display = display_backend()
        if args.check:
            found = electron_override()
            if not found:
                electron = electron_module()
                cache, platform = electron_directory()
                found = cache / f'{electron.VERSION}-{platform}' / electron.executable(platform)
            report(found if found.is_file() else None, f'{args.backend}: {display}')
            return
        state = state_directory()
        lock = take_lock(state)  # noqa: F841 held until the launcher exits
        if args.detach and not detach(state):
            return
        sys.exit(open_window(args, state))
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as error:
        fail(str(error))


if __name__ == '__main__':
    main()
