#!/usr/bin/env python3
"""The Linux client: the editor's Electron shell (../shell/) in a bubblewrap sandbox.

scripts/code-server.sh open runs this on the host, with the agent's server
up and its connection token in CLAUDE_DEVCONTAINER_VSCODE_TOKEN. The shell
itself sends every request to code-server-proxy and refuses any other
destination; the sandbox is a second wall, the OS's, in case the shell is
taken over. Inside, the shell reaches the proxy on 127.0.0.1:18086 and
nothing else, and sees nothing of the host but what the window needs:

- Namespaces: its own user, PID, network, IPC and UTS namespaces, every
  capability dropped. The network namespace has only loopback, so no page
  can reach another address: none is routable.
- Filesystem: the host's system directories, /nix/store and fonts read-only,
  for Electron's libraries and this script; Electron, from the cache of
  ../electron.py, and a copy of the shell, read-only; a private home with the
  shell's profile; the Wayland socket, for the window and the compositor's
  input method (text-input-v3). No home directory, session D-Bus, X11, SSH
  agent or Docker socket. xdg-open is a script that refuses.
- The proxy, through a relay: inside, this script listens on 127.0.0.1:18086
  and forwards each connection to a Unix socket; outside, it accepts those and
  forwards them to the proxy's published port. The allowlist of
  code-server-proxy (../proxy.py) decides what the sandbox may reach.

Chromium's own sandbox stays on, nesting user namespaces in this one.
Where AppArmor restricts unprivileged user namespaces (Ubuntu 23.10 and
later), bubblewrap and Chromium need a profile that allows them; never
--no-sandbox, which would leave the shell's renderers unconfined.

Usage:  client.py [--folder /workspace]   open the window (the default)
        client.py --prepare               download Electron, then report as --check
        client.py --check                 report Electron, bwrap and the display
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
# In the sandbox this file is /run/code-server/client.py, with no workspace
# or shell beside it (workspace_root).
SHELL = HERE.parent / 'shell'
# Inside the sandbox, where the relay listens and the shell's proxy is. Each
# sandbox has its own network namespace, so the number is the same for every
# workspace; the proxy's published port on the host is not (--proxy-port).
PROXY_PORT = 18086
TOKEN = 'CLAUDE_DEVCONTAINER_VSCODE_TOKEN'
# The host's, shared read-only when present. The merged-usr links (/bin ->
# usr/bin) are recreated as links.
SYSTEM = ('/usr', '/lib', '/lib64', '/lib32', '/bin', '/sbin', '/nix/store',
          '/etc/fonts', '/etc/ld.so.cache', '/etc/alternatives', '/etc/localtime')
HOME = '/home/vscode'
# Paths in the sandbox.
RUN = '/run/code-server'
ELECTRON = '/opt/electron'
# In the private home: the sandbox puts it there once VS Code's process is
# started (the window follows), which a detached parent waits for.
LAUNCHED = '.launched'
DENY = '''#!/bin/sh
printf 'claude-devcontainer: opening external applications is disabled in this VS Code.\\n' >&2
exit 126
'''
# An address nothing routes to (TEST-NET-1). Connecting must fail inside.
OUTSIDE = ('192.0.2.1', 80)
USERNS_RESTRICTION = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
USERNS_ADVICE = ('AppArmor restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1), '
                 "which bubblewrap and Chromium's sandbox need; see code-server/README.md")


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


def ensure_electron():
    """Electron of ../electron.py, downloaded on the host's network the first time."""
    return electron_module().ensure(*electron_directory())


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
        fail(f'a Wayland session is required ({path} is not a socket); X11 is not shared with VS Code')
    return path


def workspace_root():
    return HERE.parents[3]


def state_directory(root=None, environ=os.environ):
    """Persistent client state of one workspace: the shell's profile, and the log.

    Per workspace, so that two can be open at once: Electron instances must
    not share a profile, and its single-instance lock does not see across
    PID namespaces.
    """
    root = Path(root or workspace_root()).resolve()
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return state / 'claude-devcontainer/vscode' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24]


