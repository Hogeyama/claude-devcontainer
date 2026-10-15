#!/usr/bin/env python3
"""The agent's VS Code server for the Linux client: the host's VS Code's own.

scripts/code-server.sh open runs this on the host before client.py. A VS
Code client connects only to the server of its own commit, so the server is
not built into the image, which would tie the host's VS Code to it; it is
taken here, on the host's network, as the Dev Containers extension would do
from the host too:

- The server of `code --version`'s commit, from update.code.visualstudio.com,
  cached at ~/.cache/claude-devcontainer/vscode-server/<commit>/.
- codeServer.extensions, installed with that server's CLI from the
  Marketplace into the cache, whenever the list changes. The agent has no
  access to the Marketplace.

Both go into the agent's vscode-server volume by `docker compose cp`, so
they are root's there and the agent cannot change them. A new connection
token goes to the agent and to the client's state directory, and the server
starts as node on port 8089, which the agent's firewall opens to
code-server-proxy alone. The extensions are copied anew into the server's
data directory at each start: the list is what is installed, as with the
Windows client's code-server.
"""
import hashlib
import http.client
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import client  # noqa: E402

COMPOSE = client.SCRIPTS / 'compose.sh'
PORT = 8089
# In the agent: the volume, the server's data and the token's tmpfs.
VOLUME = '/opt/vscode-server'
DATA = '/home/node/.vscode-server'
TOKEN_FILE = '/run/claude-devcontainer/vscode-token'
LOG = '/run/claude-devcontainer/vscode-server.log'
# In a cached extension set: the list it was installed from, written last.
INSTALLED = '.installed'
# Why the server must match: VS Code refuses a server of another commit.
UPDATE = 'https://update.code.visualstudio.com/commit:{commit}/server-linux-{arch}/stable'


def fail(message):
    raise SystemExit('error: ' + message)


