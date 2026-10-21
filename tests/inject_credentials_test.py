"""How inject-credentials.py keeps the host's Claude login fresh; no Docker or network needed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inject_credentials', ROOT / '.devcontainer/claude/scripts/inject-credentials.py')
inject_credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inject_credentials)

SCOPES = ['user:profile', 'user:inference', 'user:mcp_servers']


def now_ms():
    return int(time.time() * 1000)


class LoginTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / '.claude'
        self.home.mkdir()
        self.path = self.home / '.credentials.json'
        self.requests = []
        self.answer = {'access_token': 'access-2', 'refresh_token': 'refresh-2', 'expires_in': 3600}
        patcher = mock.patch.object(inject_credentials.urllib.request, 'urlopen', side_effect=self.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in [('LOCK_ATTEMPTS', 2), ('LOCK_RENEW_SECONDS', 0.05)]:
            patcher = mock.patch.object(inject_credentials, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleep = mock.patch.object(inject_credentials.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def urlopen(self, request, timeout):
        self.requests.append((request.full_url, json.loads(request.data), dict(request.header_items())))
        if isinstance(self.answer, Exception):
            raise self.answer
        return io.BytesIO(json.dumps(self.answer).encode())

    def write(self, expires_in_ms, access='access-1', **extra):
        self.path.write_text(json.dumps({
            'claudeAiOauth': {'accessToken': access, 'refreshToken': 'refresh-1', 'expiresAt': now_ms() + expires_in_ms,
                              'scopes': SCOPES, 'subscriptionType': 'max', 'rateLimitTier': 'tier', **extra},
            'mcpOAuth': {'server': {'accessToken': 'mcp-token'}}}))
        os.chmod(self.path, 0o600)

    def saved(self):
        return json.loads(self.path.read_text())

    def test_fresh_token_is_used_as_is(self):
        self.write(60 * 60 * 1000)
        self.assertEqual(inject_credentials.Login(self.path).current(), 'access-1')
        self.assertEqual(self.requests, [])

    def test_expiring_token_is_refreshed_and_written_back(self):
        self.write(60 * 1000, clientId='client-from-file')
        login = inject_credentials.Login(self.path)
        self.assertEqual(login.current(), 'access-2')
        [(url, body, headers)] = self.requests
        self.assertEqual(url, 'https://platform.claude.com/v1/oauth/token')
        self.assertEqual(body, {'grant_type': 'refresh_token', 'refresh_token': 'refresh-1',
                                'client_id': 'client-from-file', 'scope': ' '.join(SCOPES)})
        saved = self.saved()
        self.assertEqual(saved['claudeAiOauth']['accessToken'], 'access-2')
        self.assertEqual(saved['claudeAiOauth']['refreshToken'], 'refresh-2')
        self.assertGreater(saved['claudeAiOauth']['expiresAt'], now_ms() + 50 * 60 * 1000)
        # Everything else is kept.
        self.assertEqual(saved['claudeAiOauth']['scopes'], SCOPES)
        self.assertEqual(saved['mcpOAuth'], {'server': {'accessToken': 'mcp-token'}})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ['.credentials.json'])
        self.assertFalse(Path(str(self.home) + '.lock').exists())
        # The next call uses the new token without another request.
        self.assertEqual(login.current(), 'access-2')
        self.assertEqual(len(self.requests), 1)

    def test_default_client_and_kept_refresh_token(self):
        self.write(60 * 1000)
        self.answer = {'access_token': 'access-2', 'expires_in': 3600}
        inject_credentials.Login(self.path).current()
        self.assertEqual(self.requests[0][1]['client_id'], '9d1c250a-e61b-44d9-88ed-5944d1962f5e')
        self.assertEqual(self.saved()['claudeAiOauth']['refreshToken'], 'refresh-1')

    def test_token_refreshed_by_the_host_is_adopted(self):
        self.write(60 * 60 * 1000)
        login = inject_credentials.Login(self.path)
        self.write(2 * 60 * 60 * 1000, access='access-from-host')
        self.assertEqual(login.current(), 'access-from-host')
        self.assertEqual(self.requests, [])

    def test_refresh_under_the_lock_rereads_the_file(self):
        self.write(60 * 1000)
        login = inject_credentials.Login(self.path)
        real = inject_credentials.Lock

        def lock_then_host_refreshes(path):
            taken = real(path)
            if path.name == '.oauth_refresh.lock':
                self.write(2 * 60 * 60 * 1000, access='access-from-host')
            return taken
        with mock.patch.object(inject_credentials, 'Lock', side_effect=lock_then_host_refreshes):
            self.assertEqual(login.current(), 'access-from-host')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.saved()['claudeAiOauth']['accessToken'], 'access-from-host')

    def test_held_lock_is_waited_for_then_the_host_token_adopted(self):
        self.write(60 * 1000)
        login = inject_credentials.Login(self.path)
        (self.home / '.oauth_refresh.lock').mkdir()

        def host_refreshes(seconds):
            self.write(2 * 60 * 60 * 1000, access='access-from-host')
        with mock.patch.object(inject_credentials.time, 'sleep', side_effect=host_refreshes):
            self.assertEqual(login.current(), 'access-from-host')
        self.assertEqual(self.requests, [])
        self.assertTrue((self.home / '.oauth_refresh.lock').exists(), 'the other holder keeps its lock')

    def test_stale_lock_is_taken_over(self):
        self.write(60 * 1000)
        lock = self.home / '.oauth_refresh.lock'
        lock.mkdir()
        old = time.time() - 120
        os.utime(lock, (old, old))
        self.assertEqual(inject_credentials.Login(self.path).current(), 'access-2')
        self.assertFalse(lock.exists())

    def test_lock_held_throughout_fails_and_keeps_the_token(self):
        self.write(60 * 1000)
        (self.home / '.oauth_refresh.lock').mkdir()
        login = inject_credentials.Login(self.path)
        with mock.patch.object(inject_credentials, 'log') as log:
            self.assertEqual(login.current(), 'access-1')
        self.assertIn('locks stayed held', str(log.call_args))
        self.assertEqual(self.requests, [])

    def test_failed_refresh_keeps_the_token_and_retries_later(self):
        self.write(60 * 1000)
        self.answer = OSError('network down')
        login = inject_credentials.Login(self.path)
        with mock.patch.object(inject_credentials, 'log'):
            self.assertEqual(login.current(), 'access-1')
            self.assertEqual(login.current(), 'access-1')
        self.assertEqual(len(self.requests), 1, 'no retry before REFRESH_RETRY_SECONDS')
        self.assertEqual(self.saved()['claudeAiOauth']['accessToken'], 'access-1')
        self.assertFalse((self.home / '.oauth_refresh.lock').exists())
        login.retry_at = 0
        self.answer = {'access_token': 'access-2', 'expires_in': 3600}
        self.assertEqual(login.current(), 'access-2')

    def test_missing_login(self):
        for content in [None, '{}', '{"claudeAiOauth": {"accessToken": "a"}}', 'not json']:
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(content)
                with self.assertRaises(inject_credentials.Unavailable):
                    inject_credentials.Login(self.path)

    def test_settings_for_the_agent(self):
        self.write(60 * 60 * 1000)
        self.assertEqual(inject_credentials.Login(self.path).settings(), {'env': {
            'CLAUDE_CODE_OAUTH_SCOPES': ' '.join(SCOPES), 'CLAUDE_CODE_SUBSCRIPTION_TYPE': 'max',
            'CLAUDE_CODE_RATE_LIMIT_TIER': 'tier'}})
        self.assertIsNone(inject_credentials.ApiKey('key').settings())

    def test_lock_is_renewed_while_held(self):
        path = self.home / 'renewed.lock'
        lock = inject_credentials.Lock(path)
        old = time.time() - 120
        os.utime(path, (old, old))
        deadline = time.monotonic() + 2
        while path.stat().st_mtime < time.time() - 60 and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        self.assertGreater(path.stat().st_mtime, time.time() - 60)
        lock.release()
        self.assertFalse(path.exists())


class ConfigTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'credentials.toml'
        self.root = Path(directory.name) / 'src/acme-api'
        self.root.mkdir(parents=True)
        for name, value in [('CONFIG', self.config), ('OLD_CONFIG', self.config.with_name('credentials')),
                            ('ROOT', self.root)]:
            patcher = mock.patch.object(inject_credentials, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self, text=None, names=None, **environment):
        if text is not None:
            self.config.write_text(text)
        with mock.patch.dict(os.environ, {'PATH': os.environ['PATH'], **environment}, clear=True):
            return inject_credentials.config(names)

    def test_one_claude_credential_defaulting_to_the_login(self):
        self.assertEqual(self.load(HOME='/home/u')['CLAUDE_CREDENTIALS_FILE'], '/home/u/.claude/.credentials.json')
        self.assertEqual(self.load(CLAUDE_CONFIG_DIR='/elsewhere')['CLAUDE_CREDENTIALS_FILE'], '/elsewhere/.credentials.json')
        self.assertEqual(self.load(CLAUDE_CREDENTIALS_FILE='login')['CLAUDE_CREDENTIALS_FILE'], 'login')
        values = self.load(ANTHROPIC_API_KEY='k')
        self.assertEqual((values['ANTHROPIC_API_KEY'], values['CLAUDE_CREDENTIALS_FILE']), ('k', ''))
        with self.assertRaises(inject_credentials.Unavailable):
            self.load(ANTHROPIC_API_KEY='k', CLAUDE_CREDENTIALS_FILE='login')

    def test_claude_credential_from_the_file(self):
        values = self.load('[claude]\napi_key = { command = "echo file-key" }\n')
        self.assertEqual((values['ANTHROPIC_API_KEY'], values['CLAUDE_CREDENTIALS_FILE']), ('file-key', ''))
        values = self.load('[claude]\ncredentials_file = "~/other/.credentials.json"\n', HOME='/home/u')
        self.assertEqual(values['CLAUDE_CREDENTIALS_FILE'], '/home/u/other/.credentials.json')
        with self.assertRaisesRegex(inject_credentials.Unavailable, 'not both'):
            self.load('[claude]\napi_key = "k"\ncredentials_file = "f"\n')

    def test_environment_claude_credential_replaces_the_files(self):
        values = self.load('[claude]\napi_key = "file-key"\n[secrets]\nGITHUB_TOKEN = "gh-file"\n',
                           CLAUDE_CREDENTIALS_FILE='login')
        self.assertEqual((values['ANTHROPIC_API_KEY'], values['CLAUDE_CREDENTIALS_FILE']), ('', 'login'))
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'gh-file'})

    def test_secrets_are_strings_or_what_commands_print(self):
        values = self.load('[secrets]\n'
                           'GITHUB_TOKEN = { command = "echo pat-from-command" }\n'
                           'NOTION_API_KEY = "secret with spaces"\n'
                           'MULTILINE = { command = "printf \'a\\\\nb\\\\n\\\\n\'" }\n'
                           'HOST_SECRET_COPY = { command = "printf %s \\"$HOST_SECRET\\"" }\n',
                           HOST_SECRET='from-the-environment')
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'pat-from-command', 'NOTION_API_KEY': 'secret with spaces',
                                             'MULTILINE': 'a\nb\n', 'HOST_SECRET_COPY': 'from-the-environment'})

    def test_only_the_named_secrets_run(self):
        marker = self.config.with_name('ran')
        values = self.load(f'[secrets]\nGITHUB_TOKEN = "gh"\nOTHER = {{ command = "touch {marker}" }}\n',
                           names=['GITHUB_TOKEN', 'REDMINE_API_KEY'])
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'gh'})
        self.assertFalse(marker.exists())

    def test_workspaces_override_by_path(self):
        text = ('[claude]\napi_key = "personal"\n'
                '[secrets]\nGITHUB_TOKEN = "personal-gh"\nNOTION_API_KEY = "notion"\n'
                f'[workspaces."{self.root.parent}/acme-*".secrets]\nGITHUB_TOKEN = "acme-gh"\nJIRA_TOKEN = "jira"\n'
                f'[workspaces."{self.root}".claude]\ncredentials_file = "/acme/login.json"\n'
                f'[workspaces."{self.root.parent}/other".secrets]\nGITHUB_TOKEN = "other-gh"\n')
        values = self.load(text)
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'acme-gh', 'NOTION_API_KEY': 'notion', 'JIRA_TOKEN': 'jira'})
        self.assertEqual((values['ANTHROPIC_API_KEY'], values['CLAUDE_CREDENTIALS_FILE']), ('', '/acme/login.json'))
        with mock.patch.object(inject_credentials, 'ROOT', self.root.parent / 'other'):
            values = self.load()
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'other-gh', 'NOTION_API_KEY': 'notion'})
        self.assertEqual(values['ANTHROPIC_API_KEY'], 'personal')

    def test_workspace_paths_expand_the_home(self):
        values = self.load('[workspaces."~/src/acme-*".secrets]\nGITHUB_TOKEN = "acme-gh"\n',
                           HOME=str(self.root.parents[1]))
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'acme-gh'})

    def test_a_failed_command_leaves_only_its_secret_out(self):
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            values = self.load('[secrets]\nGITHUB_TOKEN = "gh"\nBROKEN = { command = "exit 3" }\n')
        self.assertEqual(values['secrets'], {'GITHUB_TOKEN': 'gh'})
        self.assertIn('secrets.BROKEN: the command failed with exit code 3', stderr.getvalue())
        with self.assertRaisesRegex(inject_credentials.Unavailable, 'claude.api_key: the command failed'):
            self.load('[claude]\napi_key = { command = "false" }\n')

    def test_invalid_files_name_the_setting(self):
        for text, message in [('[secrets]\nlowercase = "x"\n', r'secrets.lowercase: unknown'),
                              ('[secrets]\nA = 1\n', r'secrets.A: expected a string'),
                              ('[secrets]\nA = { cmd = "x" }\n', r'secrets.A: expected a string'),
                              ('[claude]\ntoken = "x"\n', r'claude.token: unknown'),
                              ('[other]\n', r'expected \[claude\] and \[secrets\]'),
                              ('[workspaces."src/*".secrets]\nA = "x"\n', r'workspaces."src/\*": expected an absolute'),
                              ('[workspaces."/src".other]\n', r'workspaces."/src".: expected'),
                              ('[secrets\n', r'credentials.toml: ')]:
            with self.subTest(text=text), self.assertRaisesRegex(inject_credentials.Unavailable, message):
                self.load(text)

    def test_the_old_shell_file_is_not_read(self):
        self.config.with_name('credentials').write_text('GITHUB_TOKEN=old\n')
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            values = self.load(HOME='/home/u')
        self.assertEqual(values['secrets'], {})
        self.assertIn('no longer read', stderr.getvalue())

    def test_a_setup_token_is_ignored_with_a_warning(self):
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            values = self.load(HOME='/home/u', CLAUDE_CODE_OAUTH_TOKEN='setup-token')
        self.assertEqual(values['CLAUDE_CREDENTIALS_FILE'], '/home/u/.claude/.credentials.json')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', stderr.getvalue())
        self.assertNotIn('setup-token\n', stderr.getvalue())

    def test_snapshot_sends_only_the_requested_secrets(self):
        values = self.load('[claude]\napi_key = "t"\n[secrets]\nGITHUB_TOKEN = "pat"\nNOTION_API_KEY = "n"\n')
        source = inject_credentials.ApiKey('t')
        with mock.patch.object(inject_credentials, 'log') as log:
            snapshot = json.loads(inject_credentials.snapshot(values, source, ['GITHUB_TOKEN', 'REDMINE_API_KEY']))
        self.assertEqual(snapshot, {'anthropic_token': 't', 'auth_mode': 'api-key', 'secrets': {'GITHUB_TOKEN': 'pat'}})
        self.assertIn('REDMINE_API_KEY', str(log.call_args))
        self.assertNotIn('pat', str(log.call_args))

    def test_generated_secrets_are_the_ones_proxy_json_injects(self):
        generated = self.root / '.devcontainer/.generated'
        self.assertIsNone(inject_credentials.generated_secrets(self.root))
        generated.mkdir(parents=True)
        (generated / 'proxy.json').write_text(json.dumps({'hosts': {
            'api.github.com': {'routes': ['/'], 'inject': {'Authorization': 'Bearer ${GITHUB_TOKEN}'}},
            'fonts.googleapis.com': {'routes': ['/']}}}))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLAUDE_DEVCONTAINER_CONFIG_DIR', None)
            self.assertEqual(inject_credentials.generated_secrets(self.root), ['GITHUB_TOKEN'])


class WatchTest(unittest.TestCase):
    """How the detached watcher waits for the environment that `up` starts."""

    def setUp(self):
        self.clock = 0
        # (seconds since the start, (agent, proxy)) in order; the last one reached is running.
        self.timeline = []
        self.injected = []
        self.configured = []
        self.logged = []
        source = mock.Mock(mode='oauth', **{'current.return_value': 'token', 'settings.return_value': None})
        for name, value in [('take_over', lambda: None), ('claude', lambda values: source),
                            ('find_environment', self.environment), ('requested_secrets', lambda proxy: []),
                            ('inject', lambda proxy, data: self.injected.append(proxy)),
                            ('install_settings', lambda agent, settings: self.configured.append(agent)),
                            ('log', lambda *message: self.logged.append(' '.join(message)))]:
            patcher = mock.patch.object(inject_credentials, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in [('monotonic', lambda: self.clock), ('sleep', self.sleep)]:
            patcher = mock.patch.object(inject_credentials.time, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.clock += seconds

    def environment(self):
        running = [ids for start, ids in self.timeline if start <= self.clock]
        return running[-1] if running else (None, None)

    def test_waits_for_a_proxy_that_starts_after_a_slow_build(self):
        # `up` builds the images after initializeCommand starts the watcher.
        self.timeline = [(90, ('agent', 'proxy')), (200, (None, None))]
        inject_credentials.watch({'secrets': {}})
        self.assertEqual(self.injected, ['proxy'])

    def test_configures_the_agent_that_replaces_a_running_one(self):
        # --remove-existing-container removes the agent but not the proxy, and
        # builds the images before it starts the new agent.
        self.timeline = [(0, ('old', 'proxy')), (10, (None, None)), (150, ('new', 'proxy')), (700, (None, None))]
        inject_credentials.watch({'secrets': {}})
        self.assertEqual(self.configured, ['old', 'new'])

    def test_logs_each_agent_it_configures(self):
        self.timeline = [(0, ('old', 'proxy')), (10, (None, None)), (150, ('new', 'proxy')), (700, (None, None))]
        inject_credentials.watch({'secrets': {}})
        self.assertTrue(any('new' in line for line in self.logged), self.logged)

    def test_stops_soon_after_the_environment_stops(self):
        stopped = inject_credentials.START_SECONDS + 100
        self.timeline = [(0, ('agent', 'proxy')), (stopped, (None, None))]
        inject_credentials.watch({'secrets': {}})
        self.assertEqual(self.injected, ['proxy'])
        self.assertLessEqual(self.clock, stopped + inject_credentials.WAIT_SECONDS + inject_credentials.POLL_SECONDS)


if __name__ == '__main__':
    unittest.main()
