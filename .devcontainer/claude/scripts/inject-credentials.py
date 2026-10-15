#!/usr/bin/env python3
"""Send host credentials to this workspace's running proxy.

Without arguments, inject now. With --background, as the Dev Containers
initializeCommand runs it before the containers start, hand the credentials to a
detached process that injects them once the proxy is up, and again whenever the
proxy is recreated or the Claude login is refreshed, until the environment stops.
"""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# The workspace root: this script is .devcontainer/claude/scripts/ in it.
ROOT = Path(__file__).resolve().parents[3]
# Shell assignments such as GITHUB_TOKEN=$(pass github/token): the Claude
# credential, and the secrets that proxy.hosts of the workspace's claude.toml
# injects as ${NAME}. The host runs it, so it must stay outside the workspace, which the
# agent can write.
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer/credentials'
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer'
LOG = STATE / 'inject.log'
# The Claude credential: an API key, or else the host's own Claude Code login,
# which needs nothing set when it is in the default place.
CLAUDE_KEYS = ('ANTHROPIC_API_KEY', 'CLAUDE_CREDENTIALS_FILE')
# A setup-token, no longer used: it carries only user:inference, so claude.ai
# connectors cannot work with it, and it is not refreshed.
SETUP_TOKEN = 'CLAUDE_CODE_OAUTH_TOKEN'
SECRET_NAME = re.compile(r'[A-Z_][A-Z0-9_]*')
# What the shell itself changes while reading the file.
SHELL_VARIABLES = {'PWD', 'OLDPWD', 'SHLVL', '_'}
# How long the watcher keeps watching after it starts, even while the
# environment is gone: `up` builds the images after initializeCommand starts it,
# which takes minutes the first time, and --remove-existing-container removes
# the running agent before that build.
START_SECONDS = 600
# After that, how long it waits for the environment to reappear.
WAIT_SECONDS = 60
POLL_SECONDS = 5
RETRY = 'Run .devcontainer/claude/scripts/inject-credentials.py on the host once the container is up.'
# The agent's Claude Code takes the dummy token's scopes from this drop-in, so
# that it offers what the login allows (claude.ai connectors need
# user:mcp_servers). With an API key it is removed.
SCOPES_SETTINGS = '/etc/claude-code/managed-settings.d/60-claude-login.json'

# Claude Code's login refresh, as Claude Code 2.1.295 does it.
TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
# Earlier than Claude Code's 5 minutes: the host need not run Claude Code, so
# this is usually the only refresher, and a failed refresh gets ~60 retries
# before the token expires.
REFRESH_BEFORE_MS = 30 * 60 * 1000
REFRESH_RETRY_SECONDS = 30
# Claude Code's locks around a refresh, in proper-lockfile's form: a directory,
# its mtime renewed while held, taken over once it is that stale.
LOCK_RENEW_SECONDS = 5
LOCK_STALE_SECONDS = 60
LOCK_ATTEMPTS = 5


class Unavailable(Exception):
    pass


def log(*message):
    print(time.strftime('%F %T'), *message, flush=True)


def docker(*arguments, data=None):
    return subprocess.run(['docker', *arguments], input=data, stdout=subprocess.PIPE,
                          text=True, check=True).stdout


