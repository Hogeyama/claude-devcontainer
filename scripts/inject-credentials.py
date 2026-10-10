#!/usr/bin/env python3
"""Send host credentials to this workspace's running proxy.

Without arguments, inject now. With --background, as the Dev Containers
initializeCommand runs it before the containers start, hand the credentials to a
detached process that injects them once the proxy is up.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
# Shell assignments such as GITHUB_TOKEN=$(pass github/token). The host runs it,
# so it must stay outside the workspace, which the agent can write.
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer/credentials'
LOG = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer/inject.log'
KEYS = ('GITHUB_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY')
WAIT_SECONDS = 60
RETRY = 'Run ./scripts/inject-credentials.py on the host once the container is up.'


class Unavailable(Exception):
    pass


def docker(*arguments, data=None):
    return subprocess.run(['docker', *arguments], input=data, stdout=subprocess.PIPE,
                          text=True, check=True).stdout


def complete(values):
    return values['GITHUB_TOKEN'] and (values['CLAUDE_CODE_OAUTH_TOKEN'] or values['ANTHROPIC_API_KEY'])


def snapshot():
    """Return the credentials, from the environment or else from CONFIG, as the proxy's JSON."""
    values = {key: os.environ.get(key, '') for key in KEYS}
    if not complete(values) and CONFIG.exists():
        result = subprocess.run(['sh', '-c', 'set -a; . "$1"; env -0', 'sh', str(CONFIG)], stdout=subprocess.PIPE)
        if result.returncode:
            raise Unavailable(f'{CONFIG} failed with exit code {result.returncode}.')
        loaded = dict(item.split('=', 1) for item in result.stdout.decode(errors='replace').split('\0') if '=' in item)
        values = {key: values[key] or loaded.get(key, '') for key in KEYS}
    if not values['GITHUB_TOKEN']:
        raise Unavailable(f'GITHUB_TOKEN is set neither in the environment nor in {CONFIG}.')
    claude = [(mode, values[key]) for mode, key in
              (('oauth', 'CLAUDE_CODE_OAUTH_TOKEN'), ('api-key', 'ANTHROPIC_API_KEY')) if values[key]]
    if len(claude) != 1:
        raise Unavailable('Set exactly one of CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY.')
    [(mode, anthropic)] = claude
    return json.dumps(dict(github_token=values['GITHUB_TOKEN'], anthropic_token=anthropic, auth_mode=mode))


def find_proxy():
    """Return the ID of this workspace's running proxy, or None."""
    projects = docker('ps', '--filter', f'label=devcontainer.local_folder={ROOT}',
                      '--filter', 'label=com.docker.compose.service=agent',
                      '--format', '{{.Label "com.docker.compose.project"}}').split()
    if len(projects) != 1:
        return None
    proxies = docker('ps', '-q', '--filter', f'label=com.docker.compose.project={projects[0]}',
                     '--filter', 'label=com.docker.compose.service=proxy').split()
    return proxies[0] if len(proxies) == 1 else None


def inject(proxy, data):
    docker('exec', '-i', proxy, 'python', '/opt/proxy/credentials.py', 'inject', data=data)


def wait_and_inject(data):
    # Inject again whenever the proxy changes: `up` may recreate a proxy that
    # was running when this started, and the new one starts without credentials.
    injected = None
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            proxy = find_proxy()
            if proxy and proxy != injected:
                inject(proxy, data)
                injected = proxy
                print(time.strftime('%F %T'), 'injected into', proxy[:12], flush=True)
        except subprocess.CalledProcessError:
            pass  # Not ready yet.
        time.sleep(1)
    if not injected:
        print(time.strftime('%F %T'), f'no proxy started within {WAIT_SECONDS}s.', RETRY)


def start_background(data):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as log:
        child = subprocess.Popen([sys.executable, __file__, '--wait'], stdin=subprocess.PIPE,
                                 stdout=log, stderr=log, start_new_session=True, text=True)
    child.stdin.write(data)
    child.stdin.close()
    print(f'Credentials will be injected once the proxy starts (log: {LOG}).\n'
          'If Claude reports HTTP 403 "no credentials", ' + RETRY[0].lower() + RETRY[1:])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == '--wait':
        return wait_and_inject(sys.stdin.read())
    try:
        data = snapshot()
    except Unavailable as error:
        # Never fail initializeCommand: the environment works once injected later.
        print(f'{"warning" if mode == "--background" else "error"}: {error}\n{RETRY}', file=sys.stderr)
        sys.exit(0 if mode == '--background' else 1)
    if mode == '--background':
        return start_background(data)
    proxy = find_proxy()
    if not proxy:
        sys.exit('error: no running environment for this workspace. Run `devcontainer up`, then retry.')
    inject(proxy, data)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
