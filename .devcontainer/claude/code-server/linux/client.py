#!/usr/bin/env python3
"""The Linux client: the host's VS Code in a bubblewrap sandbox.

scripts/code-server.sh open runs this on the host, after server.py has the
agent's VS Code server up. As with the Windows client, VS Code reaches
code-server-proxy on 127.0.0.1:18086 and nothing else, and sees nothing of
the host but what the window needs:

- Namespaces: its own user, PID, network, IPC and UTS namespaces, every
  capability dropped. The network namespace has only loopback, so no
  setting, extension or page can reach another address: none is routable.
- Filesystem: the host's system directories, /nix/store and fonts read-only,
  for VS Code and this script; a private home with VS Code's persistent
  user data and extensions; the Wayland socket, for the window and the
  compositor's input method (text-input-v3). No home directory, session
  D-Bus, X11, SSH agent or Docker socket. xdg-open is a script that refuses.
- The proxy, through a relay: inside, this script listens on 127.0.0.1:18086
  and forwards each connection to a Unix socket; outside, it accepts those and
  forwards them to the proxy's published port. The allowlist of
  code-server-proxy (../proxy.py) decides what the sandbox may reach.

The resolver extension (resolver/) connects VS Code's remote authority to the
agent's server through that proxy; the Dev Containers extension, Docker and
the usual paths of docs/usage.md "VS Codeが作る経路" are not involved. What a
remote extension can still do, such as open a "local" terminal, happens in
this sandbox.

Usage:  client.py [--folder /workspace]   open the window (the default)
        client.py --check                 report VS Code, bwrap and the display
"""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
# .devcontainer/claude/scripts, for settings.py.
SCRIPTS = HERE.parents[1] / 'scripts'
PROXY_PORT = 18086
# The resolver extension's ID (resolver/package.json) and the remote authority
# it resolves. The server's folder is given on the command line.
EXTENSION = 'claude-devcontainer.agent-resolver'
AUTHORITY = 'claude-devcontainer+agent'
# The host's, shared read-only when present. The merged-usr links (/bin ->
# usr/bin) are recreated as links.
SYSTEM = ('/usr', '/lib', '/lib64', '/lib32', '/bin', '/sbin', '/nix/store',
          '/etc/fonts', '/etc/ld.so.cache', '/etc/alternatives', '/etc/localtime')
HOME = '/home/vscode'
# Paths in the sandbox.
RUN = '/run/code-server'
DENY = '''#!/bin/sh
printf 'claude-devcontainer: opening external applications is disabled in this VS Code.\\n' >&2
exit 126
'''
# An address nothing routes to (TEST-NET-1). Connecting must fail inside.
OUTSIDE = ('192.0.2.1', 80)
# Written to a new user data directory: no update or Marketplace checks,
# which the sandbox cannot make anyway.
SETTINGS = {
    'update.mode': 'none',
    'extensions.autoCheckUpdates': False,
    'extensions.autoUpdate': False,
    'telemetry.telemetryLevel': 'off',
    'workbench.startupEditor': 'none',
}


def fail(message):
    raise SystemExit('error: ' + message)


def find_code(setting='', which=shutil.which):
    """VS Code's real path: codeServer.vscode, or `code` on PATH.

    The real path matters: the sandbox shares the system directories, not a
    profile or home directory that a link on PATH may lead through.
    """
    if setting:
        path = Path(setting)
        if not path.is_file() or not os.access(path, os.X_OK):
            fail(f'codeServer.vscode: {setting} is not an executable file')
        return path.resolve()
    found = which('code')
    if not found:
        fail('VS Code (code) is not on PATH; install it or set codeServer.vscode')
    return Path(found).resolve()


def code_version(code):
    """(version, commit, arch) of `code --version`."""
    result = subprocess.run([str(code), '--version'], capture_output=True, text=True)
    lines = result.stdout.split()
    if result.returncode or len(lines) < 3:
        fail(f'{code} --version failed: {result.stderr.strip() or result.stdout.strip()}')
    return lines[0], lines[1], lines[2]


def wayland_socket(environ=os.environ):
    """The compositor's socket, which WAYLAND_DISPLAY names by name or path."""
    name = environ.get('WAYLAND_DISPLAY', 'wayland-0')
    runtime = environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    path = Path(name) if name.startswith('/') else Path(runtime) / name
    if not path.is_socket():
        fail(f'a Wayland session is required ({path} is not a socket); X11 is not shared with VS Code')
    return path


def state_directory(environ=os.environ):
    """Persistent client state: VS Code's user data and extensions, and the server's token."""
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return state / 'claude-devcontainer/vscode'


def load_settings():
    sys.path.insert(0, str(SCRIPTS))
    import settings
    return settings.load()['codeServer']