def config():
    """Return the Claude credential and the secrets assigned in CONFIG.

    The secrets are only what CONFIG assigns: the workspace's proxy.hosts names
    the ones to send, and must not reach the rest of the host's environment.
    """
    values = {key: os.environ.get(key, '') for key in CLAUDE_KEYS}
    secrets = {}
    if CONFIG.exists():
        result = subprocess.run(['sh', '-c', 'set -a; . "$1"; env -0', 'sh', str(CONFIG)], stdout=subprocess.PIPE)
        if result.returncode:
            raise Unavailable(f'{CONFIG} failed with exit code {result.returncode}.')
        loaded = dict(item.split('=', 1) for item in result.stdout.decode(errors='replace').split('\0') if '=' in item)
        secrets = {name: value for name, value in loaded.items()
                   if SECRET_NAME.fullmatch(name) and name not in SHELL_VARIABLES and os.environ.get(name) != value}
        # A Claude credential in the environment replaces the file's, of any kind.
        if not any(values.values()):
            values = {key: loaded.get(key, '') if key in secrets else '' for key in CLAUDE_KEYS}
    if all(values.values()):
        raise Unavailable('Set either ANTHROPIC_API_KEY or CLAUDE_CREDENTIALS_FILE, not both.')
    if not any(values.values()):
        home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
        values['CLAUDE_CREDENTIALS_FILE'] = str(home / '.credentials.json')
    if os.environ.get(SETUP_TOKEN) or SETUP_TOKEN in secrets:
        using = values['CLAUDE_CREDENTIALS_FILE'] or 'ANTHROPIC_API_KEY'
        print(f'warning: {SETUP_TOKEN} (a setup-token) is no longer used, and Claude uses {using}; '
              f'remove it from {CONFIG}.', file=sys.stderr)
    for key in (*CLAUDE_KEYS, SETUP_TOKEN):
        secrets.pop(key, None)  # Never a secret to inject elsewhere.
    values['secrets'] = secrets
    return values


class ApiKey:
    """An API key, which the host renews by hand. Claude.ai features (Artifacts,
    connectors) need a login instead."""
    mode = 'api-key'

    def __init__(self, token):
        self.token = token

    def current(self):
        return self.token

    def settings(self):
        return None


class Login:
    """Claude Code's own login on the host (~/.claude/.credentials.json).

    The proxy gets its access token, which this refreshes shortly before it
    expires, coordinating with the host's Claude Code through the same locks and
    by reading the file again under them. The refreshed tokens are written back,
    so the host's Claude Code goes on with them too.
    """
    mode = 'oauth'

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.retry_at = 0
        oauth = self.read()
        if not oauth.get('accessToken') or not oauth.get('refreshToken'):
            raise Unavailable(f'{self.path} has no Claude login; run `claude /login` on the host.')
        self.oauth = oauth

    def read(self):
        try:
            return json.loads(self.path.read_text()).get('claudeAiOauth') or {}
        except (OSError, ValueError) as error:
            raise Unavailable(f'cannot read {self.path}: {error}') from None

    def current(self):
        """The access token, refreshed first if it expires soon; on failure the held one."""
        now = time.time()
        try:
            self.adopt(self.read())  # The host's Claude Code may have refreshed it.
        except Unavailable:
            pass
        if now >= self.retry_at and self.expiring():
            try:
                self.refresh()
            except Exception as error:  # Keep the held token; Claude may still accept it.
                log(f'refreshing the Claude login failed: {error}; retrying in {REFRESH_RETRY_SECONDS}s.')
                self.retry_at = now + REFRESH_RETRY_SECONDS
        return self.oauth['accessToken']

    def expiring(self, oauth=None):
        expires = (oauth or self.oauth).get('expiresAt')
        return not isinstance(expires, (int, float)) or expires - time.time() * 1000 < REFRESH_BEFORE_MS

    def adopt(self, oauth):
        """Take the file's login when another process (the host's Claude Code) has refreshed it."""
        if oauth.get('accessToken') and oauth['accessToken'] != self.oauth['accessToken'] and not self.expiring(oauth):
            self.oauth = oauth
            return True
        return False

    def refresh(self):
        if self.adopt(self.read()):
            return
        directory = self.path.parent
        locks = [directory / '.oauth_refresh.lock', Path(str(directory.resolve()) + '.lock')]
        for attempt in range(LOCK_ATTEMPTS):
            held = []
            try:
                for lock in locks:
                    held.append(Lock(lock))
            except BlockingIOError:
                for lock in held:
                    lock.release()
                time.sleep(1 + attempt % 2)
                if self.adopt(self.read()):
                    return
                continue
            try:
                return self.refresh_locked()
            finally:
                for lock in reversed(held):
                    lock.release()
        raise RuntimeError("Claude Code's refresh locks stayed held")

    def refresh_locked(self):
        oauth = self.read()
        if self.adopt(oauth) or not self.expiring(oauth):
            self.oauth = oauth
            return
        request = urllib.request.Request(TOKEN_URL, method='POST', data=json.dumps({
            'grant_type': 'refresh_token', 'refresh_token': oauth['refreshToken'],
            'client_id': oauth.get('clientId') or CLIENT_ID, 'scope': ' '.join(oauth.get('scopes') or []),
        }).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'claude-devcontainer'})
        with urllib.request.urlopen(request, timeout=20) as response:
            answer = json.load(response)
        now = time.time() * 1000
        updated = {'accessToken': answer['access_token'],
                   'refreshToken': answer.get('refresh_token') or oauth['refreshToken'],
                   'expiresAt': int(now + answer['expires_in'] * 1000)}
        if isinstance(answer.get('refresh_token_expires_in'), (int, float)):
            updated['refreshTokenExpiresAt'] = int(now + answer['refresh_token_expires_in'] * 1000)
        self.write(updated)
        self.oauth = {**oauth, **updated}
        log('refreshed the Claude login.')

    def write(self, updated):
        """Write the new tokens back, keeping everything else in the file as it is now."""
        data = json.loads(self.path.read_text())
        data['claudeAiOauth'] = {**data.get('claudeAiOauth', {}), **updated}
        with tempfile.NamedTemporaryFile('w', dir=self.path.parent, prefix='.credentials.', delete=False) as output:
            os.chmod(output.name, 0o600)
            json.dump(data, output)
        os.replace(output.name, self.path)

    def settings(self):
        env = {'CLAUDE_CODE_OAUTH_SCOPES': ' '.join(self.oauth.get('scopes') or [])}
        for key, name in (('subscriptionType', 'CLAUDE_CODE_SUBSCRIPTION_TYPE'),
                          ('rateLimitTier', 'CLAUDE_CODE_RATE_LIMIT_TIER')):
            if isinstance(self.oauth.get(key), str):
                env[name] = self.oauth[key]
        return {'env': env}


