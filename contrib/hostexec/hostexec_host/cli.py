"""hostexec: run commands on the host for a container, one approval per run.

  hostexec init [--force]
  hostexec serve [--listen ADDR] [--port N] [--ui-port N] [--map CONTAINER=HOST]... [--no-notify]
  hostexec review [--ui-port N]
  hostexec ui-url [--ui-port N]
  hostexec snippet [--port N | --secrets]
  hostexec service [serve's options]
"""
import json
import os
from pathlib import Path
import secrets
import signal
import ssl
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request

from .api import Server, api_handler
from .approvals import Approvals
from .display import command_line, shell_word, visible
from .request import Invalid, parse_mapping
from .run import INHERITED_ENV, kill_all
from .ui import ui_handler

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'client' / 'hostexec'
HOST_NAME = 'host.docker.internal'
DEFAULT_PORT = 7878
DEFAULT_UI_PORT = 7879
DEFAULT_LISTEN = '172.17.0.1'


class Files:
    def __init__(self):
        base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
        self.dir = Path(base) / 'hostexec'
        self.api_token = self.dir / 'api-token'
        self.ui_token = self.dir / 'ui-token'
        self.ca = self.dir / 'ca.pem'
        self.cert = self.dir / 'server.pem'
        self.key = self.dir / 'server-key.pem'


def tilde(path):
    """A path under $HOME as ~/..., for settings that expand ~ themselves: the
    proxy's ca, agent.mounts, and the shell running a secret's command."""
    home = str(Path.home())
    path = str(path)
    if home != '/' and (path == home or path.startswith(home + '/')):
        return '~' + path[len(home):]
    return path


def die(message):
    print(f'hostexec: {message}', file=sys.stderr)
    sys.exit(2)


def options(args, known):
    """`--name` options by kind: 'value', 'flag' or 'list'."""
    out = {}
    i = 0
    while i < len(args):
        a = args[i]
        name = a[2:]
        kind = known.get(name) if a.startswith('--') else None
        if kind is None:
            die(f'unknown argument {a}')
        if kind == 'flag':
            out[name] = True
        else:
            i += 1
            if i >= len(args):
                die(f'{a} needs a value')
            if kind == 'list':
                out.setdefault(name, []).append(args[i])
            else:
                out[name] = args[i]
        i += 1
    return out


def port(value, fallback):
    if value is None:
        return fallback
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        die(f'not a port: {value}')
    return int(value)


def write_secret(path):
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_hex(32) + '\n')


def read_secret(path):
    try:
        return path.read_text().strip()
    except OSError:
        die(f'cannot read {path}; run `hostexec init` first')