def sandbox(bwrap, electron, home, runtime, wayland, environ=os.environ):
    """bwrap's command line, up to the command to run in the sandbox.

    electron, home and runtime are host directories: Electron, the private
    home, and the relay's socket, this script, the shell and the refusing
    xdg-open. electron and wayland, the compositor's socket, are None for a
    run without a window.
    """
    uid = os.getuid()
    cmd = [bwrap, '--die-with-parent', '--new-session', '--unshare-user', '--unshare-pid',
           '--unshare-net', '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--clearenv',
           '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc',
           '--dir', f'/run/user/{uid}']
    for path in SYSTEM:
        if Path(path).is_symlink():
            cmd += ['--symlink', os.readlink(path), path]
        elif Path(path).exists():
            cmd += ['--ro-bind', path, path]
    # Python installed elsewhere, as a toolchain of its own. A directory of
    # the home would open the home.
    shared = [Path(path).resolve() for path in SYSTEM if Path(path).exists()]
    python = Path(sys.base_prefix).resolve()
    if not any(python.is_relative_to(root) for root in shared):
        if python.is_relative_to(Path.home()):
            fail(f'{python} is in the home directory, which the sandbox does not get; '
                 'install Python system-wide or in /nix/store')
        cmd += ['--ro-bind', str(python), str(python)]
    if electron:
        cmd += ['--ro-bind', str(electron), ELECTRON]
    # Nothing of the host's /etc but these: the user, and localhost.
    for name in ('passwd', 'group', 'hosts'):
        cmd += ['--ro-bind', str(runtime / 'etc' / name), f'/etc/{name}']
    cmd += ['--ro-bind', str(runtime), RUN,
            '--bind', str(home), HOME,
            '--chdir', HOME,
            '--setenv', 'HOME', HOME,
            '--setenv', 'PATH', f'{RUN}/bin:/usr/bin:/bin',
            '--setenv', 'XDG_RUNTIME_DIR', f'/run/user/{uid}',
            '--setenv', 'LANG', environ.get('LANG', 'C.UTF-8'),
            # No session bus: a name that does not exist, so nothing is tried.
            '--setenv', 'DBUS_SESSION_BUS_ADDRESS', 'unix:path=/nonexistent']
    for name in ('LC_ALL', 'FONTCONFIG_FILE', 'XCURSOR_THEME', 'XCURSOR_SIZE'):
        if environ.get(name):
            cmd += ['--setenv', name, environ[name]]
    if wayland:
        cmd += ['--ro-bind', str(wayland), f'/run/user/{uid}/wayland-0',
                '--setenv', 'WAYLAND_DISPLAY', 'wayland-0']
    return cmd


def shell_command(folder):
    """The shell's command line in the sandbox."""
    return [f'{ELECTRON}/electron', f'{RUN}/shell', f'--proxy-port={PROXY_PORT}', f'--folder={folder}',
            f'--user-data-dir={HOME}/shell',
            # No /dev/dri in the sandbox; the window is Wayland's and takes
            # the compositor's input method.
            '--disable-gpu', '--ozone-platform=wayland', '--enable-features=WaylandWindowDecorations',
            '--enable-wayland-ime', '--wayland-text-input-version=3']


def write_etc(etc):
    """passwd, group and hosts for the sandbox: the user with the private home, and localhost.

    Chromium looks the user up, and there is no DNS inside.
    """
    uid, gid = os.getuid(), os.getgid()
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        name = 'vscode'
    shell = '/bin/bash' if Path('/bin/bash').exists() else '/bin/sh'
    etc.mkdir(mode=0o700)
    (etc / 'passwd').write_text(f'{name}:x:{uid}:{gid}:{name}:{HOME}:{shell}\n')
    (etc / 'group').write_text(f'{name}:x:{gid}:\n')
    (etc / 'hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')


async def relay(reader, writer, connect):
    """Copy both ways between an accepted connection and a new one."""
    try:
        peer_reader, peer_writer = await connect()
    except OSError:
        writer.close()
        return

    async def copy(source, target):
        try:
            while data := await source.read(65536):
                target.write(data)
                await target.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            target.close()
    await asyncio.gather(copy(reader, peer_writer), copy(peer_reader, writer))


def probe(host_netns):
    """What the sandbox lets this process do; inside() refuses to go on unless it is nothing."""
    result = {'own_network_namespace': os.readlink('/proc/self/ns/net') != host_netns,
              'home_entries': sorted(path.name for path in Path('/home').iterdir())}
    try:
        with socket.create_connection(OUTSIDE, timeout=1):
            result['outside'] = 'connected'
    except OSError as error:
        result['outside'] = type(error).__name__
    return result


def check_sandbox(host_netns):
    found = probe(host_netns)
    if not found['own_network_namespace']:
        fail('VS Code would run in the host network namespace')
    if found['outside'] == 'connected':
        fail('the sandbox can reach addresses outside it')
    if found['home_entries'] != ['vscode']:
        fail('the sandbox can see a home directory')


def others():
    """PIDs in the sandbox besides this process and bwrap's init."""
    return [int(name) for name in os.listdir('/proc') if name.isdigit() and int(name) not in (1, os.getpid())]