class Lock:
    """A proper-lockfile lock: mkdir to take, renew the mtime while held, rmdir to release."""

    def __init__(self, path):
        self.path = path
        try:
            os.mkdir(path)
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS
            except FileNotFoundError:
                stale = True
            if not stale:
                raise BlockingIOError(path) from None
            try:
                os.rmdir(path)
            except FileNotFoundError:
                pass
            try:
                os.mkdir(path)
            except FileExistsError:  # Another process took it over first.
                raise BlockingIOError(path) from None
        self.stop = threading.Event()
        self.renewer = threading.Thread(target=self.renew, daemon=True)
        self.renewer.start()

    def renew(self):
        while not self.stop.wait(LOCK_RENEW_SECONDS):
            try:
                os.utime(self.path)
            except FileNotFoundError:
                return

    def release(self):
        self.stop.set()
        self.renewer.join()
        try:
            os.rmdir(self.path)
        except FileNotFoundError:
            pass


def claude(values):
    if values['CLAUDE_CREDENTIALS_FILE']:
        return Login(values['CLAUDE_CREDENTIALS_FILE'])
    return ApiKey(values['ANTHROPIC_API_KEY'])


def requested_secrets(proxy):
    """The names of the secrets that the proxy's proxy.hosts injects."""
    return docker('exec', proxy, 'python', '/opt/proxy/workspace_config.py', 'secrets').split()


def snapshot(values, source, names):
    """The proxy's JSON, with the requested secrets that CONFIG assigns."""
    missing = [name for name in names if name not in values['secrets']]
    if missing:
        # The proxy refuses the requests that need them, naming them too.
        log(f'proxy.hosts injects {", ".join(missing)}, which {CONFIG} does not assign.')
    return json.dumps(dict(anthropic_token=source.current(), auth_mode=source.mode,
                           secrets={name: values['secrets'][name] for name in names if name in values['secrets']}))


