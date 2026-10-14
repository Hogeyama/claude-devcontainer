#!/usr/bin/env python3
"""Native Wayland Chromium, private filesystem/network, proxy-only Unix bridge.

The host's IME remains with its compositor. No X11, session D-Bus, portal,
desktop opener, home directory, or credential socket crosses the boundary.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


async def bridge(reader, writer, connect):
    try:
        other_reader, other_writer = await connect()
        async def copy(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()
        await asyncio.gather(copy(reader, other_writer), copy(other_reader, writer))
    except (ConnectionError, OSError):
        writer.close()


def binary(env, name):
    value = os.environ.get(env) or shutil.which(name)
    if not value:
        raise SystemExit(f'{name} is required; install it or use nix develop .#browser-ide-demo')
    return str(Path(value).resolve())


async def inside(args):
    proxy = await asyncio.start_server(
        lambda r, w: bridge(r, w, lambda: asyncio.open_unix_connection('/bridge/proxy.sock')),
        '127.0.0.1', 18081)
    if args.test:
        await asyncio.start_unix_server(
            lambda r, w: bridge(r, w, lambda: asyncio.open_connection('127.0.0.1', 9222)),
            '/control/cdp.sock')
    flags = [args.chromium, '--disable-setuid-sandbox', '--no-first-run',
             '--no-default-browser-check', '--disable-background-networking',
             '--disable-sync', '--disable-quic', '--disable-gpu',
             '--password-store=basic', '--user-data-dir=/home/browser/profile',
             '--proxy-server=http://127.0.0.1:18081', '--proxy-bypass-list=<-loopback>',
             '--window-size=1440,1000']
    if args.headless:
        flags += ['--headless=new']
    else:
        flags += ['--ozone-platform=wayland', '--enable-wayland-ime',
                  '--wayland-text-input-version=3']
    if args.test:
        flags += ['--remote-debugging-port=9222', '--remote-debugging-address=127.0.0.1']
    flags += ['http://ide.localhost:8000/?folder=/workspace']
    # Run syscall-level boundary probes from the same namespace as the browser.
    results = {'network_namespace': os.readlink('/proc/self/ns/net'),
               'session_bus_present': Path('/run/user/' + str(os.getuid()) + '/bus').exists(),
               'host_home_present': any(p.name != 'browser' for p in Path('/home').iterdir()),
               'host_sentinel_present': Path('/tmp/browser-ide-host-sentinel').exists()}
    for label, address in [('proxy', ('127.0.0.1', 18081)),
                           ('host_loopback', ('127.0.0.1', 18082)),
                           ('direct_external', ('192.0.2.1', 80)),
                           ('direct_dns', ('192.0.2.1', 53))]:
        try:
            with socket.create_connection(address, timeout=.5):
                results[label] = 'connected'
        except OSError as e:
            results[label] = type(e).__name__
    for label, family, target in [('udp_dns', socket.AF_INET, ('192.0.2.1', 53)),
                                   ('ipv6_direct', socket.AF_INET6, ('2001:db8::1', 443))]:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            try:
                sock.sendto(b'DEMO_ONLY', target)
                results[label] = 'sent'
            except OSError as e:
                results[label] = type(e).__name__
    results['external_opener_exit'] = subprocess.run(['/bin/xdg-open', 'https://example.invalid/']).returncode
    Path('/home/browser/boundary.json').write_text(json.dumps(results, indent=2))
    proc = await asyncio.create_subprocess_exec(*flags)
    async with proxy:
        return await proc.wait()


async def outside(args):
    bwrap = binary('BWRAP_BIN', 'bwrap')
    chromium = binary('CHROMIUM_BIN', 'chromium')
    python = str(Path(sys.executable).resolve())
    uid = os.getuid()
    runtime = Path(args.runtime).resolve() if args.runtime else Path(tempfile.mkdtemp(prefix='browser-ide-'))
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.chmod(0o700)
    for child in ('bridge', 'control', 'home', 'bin'):
        (runtime / child).mkdir(mode=0o700, exist_ok=True)
    deny = runtime / 'bin/xdg-open'
    deny.write_text('#!' + binary('SH_BIN', 'sh') + '\nprintf "External application launch is disabled in this browser.\\n" >&2\nexit 126\n')
    deny.chmod(0o755)
    shutil.copy(deny, runtime / 'bin/xdg-email')
    # One fixed TCP destination. The browser cannot ask this bridge to dial
    # another host: the only exposed interface is an HTTP proxy byte stream.
    proxy_sock = runtime / 'bridge/proxy.sock'
    proxy_sock.unlink(missing_ok=True)
    proxy = await asyncio.start_unix_server(
        lambda r, w: bridge(r, w, lambda: asyncio.open_connection('127.0.0.1', args.proxy_port)),
        str(proxy_sock))
    cmd = [bwrap, '--die-with-parent', '--new-session', '--unshare-user',
           '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
           '--cap-drop', 'ALL', '--clearenv',
           '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
           '--dir', '/etc', '--dir', f'/run/user/{uid}',
           '--ro-bind', str(HERE), '/demo',
           '--ro-bind', str(runtime / 'bridge'), '/bridge',
           '--bind', str(runtime / 'home'), '/home/browser',
           '--ro-bind', str(runtime / 'bin'), '/bin',
           '--setenv', 'HOME', '/home/browser', '--setenv', 'PATH', '/bin:/usr/bin',
           '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'XDG_RUNTIME_DIR', f'/run/user/{uid}',
           '--setenv', 'DBUS_SESSION_BUS_ADDRESS', 'unix:path=/nonexistent-session-bus',
           '--chdir', '/home/browser']
    # Nix closures are self-contained; Debian's Chromium/Python use /usr and
    # the system library directories. Neither branch shares host user data.
    if python.startswith('/nix/store/') and chromium.startswith('/nix/store/'):
        cmd += ['--ro-bind', '/nix/store', '/nix/store']
    else:
        for path in ('/usr', '/lib', '/lib64'):
            if Path(path).exists():
                cmd += ['--ro-bind', str(Path(path).resolve()), path]
        if Path('/nix/store').exists():
            cmd += ['--ro-bind', '/nix/store', '/nix/store']
    if Path('/etc/fonts').exists():
        cmd += ['--ro-bind', '/etc/fonts', '/etc/fonts']
    if os.environ.get('FONTCONFIG_FILE'):
        cmd += ['--setenv', 'FONTCONFIG_FILE', os.environ['FONTCONFIG_FILE']]
    if not args.headless:
        name = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
        display = (Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{uid}')) / name).resolve()
        if not display.is_socket():
            raise SystemExit('A native Wayland session is required. X11 is deliberately not shared.')
        cmd += ['--ro-bind', str(display), f'/run/user/{uid}/wayland-0',
                '--setenv', 'WAYLAND_DISPLAY', 'wayland-0']
    if args.test:
        (runtime / 'control/cdp.sock').unlink(missing_ok=True)
        cmd += ['--bind', str(runtime / 'control'), '/control']
        await asyncio.start_server(
            lambda r, w: bridge(r, w, lambda: asyncio.open_unix_connection(str(runtime / 'control/cdp.sock'))),
            '127.0.0.1', args.cdp_port)
    else:
        cmd += ['--dir', '/control']
    cmd += [python, '/demo/local-browser.py', '--inside', '--chromium', chromium]
    if args.headless:
        cmd += ['--headless']
    if args.test:
        cmd += ['--test']
    (runtime / 'launcher.json').write_text(json.dumps({
        'host_network_namespace': os.readlink('/proc/self/ns/net'),
        'runtime': str(runtime), 'test_control_port': args.cdp_port if args.test else None,
        'headless': args.headless, 'chromium': chromium,
    }, indent=2))
    print(f'Browser runtime: {runtime}', flush=True)
    proc = await asyncio.create_subprocess_exec(*cmd)
    (runtime / 'launcher.pid').write_text(str(os.getpid()))
    async with proxy:
        try:
            return await proc.wait()
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--chromium', help=argparse.SUPPRESS)
    parser.add_argument('--headless', action='store_true', help='Automated tests only; does not test IME.')
    parser.add_argument('--test', action='store_true', help='Expose CDP on loopback for verification only.')
    parser.add_argument('--runtime', help='Private disposable browser state directory.')
    parser.add_argument('--proxy-port', type=int, default=18081)
    parser.add_argument('--cdp-port', type=int, default=19222)
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(inside(args) if args.inside else outside(args)))
    except KeyboardInterrupt:
        pass