async def inside(args):
    """In the sandbox: the loopback relay to the bridge socket, then the shell.

    The sandbox is kept, with the relay, until nothing runs in it, Chromium's
    helper processes included.
    """
    check_sandbox(args.host_netns)
    server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, lambda: asyncio.open_unix_connection(f'{RUN}/proxy.sock')),
        '127.0.0.1', PROXY_PORT)
    async with server:
        if args.probe:
            Path(HOME, LAUNCHED).touch()
            found = probe(args.host_netns)
            reader, writer = await asyncio.open_connection('127.0.0.1', PROXY_PORT)
            writer.write(b'probe\n')
            found['relay'] = (await reader.readline()).decode().strip()
            writer.close()
            print(json.dumps(found))
            return 0
        process = await asyncio.create_subprocess_exec(*shell_command(args.folder))
        Path(HOME, LAUNCHED).touch()
        status = await process.wait()
        while others():
            await asyncio.sleep(1)
        return status


def take_lock(state):
    """One window per workspace: the shell's own single-instance lock does not see across PID namespaces.

    The lock is the open file, which a detached child inherits.
    """
    home = state / 'home'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(state / 'lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail("the agent's editor window is already open; close it first")
    return lock


def detach(state):
    """Fork: the child goes on as a session of its own, its output in client.log.

    The parent returns once the sandbox reports (LAUNCHED in the private
    home) that the shell is started, or exits with the child's status and the
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
            print(f'the editor is open; its output goes to {log}')
            return False
        time.sleep(0.1)
    raise SystemExit(f'error: the editor is taking too long to start; see {log}')


def report(bwrap, electron, wayland):
    print(f'electron: {electron or "not downloaded yet; prepare or open downloads it"}\n'
          f'bwrap: {bwrap}\nwayland: {wayland}')
    if userns_restricted():
        print('warning: ' + USERNS_ADVICE)


async def outside(args):
    """On the host: the bridge socket to the proxy, and bwrap around inside()."""
    token = os.environ.get(TOKEN, '')
    if not token and not (args.probe or args.check or args.prepare):
        fail(f'no connection token in {TOKEN}; open the editor with code-server.sh open')
    bwrap = shutil.which('bwrap') or fail('bubblewrap (bwrap) is required on the host')
    wayland = None if args.probe else wayland_socket()
    if args.check:
        cache, platform = electron_directory()
        found = cache / f'{electron_module().VERSION}-{platform}'
        report(bwrap, found if found.is_dir() else None, wayland)
        return 0
    if args.prepare:
        report(bwrap, ensure_electron(), wayland)
        return 0
    electron = None if args.probe else ensure_electron()
    home = state_directory() / 'home'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix='code-server-', dir=os.environ.get('XDG_RUNTIME_DIR')))
    try:
        (runtime / 'bin').mkdir(mode=0o700)
        for name in ('xdg-open', 'xdg-email'):
            (runtime / 'bin' / name).write_text(DENY)
            (runtime / 'bin' / name).chmod(0o755)
        write_etc(runtime / 'etc')
        shutil.copy(HERE / 'client.py', runtime / 'client.py')
        # A copy, so that the window runs the shell as checked out at its start.
        shutil.copytree(SHELL, runtime / 'shell')
        port = args.proxy_port
        bridge = await asyncio.start_unix_server(
            lambda reader, writer: relay(reader, writer, lambda: asyncio.open_connection('127.0.0.1', port)),
            str(runtime / 'proxy.sock'))
        command = sandbox(bwrap, electron, home, runtime, wayland)
        if token:
            command += ['--setenv', TOKEN, token]
        command += [str(Path(sys.executable).resolve()), f'{RUN}/client.py', '--inside',
                    '--folder', args.folder, '--host-netns', os.readlink('/proc/self/ns/net')]
        if args.probe:
            command.append('--probe')
        async with bridge:
            process = await asyncio.create_subprocess_exec(*command)
            try:
                return await process.wait()
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
    finally:
        shutil.rmtree(runtime, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Open the agent's workspace in the sandboxed editor shell.")
    parser.add_argument('--folder', default='/workspace', help='the folder in the agent to open')
    parser.add_argument('--check', action='store_true', help='report what would be used, without opening a window')
    parser.add_argument('--prepare', action='store_true', help='download Electron, then report as --check')
    parser.add_argument('--proxy-port', type=int, default=PROXY_PORT,
                        help="the host port of this workspace's code-server-proxy")
    parser.add_argument('--detach', action='store_true',
                        help='return once the editor is started; its output goes to client.log in the state directory')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)  # tests: report the sandbox, no window
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--host-netns', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.inside:
            sys.exit(asyncio.run(inside(args)))
        if not args.check and not args.prepare:
            state = state_directory()
            lock = take_lock(state)  # noqa: F841  held until this process and its child end
            if args.detach and not detach(state):
                return
        sys.exit(asyncio.run(outside(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == '__main__':
    main()