def find_environment():
    """Return the IDs of this workspace's running agent and proxy, or (None, None)."""
    agents = docker('ps', '--filter', f'label=devcontainer.local_folder={ROOT}',
                    '--filter', 'label=com.docker.compose.service=agent',
                    '--format', '{{.ID}} {{.Label "com.docker.compose.project"}}').split()
    if len(agents) != 2:
        return None, None
    agent, project = agents
    proxies = docker('ps', '-q', '--filter', f'label=com.docker.compose.project={project}',
                     '--filter', 'label=com.docker.compose.service=proxy').split()
    return (agent, proxies[0]) if len(proxies) == 1 else (None, None)


def inject(proxy, data):
    docker('exec', '-i', proxy, 'python', '/opt/proxy/credentials.py', 'inject', data=data)


def install_settings(agent, settings):
    if settings is None:
        docker('exec', '--user', '0', agent, 'rm', '-f', SCOPES_SETTINGS)
        return
    docker('exec', '-i', '--user', '0', agent, 'sh', '-c',
           'umask 022; cat > "$1.tmp" && mv "$1.tmp" "$1"', 'sh', SCOPES_SETTINGS,
           data=json.dumps(settings))


def watch(values):
    """Inject into the environment while it runs, and again on a new proxy or token."""
    take_over()
    source = claude(values)
    injected, names = None, []
    started = last_seen = time.monotonic()
    while time.monotonic() < max(started + START_SECONDS, last_seen + WAIT_SECONDS):
        try:
            agent, proxy = find_environment()
            if proxy:
                last_seen = time.monotonic()
                if injected is None or injected[1] != proxy:
                    names = requested_secrets(proxy)
                state = (agent, proxy, snapshot(values, source, names), json.dumps(source.settings()))
                if state != injected:
                    inject(proxy, state[2])
                    install_settings(agent, source.settings())
                    if injected is None or injected[:2] != state[:2]:
                        log('injected into', proxy[:12], 'for agent', agent[:12])
                    injected = state
        except subprocess.CalledProcessError:
            pass  # Not ready yet.
        time.sleep(POLL_SECONDS)
    if injected is None:
        log(f'no proxy started within {START_SECONDS}s.', RETRY)
    else:
        log('the environment stopped.')


def take_over():
    """Stop this workspace's previous watcher: the newest one has the current settings."""
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / f'watch-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]}.pid'
    handle = open(path, 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        try:
            os.kill(int(handle.read().strip()), signal.SIGTERM)
        except (ValueError, ProcessLookupError):
            pass
        fcntl.flock(handle, fcntl.LOCK_EX)
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    watch.handle = handle  # Held until exit.


def start_background(values):
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as log_file:
        child = subprocess.Popen([sys.executable, __file__, '--watch'], stdin=subprocess.PIPE,
                                 stdout=log_file, stderr=log_file, start_new_session=True, text=True)
    child.stdin.write(json.dumps(values))
    child.stdin.close()
    print(f'Credentials will be injected once the proxy starts (log: {LOG}).\n'
          'If Claude reports HTTP 403 "no credentials", ' + RETRY[0].lower() + RETRY[1:])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        if mode == '--watch':
            return watch(json.loads(sys.stdin.read()))
        values = config()
        if mode == '--background':
            claude(values)  # Report a missing login now, not in the log.
            return start_background(values)
        source = claude(values)
    except Unavailable as error:
        # Never fail initializeCommand: the environment works once injected later.
        print(f'{"warning" if mode in ("--background", "--watch") else "error"}: {error}\n{RETRY}', file=sys.stderr)
        sys.exit(0 if mode in ('--background', '--watch') else 1)
    agent, proxy = find_environment()
    if not proxy:
        sys.exit('error: no running environment for this workspace. Run `devcontainer up`, then retry.')
    inject(proxy, snapshot(values, source, requested_secrets(proxy)))
    install_settings(agent, source.settings())


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