def openssl(args, cwd):
    try:
        r = subprocess.run(['openssl', *args], cwd=cwd, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        die(f'openssl {args[0]} failed: {e}')
    if r.returncode != 0:
        die(f'openssl {args[0]} failed: {r.stderr.strip()}')


def init(args):
    opts = options(args, {'force': 'flag'})
    f = Files()
    if f.api_token.exists() and not opts.get('force'):
        die(f'{f.dir} is already set up; --force makes new keys and tokens')
    f.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    f.dir.chmod(0o700)
    write_secret(f.api_token)
    write_secret(f.ui_token)
    # A CA that can sign for host.docker.internal alone, whose key is thrown
    # away once it has signed the server's certificate: trusting it in the
    # proxy trusts nothing else.
    ca_key = f.dir / 'ca-key.pem.tmp'
    csr = f.dir / 'server.csr.tmp'
    ext = f.dir / 'server.ext.tmp'
    try:
        openssl(['req', '-x509', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                 '-nodes', '-keyout', str(ca_key), '-out', str(f.ca), '-days', '3650',
                 '-subj', '/CN=hostexec local CA',
                 '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                 '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                 '-addext', f'nameConstraints=critical,permitted;DNS:{HOST_NAME}'], f.dir)
        openssl(['req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                 '-nodes', '-keyout', str(f.key), '-out', str(csr), '-subj', f'/CN={HOST_NAME}'], f.dir)
        ext.write_text('\n'.join([
            'basicConstraints=critical,CA:FALSE',
            'keyUsage=critical,digitalSignature',
            'extendedKeyUsage=serverAuth',
            f'subjectAltName=DNS:{HOST_NAME}',
            '']))
        openssl(['x509', '-req', '-in', str(csr), '-CA', str(f.ca), '-CAkey', str(ca_key),
                 '-set_serial', f'0x{secrets.token_hex(16)}',
                 '-days', '825', '-sha256', '-extfile', str(ext), '-out', str(f.cert)], f.dir)
        f.key.chmod(0o600)
    finally:
        for tmp in (ca_key, csr, ext):
            tmp.unlink(missing_ok=True)
    print(f'''hostexec: set up in {f.dir}

Add to ~/.config/claude-devcontainer/credentials.toml (`hostexec snippet --secrets`):

{textwrap.indent(secrets_snippet(f.api_token), '  ')}
and what `hostexec snippet` prints to .devcontainer/claude.local.toml of each
workspace (devcontainer up), or to ~/.config/claude-devcontainer/claude.toml
(claude-devcontainer up). Then restart the environment, run `hostexec serve`
on the host, and in the container:

  hostexec -- uname -a''')


def read_assets():
    web = ROOT / 'web'
    return {
        '/': ((web / 'index.html').read_text(), 'text/html'),
        '/app.js': ((web / 'app.js').read_text(), 'text/javascript'),
        '/style.css': ((web / 'style.css').read_text(), 'text/css'),
        '/favicon.svg': ((web / 'favicon.svg').read_text(), 'image/svg+xml'),
        '/favicon-pending.svg': ((web / 'favicon-pending.svg').read_text(), 'image/svg+xml'),
    }


def notify():
    # A fixed text: what the container sent never reaches the notification
    # daemon, some of which read markup.
    try:
        child = subprocess.Popen(['notify-send', 'hostexec', 'A command is waiting for approval'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return
    threading.Thread(target=child.wait, daemon=True).start()


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()


SERVE_OPTIONS = {'listen': 'value', 'port': 'value', 'ui-port': 'value', 'map': 'list', 'no-notify': 'flag'}


def serve_options(args):
    opts = options(args, SERVE_OPTIONS)
    for spec in opts.get('map', []):
        try:
            parse_mapping(spec)
        except Invalid as e:
            die(f'--map {e}')
    port(opts.get('port'), DEFAULT_PORT)
    port(opts.get('ui-port'), DEFAULT_UI_PORT)
    return opts


def serve(args):
    opts = serve_options(args)
    f = Files()
    api_token = read_secret(f.api_token)
    ui_token = read_secret(f.ui_token)
    mapping = [parse_mapping(spec) for spec in opts.get('map', [])]
    approvals = Approvals()
    listen = opts.get('listen', DEFAULT_LISTEN)
    api_port = port(opts.get('port'), DEFAULT_PORT)
    ui_port = port(opts.get('ui-port'), DEFAULT_UI_PORT)

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        tls.load_cert_chain(f.cert, f.key)
    except (OSError, ssl.SSLError) as e:
        die(f'cannot load {f.cert}: {e}; run `hostexec init` first')
    handler = api_handler(api_token, approvals, mapping, [api_token, ui_token], CLIENT.read_text())
    try:
        api = Server((listen, api_port), handler, tls)
    except OSError as e:
        die(f'cannot listen on {listen}:{api_port}: {e}')
    authority = f'127.0.0.1:{ui_port}'
    try:
        ui = Server(('127.0.0.1', ui_port), ui_handler(lambda: authority, ui_token, approvals, read_assets()))
    except OSError as e:
        die(f'cannot listen on {authority}: {e}')

    def on_hold(held):
        cmd = command_line(held.exec.argv)
        print(f'hostexec: waiting for approval: {cmd[:200] + "…" if len(cmd) > 200 else cmd}',
              file=sys.stderr, flush=True)
        if not opts.get('no-notify'):
            notify()
    approvals.on_hold(on_hold)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    start(api)
    start(ui)
    print(f'hostexec: taking requests on https://{listen}:{api_port} (as https://{HOST_NAME}:{api_port})',
          file=sys.stderr)
    for source, target in mapping:
        print(f'hostexec: {source} in the container is {target} here', file=sys.stderr)
    print(f'hostexec: approve in a browser: http://{authority}/#{ui_token}', file=sys.stderr)
    print('hostexec: or run `hostexec review` in another terminal', file=sys.stderr, flush=True)
    while not stop.wait(1):
        pass
    approvals.deny_all()
    kill_all()


def review(args):
    opts = options(args, {'ui-port': 'value'})
    token = read_secret(Files().ui_token)
    origin = f'http://127.0.0.1:{port(opts.get("ui-port"), DEFAULT_UI_PORT)}'

    # Straight to loopback: an http_proxy from the environment would get the token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body):
        req = urllib.request.Request(origin + path, data=json.dumps(body).encode(), method='POST', headers={
            'Authorization': f'Bearer {token}', 'Origin': origin, 'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=10) as res:
                ok, raw = True, res.read()
        except urllib.error.HTTPError as e:
            ok, raw = False, e.read()
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        return ok, data if isinstance(data, dict) else {}

    asked = set()
    print('hostexec review: waiting for requests; Ctrl-C to stop', file=sys.stderr)
    while True:
        try:
            ok, data = call('/api/pending', {})
        except OSError:
            die(f'no server at {origin}; is `hostexec serve` running?')
        if not ok:
            die(f'the server refused: {data.get("error")}')
        pending = [r for r in data.get('pending', []) if isinstance(r, dict) and r.get('id') not in asked]
        if not pending:
            time.sleep(1)
            continue
        r = pending[0]
        asked.add(r['id'])
        env = list(r['env'].items())
        where = '' if r['cwd'] == r['requestedCwd'] else f' (from {visible(r["requestedCwd"])} in the container)'
        print('\n'.join([
            '',
            f'command:   {command_line(r["argv"])}',
            f'directory: {visible(r["cwd"])}{where}',
            f'env:       {", ".join(INHERITED_ENV)} from the host{", plus:" if env else ", nothing else"}',
            *(f'           {visible(k)}={shell_word(v)}' for k, v in env),
        ]), file=sys.stderr)
        print('Run it on the host? [y/N] ', end='', file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:
            return
        approve = line.strip().lower() in ('y', 'yes')
        try:
            ok, data = call('/api/decision', {'id': r['id'], 'approve': approve})
        except OSError:
            die(f'no server at {origin}; is `hostexec serve` running?')
        print(('approved' if approve else 'denied') if ok else f'not decided: {data.get("error")}',
              file=sys.stderr)


def ui_url(args):
    opts = options(args, {'ui-port': 'value'})
    print(f'http://127.0.0.1:{port(opts.get("ui-port"), DEFAULT_UI_PORT)}/#{read_secret(Files().ui_token)}')


def snippet(ca_path, api_port):
    """The proxy settings, the same for every workspace: the proxy names the
    workspace in X-Hostexec-Map from the HOSTEXEC_WORKSPACE secret."""
    return f'''# hostexec: commands on the host, one approval per run.
[proxy]
ca = "{tilde(ca_path)}"

[proxy.hosts."{HOST_NAME}"]
routes = ["POST /run", "GET /client"]
https = {api_port}

[proxy.hosts."{HOST_NAME}".inject]
Authorization = "Bearer ${{HOSTEXEC_TOKEN}}"
X-Hostexec-Map = "/workspace=${{HOSTEXEC_WORKSPACE}}"

# The client, read-only and on PATH.
[agent.mounts]
"/usr/bin/hostexec" = "{tilde(CLIENT)}"
'''


def secrets_snippet(api_token):
    """The secrets, for ~/.config/claude-devcontainer/credentials.toml."""
    return f'''[secrets]
HOSTEXEC_TOKEN = {{ command = "cat {tilde(api_token)}" }}
# The workspace's path on the host, which claude-devcontainer gives secret commands.
HOSTEXEC_WORKSPACE = {{ command = 'printf %s "$CLAUDE_DEVCONTAINER_WORKSPACE"' }}
'''


def print_snippet(args):
    opts = options(args, {'port': 'value', 'secrets': 'flag'})
    f = Files()
    if opts.get('secrets'):
        sys.stdout.write(secrets_snippet(f.api_token))
    else:
        sys.stdout.write(snippet(f.ca, port(opts.get('port'), DEFAULT_PORT)))


def unit_quote(value, expand_dollar=True):
    """A word in a systemd unit, as systemd reads it back: quoted, with its
    specifiers (%) and, in a command line, variables ($) taken literally."""
    for c in value:
        if c in '\n\r\0':
            die(f'{value!r}: no line breaks or NULs in a systemd unit')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    return '"' + (value.replace('$', '$$') if expand_dollar else value) + '"'


def service(args):
    """A systemd user unit that runs `hostexec serve` with these options. The
    commands it runs get PATH from it, so it keeps this shell's PATH."""
    serve_options(args)
    command = ' '.join(unit_quote(a) for a in [sys.executable, str(ROOT / 'hostexec'), 'serve', *args])
    path = unit_quote('PATH=' + os.environ.get('PATH', ''), expand_dollar=False)
    sys.stdout.write(f'''# hostexec: commands on the host, one approval per run.
# For ~/.config/systemd/user/hostexec.service; see README.md.
[Unit]
Description=hostexec: commands on the host for containers, one approval per run
# The default --listen is on Docker's bridge, which may come up later: keep trying.
StartLimitIntervalSec=0

[Service]
ExecStart={command}
# What approved commands find, as in the shell this was written from.
Environment={path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
''')


COMMANDS = {'init': init, 'serve': serve, 'review': review, 'ui-url': ui_url, 'snippet': print_snippet,
            'service': service}
USAGE = '''usage: hostexec init [--force]
       hostexec serve [--listen ADDR] [--port N] [--ui-port N] [--map CONTAINER=HOST]... [--no-notify]
       hostexec review [--ui-port N]
       hostexec ui-url [--ui-port N]
       hostexec snippet [--port N | --secrets]
       hostexec service [serve's options]'''


def main(argv):
    command = argv[0] if argv else None
    if command not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 0 if command in (None, '--help', '-h') else 2
    try:
        COMMANDS[command](argv[1:])
    except KeyboardInterrupt:
        return 130
    return 0