def cache_directory(environ=os.environ):
    cache = Path(environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return cache / 'claude-devcontainer/vscode-server'


def server_url(commit, arch):
    if arch not in ('x64', 'arm64'):
        fail(f'no Linux server for VS Code on {arch}')
    return UPDATE.format(commit=commit, arch=arch)


def download(url, target):
    """Extract the server tarball at url into target, without its top directory."""
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        archive = Path(temporary) / 'server.tar.gz'
        with urllib.request.urlopen(url) as response, open(archive, 'wb') as out:
            shutil.copyfileobj(response, out)
        with tarfile.open(archive) as tar:
            members = []
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if len(parts) < 2:
                    continue
                member.name = str(Path(*parts[1:]))
                members.append(member)
            # The data filter (Python 3.12) refuses links and paths that leave the directory.
            if hasattr(tarfile, 'data_filter'):
                tar.extractall(Path(temporary) / 'server', members, filter='data')
            else:
                tar.extractall(Path(temporary) / 'server', members)
        if not (Path(temporary) / 'server/bin/code-server').is_file():
            fail(f'{url} is not a VS Code server tarball')
        os.rename(Path(temporary) / 'server', target)


def ensure_server(cache, commit, arch, fetch=download):
    """The cached server of commit, downloaded once."""
    server = cache / commit / 'server'
    if not (server / 'bin/code-server').is_file():
        server.parent.mkdir(parents=True, exist_ok=True)
        print(f'downloading the VS Code server for {commit}', flush=True)
        fetch(server_url(commit, arch), server)
    return server


def extensions_digest(ids):
    """Names the cached and deployed extension sets; a changed list is a new set."""
    return hashlib.sha256(' '.join(ids).encode()).hexdigest()[:16]


def ensure_extensions(cache, server, ids, run=subprocess.run):
    """The cached extensions of ids, installed by the server's CLI when the list changes."""
    digest = extensions_digest(ids)
    extensions = cache / 'extensions' / digest
    # Installed in place: the CLI records absolute paths in extensions.json.
    # A marker, written last, tells a complete set from an interrupted one.
    if (extensions / INSTALLED).is_file():
        return extensions
    shutil.rmtree(extensions, ignore_errors=True)
    extensions.mkdir(parents=True)
    if ids:
        print('installing extensions: ' + ' '.join(ids), flush=True)
        cli = [str(server / 'bin/code-server'), '--extensions-dir', str(extensions),
               '--server-data-dir', str(cache / 'data'), '--accept-server-license-terms']
        install = cli + [arg for item in ids for arg in ('--install-extension', item)]
        if run(install).returncode:
            fail('extension installation failed')
        listed = run(cli + ['--list-extensions', '--show-versions'], capture_output=True, text=True).stdout.lower().split()
        for item in ids:
            found = listed if '@' in item else [entry.split('@')[0] for entry in listed]
            if item.lower() not in found:
                fail(f'codeServer.extensions: {item} was not installed')
    (extensions / INSTALLED).write_text(' '.join(ids) + '\n')
    return extensions


def compose(*args, input=None, check=True, capture=False):
    """docker compose for this workspace's environment, by scripts/compose.sh."""
    return subprocess.run([str(COMPOSE), *args], input=input, text=True, check=check, capture_output=capture)


def in_agent(compose, path):
    return compose('exec', '-T', 'agent', 'test', '-e', path, check=False).returncode == 0


def deploy(compose, source, target):
    """Copy a cached directory into the agent's volume once, as root, whole or not at all."""
    if in_agent(compose, target):
        return
    partial = target + '.partial'
    compose('exec', '-T', 'agent', 'rm', '-rf', partial)
    compose('cp', str(source), f'agent:{partial}')
    compose('exec', '-T', 'agent', 'mv', partial, target)


def new_token(compose, state):
    """A connection token for this start, in the agent and in the client's state."""
    token = secrets.token_hex(16)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / 'token'
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text(token + '\n')
    compose('exec', '-T', '-u', 'node', 'agent', 'sh', '-ec', f'umask 077; cat > {TOKEN_FILE}', input=token + '\n')
    return token


def start_script(commit, extensions):
    """The shell that starts the server in the agent, replacing a running one."""
    # Anchored: the server's node processes, not this shell, whose command
    # line names the same paths.
    return f'''
pkill -f '^{VOLUME}/' || true
while pgrep -f '^{VOLUME}/' >/dev/null; do sleep 0.2; done
rm -rf {DATA}/extensions
mkdir -p {DATA}
cp -r {extensions} {DATA}/extensions
exec {VOLUME}/{commit}/bin/code-server --host 0.0.0.0 --port {PORT} \\
    --connection-token-file {TOKEN_FILE} --accept-server-license-terms \\
    --server-data-dir {DATA} --extensions-dir {DATA}/extensions \\
    --disable-telemetry </dev/null >>{LOG} 2>&1
'''


def start(compose, commit, extensions):
    compose('exec', '-d', '-u', 'node', '-e', 'HOME=/home/node', 'agent', 'sh', '-ec', start_script(commit, extensions))


def wait_ready(commit, proxy_port=client.PROXY_PORT, attempts=120):
    """Until the server answers /version with its commit through code-server-proxy."""
    for _ in range(attempts):
        connection = http.client.HTTPConnection('127.0.0.1', proxy_port, timeout=2)
        try:
            connection.request('GET', 'http://vscode.localhost:8000/version')
            response = connection.getresponse()
            body = response.read().decode().strip()
            # 403: up, with /version behind the connection token.
            if response.status == 403 or (response.status == 200 and body == commit):
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.5)
    fail(f'the VS Code server did not answer through code-server-proxy; see compose.sh exec agent cat {LOG}')


def main():
    settings = client.load_settings()
    code = client.find_code(settings['vscode'])
    version, commit, arch = client.code_version(code)
    cache = cache_directory()
    server = ensure_server(cache, commit, arch)
    extensions = ensure_extensions(cache, server, settings['extensions'])
    deploy(compose, server, f'{VOLUME}/{commit}')
    deploy(compose, extensions, f'{VOLUME}/extensions-{extensions.name}')
    new_token(compose, client.state_directory())
    start(compose, commit, f'{VOLUME}/extensions-{extensions.name}')
    wait_ready(commit)
    print(f'VS Code server {version} ({commit}) is up in the agent', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'error: {error}')