def sandbox(bwrap, code, home, runtime, wayland, environ=os.environ):
    """bwrap's command line, up to the command to run in the sandbox.

    home and runtime are host directories: the private home, and the relay's
    socket, this script and the refusing xdg-open. wayland is the compositor's
    socket, or None for a run without a window.
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
    # VS Code or Python installed elsewhere, as /opt/visual-studio-code or a
    # toolchain of its own. A directory of the home would open the home.
    shared = [Path(path).resolve() for path in SYSTEM if Path(path).exists()]
    for path in (code.parent, Path(sys.base_prefix).resolve()):
        if not any(path.is_relative_to(root) for root in shared):
            if path.is_relative_to(Path.home()):
                fail(f'{path} is in the home directory, which VS Code does not get; '
                     'install VS Code and Python system-wide or in /nix/store')
            cmd += ['--ro-bind', str(path), str(path)]
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


def code_command(code, folder):
    """VS Code's command line in the sandbox."""
    return [str(code), '--new-window', '--disable-updates', '--disable-telemetry',
            '--skip-welcome', '--skip-release-notes',
            f'--user-data-dir={HOME}/user-data', f'--extensions-dir={HOME}/extensions',
            f'--enable-proposed-api={EXTENSION}',
            # No keyring, no /dev/dri in the sandbox; the window is Wayland's
            # and takes the compositor's input method.
            '--password-store=basic', '--disable-gpu',
            '--ozone-platform=wayland', '--enable-features=WaylandWindowDecorations',
            '--enable-wayland-ime=true', '--wayland-text-input-version=3',
            f'--folder-uri=vscode-remote://{AUTHORITY}{folder}']


def prepare_home(home):
    """The private home: the resolver extension, and initial settings once."""
    extensions = home / 'extensions'
    extensions.mkdir(parents=True, exist_ok=True)
    version = json.loads((HERE / 'resolver/package.json').read_text())['version']
    target = extensions / f'{EXTENSION}-{version}'
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(HERE / 'resolver', target)
    user = home / 'user-data/User'
    user.mkdir(parents=True, exist_ok=True)
    if not (user / 'settings.json').exists():
        (user / 'settings.json').write_text(json.dumps(SETTINGS, indent=4) + '\n')


def write_etc(etc):
    """passwd, group and hosts for the sandbox: the user with the private home, and localhost.

    Node's os.userInfo() and the integrated terminal need the user; VS Code's
    port forwarding binds localhost, and there is no DNS inside.
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
    """In the sandbox: the loopback relay to the bridge socket, then VS Code.

    VS Code's launcher may exit at once and leave the window to a process of
    its own, so the sandbox is kept, with the relay, until nothing runs in it.
    """
    check_sandbox(args.host_netns)
    server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, lambda: asyncio.open_unix_connection(f'{RUN}/proxy.sock')),
        '127.0.0.1', PROXY_PORT)
    async with server:
        if args.probe:
            found = probe(args.host_netns)
            reader, writer = await asyncio.open_connection('127.0.0.1', PROXY_PORT)
            writer.write(b'probe\n')
            found['relay'] = (await reader.readline()).decode().strip()
            writer.close()
            print(json.dumps(found))
            return 0
        process = await asyncio.create_subprocess_exec(*code_command(Path(args.code), args.folder))
        status = await process.wait()
        while others():
            await asyncio.sleep(1)
        return status


async def outside(args):
    """On the host: the bridge socket to the proxy, and bwrap around inside()."""
    bwrap = shutil.which('bwrap') or fail('bubblewrap (bwrap) is required on the host')
    code = find_code(args.code or load_settings()['vscode'])
    wayland = None if args.probe else wayland_socket()
    if args.check:
        version, commit, arch = code_version(code)
        print(f'vscode: {code} ({version} {commit} {arch})\nbwrap: {bwrap}\nwayland: {wayland}')
        return 0
    state = state_directory()
    home = state / 'home'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    # VS Code's own single-instance lock does not see across PID namespaces.
    lock = open(state / 'lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail('the agent VS Code window is already open; close it first')
    try:
        token = (state / 'token').read_text().strip() if not args.probe else ''
    except FileNotFoundError:
        fail('no server token; run code-server.sh open, which starts the server first')
    prepare_home(home)
    runtime = Path(tempfile.mkdtemp(prefix='code-server-', dir=os.environ.get('XDG_RUNTIME_DIR')))
    try:
        (runtime / 'bin').mkdir(mode=0o700)
        for name in ('xdg-open', 'xdg-email'):
            (runtime / 'bin' / name).write_text(DENY)
            (runtime / 'bin' / name).chmod(0o755)
        write_etc(runtime / 'etc')
        shutil.copy(HERE / 'client.py', runtime / 'client.py')
        port = args.proxy_port
        bridge = await asyncio.start_unix_server(
            lambda reader, writer: relay(reader, writer, lambda: asyncio.open_connection('127.0.0.1', port)),
            str(runtime / 'proxy.sock'))
        command = sandbox(bwrap, code, home, runtime, wayland)
        if token:
            command += ['--setenv', 'CLAUDE_DEVCONTAINER_VSCODE_TOKEN', token]
        command += [str(Path(sys.executable).resolve()), f'{RUN}/client.py', '--inside',
                    '--code', str(code), '--folder', args.folder,
                    '--host-netns', os.readlink('/proc/self/ns/net')]
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
        lock.close()


def main():
    parser = argparse.ArgumentParser(description='Open the agent workspace in a sandboxed VS Code.')
    parser.add_argument('--folder', default='/workspace', help='the folder in the agent to open')
    parser.add_argument('--check', action='store_true', help='report what would be used, without opening a window')
    parser.add_argument('--code', help=argparse.SUPPRESS)        # tests: instead of codeServer.vscode
    parser.add_argument('--proxy-port', type=int, default=PROXY_PORT, help=argparse.SUPPRESS)  # tests
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)  # tests: report the sandbox, no window
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--host-netns', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(inside(args) if args.inside else outside(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == '__main__':
    main()
