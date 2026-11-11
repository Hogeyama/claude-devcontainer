"""The editor's Electron shell (code-server/shell/): its URL policy with Node, and its enforcement in Electron.

The Electron run needs Electron 44 for Linux: ELECTRON_SHELL_TEST_ELECTRON
names its executable, or the cache of code-server/electron.py has it. The
shell loads a probe page (fixtures/shell_probe.html) as the IDE from a
recording proxy that stands in for code-server-proxy, and the page tries
every way out it can. What the shell lets out reaches the proxy, or a
listener on the host's loopback that nothing may reach.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / '.devcontainer/claude/code-server/shell'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
TOKEN = 'probe-token'
LOGIN_AUTHORIZATION = 'Basic ' + base64.b64encode('user:pa ss'.encode()).decode()


def electron_executable():
    if os.environ.get('ELECTRON_SHELL_TEST_ELECTRON'):
        return os.environ['ELECTRON_SHELL_TEST_ELECTRON']
    spec = importlib.util.spec_from_file_location('electron', ROOT / '.devcontainer/claude/code-server/electron.py')
    electron = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(electron)
    cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'claude-devcontainer/electron'
    path = cache / f'{electron.VERSION}-{electron.linux_platform()}/electron'
    return str(path) if path.is_file() else None


class PolicyTest(unittest.TestCase):
    """policy.js, which main.js applies to every navigation, request and permission."""

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_native_paste_shortcuts(self):
        script = r"""
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { installPasteShortcut } = require(process.argv[1]);
const contents = new EventEmitter();
let pastes = 0;
contents.paste = () => { pastes++; };
installPasteShortcut(contents);
for (const [input, expected] of [
  [{ key: 'v', control: true }, true],
  [{ key: 'V', control: true, shift: true }, true],
  [{ key: 'Insert', shift: true }, true],
  [{ key: 'v' }, false],
  [{ key: 'v', control: true, alt: true }, false],
  [{ key: 'v', control: true, meta: true }, false],
  [{ key: 'Insert', control: true, shift: true }, false],
  [{ key: 'c', control: true }, false],
  [{ key: 'v', control: true, type: 'keyUp' }, false],
]) {
  const before = pastes;
  let prevented = false;
  contents.emit('before-input-event', { preventDefault() { prevented = true; } },
    { type: 'keyDown', ...input });
  assert.equal(prevented, expected, JSON.stringify(input));
  assert.equal(pastes - before, Number(expected), JSON.stringify(input));
}
// A page's synthetic DOM event is not an Electron before-input-event.
contents.emit('keydown', { key: 'v', ctrlKey: true });
assert.equal(pastes, 3);
"""
        subprocess.run(['node', '-e', script, str(SHELL / 'paste.js')],
                       capture_output=True, text=True, check=True)

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_policy(self):
        cases = {
            'allowsNavigation': {
                'http://localhost:18000/?folder=/workspace': True, 'http://localhost:3000/': True,
                'http://localhost/': True, 'about:blank': True, 'about:srcdoc': True,
                'blob:http://localhost:18001/0b9f': True,
                'http://example.com/': False, 'https://localhost:3000/': False, 'http://127.0.0.1:3000/': False,
                'http://[::1]:3000/': False, 'http://app.localhost:3000/': False, 'http://localhost.:3000/': False,
                'http://user:pass@localhost:3000/': False, 'http://localhost:0/': False,
                'ws://localhost:18000/': False, 'vscode://x': False, 'ms-settings:': False, 'mailto:a@b': False,
                'file:///etc/passwd': False, 'data:text/html,x': False, 'javascript:alert(1)': False,
                'blob:http://example.com/0b9f': False, 'blob:null/0b9f': False, 'not a url': False,
            },
            'allowsRequest': {
                'http://localhost:18001/x': True, 'ws://localhost:18000/x': True, 'data:image/png;base64,': True,
                'blob:http://localhost:18000/0b9f': True,
                'wss://localhost:18000/': False, 'ws://example.com/': False, 'http://127.0.0.1:18000/': False,
                'https://file+.vscode-resource.vscode-cdn.net/x': False, 'file:///x': False,
            },
            'isServer': {
                'http://localhost:18000/': True, 'ws://localhost:18000/x': True, 'http://localhost:18001/x': False,
                'http://localhost:3000/': False, 'http://example.com:18000/': False, 'http://127.0.0.1:18001/': False,
            },
            'allowsPermission': {
                'clipboard-sanitized-write': True, 'clipboard-read': False, 'notifications': False,
                'openExternal': False, 'media': False, 'geolocation': False, 'fullscreen': False, 'hid': False,
            },
        }
        script = f'''
const policy = require({json.dumps(str(SHELL / 'policy.js'))});
const cases = {json.dumps(cases)};
const got = {{}};
for (const [name, inputs] of Object.entries(cases)) {{
  got[name] = Object.fromEntries(Object.keys(inputs).map((input) => [input, policy[name](input)]));
}}
got.allowsRedirect = [
  policy.allowsRedirect('/check', 'http://localhost:3000/login'),
  policy.allowsRedirect('//example.com/', 'http://localhost:3000/'),
  policy.allowsRedirect('http://localhost:18000/', 'http://localhost:3000/'),
  policy.allowsRedirect('http://[', 'http://localhost:3000/'),
];
got.allowsLogin = [
  ['http://localhost:3000/x', {{ isProxy: false, scheme: 'basic', realm: 'r' }}],
  ['http://localhost:18000/x', {{ isProxy: false, scheme: 'Digest' }}],
  ['http://localhost:3000/x', {{ isProxy: true, scheme: 'basic' }}],
  ['http://localhost:3000/x', {{ isProxy: false, scheme: 'ntlm' }}],
  ['http://localhost:3000/x', {{ isProxy: false, scheme: 'negotiate' }}],
  ['http://example.com/', {{ isProxy: false, scheme: 'basic' }}],
  ['ws://localhost:3000/', {{ isProxy: false, scheme: 'basic' }}],
  ['http://localhost:3000/x', null],
].map(([url, info]) => policy.allowsLogin(url, info));
got.loginOrigin = policy.loginOrigin('http://localhost:3000/a/b?c');
got.filePath = [
  ['file:///C:/Users/a%20b/shell/login.html?origin=x#y', true],
  ['file://server/share/login.html', true],
  ['file:///home/a%20b/shell/login.html?origin=x', false],
  ['file://server/share/login.html', false],
  ['http://localhost:3000/login.html', false],
  ['not a url', true],
].map(([url, windows]) => policy.filePath(url, windows));
got.ideUrl = policy.ideUrl('/workspace');
console.log(JSON.stringify(got));
'''
        got = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
        for name, inputs in cases.items():
            self.assertEqual(got[name], inputs, name)
        self.assertEqual(got['allowsRedirect'], [True, False, True, False])
        self.assertEqual(got['allowsLogin'], [True, True, False, False, False, False, False, False])
        self.assertEqual(got['loginOrigin'], 'http://localhost:3000')
        self.assertEqual(got['filePath'], ['C:\\Users\\a b\\shell\\login.html', '\\\\server\\share\\login.html',
                                           '/home/a b/shell/login.html', None, None, None])
        self.assertEqual(got['ideUrl'], 'http://localhost:18000/?folder=%2Fworkspace')


class Recorder(socketserver.ThreadingTCPServer):
    """code-server-proxy's stand-in: it answers for localhost itself, and records every request."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, canary, page='shell_probe.html'):
        self.canary = canary
        self.page = page
        self.requests = []
        self.cookies = {}
        self.authorization = {}
        self.reports = {}
        self.done = threading.Event()
        super().__init__(('127.0.0.1', 0), Handler)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode('latin-1').strip()
        if not line:
            return
        headers = {}
        while (header := self.rfile.readline().decode('latin-1').strip()):
            name, _, value = header.partition(':')
            headers[name.strip().lower()] = value.strip()
        method, target, _ = line.split(' ', 2)
        body = self.rfile.read(int(headers.get('content-length', 0)))
        self.server.requests.append((method, target))
        self.server.cookies[target] = headers.get('cookie', '')
        self.server.authorization.setdefault(target, []).append(headers.get('authorization'))
        if method == 'CONNECT':
            self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
            return
        url = urlsplit(target)
        if url.hostname != 'localhost':
            return self.reply(451, b'blocked')
        if url.port == 18000:
            return self.ide(method, url, body)
        if url.path == '/secret':
            # An app behind Basic authentication, whose realm is HTML.
            if headers.get('authorization') == LOGIN_AUTHORIZATION:
                return self.reply(200, b'secret')
            return self.reply(401, b'', {'WWW-Authenticate': 'Basic realm="<b>probe</b>"'})
        if url.path == '/proxy-auth':
            return self.reply(407, b'', {'Proxy-Authenticate': 'Basic realm="proxy"'})
        return self.reply(200, b'app', {'Access-Control-Allow-Origin': '*'})

    def ide(self, method, url, body):
        if method == 'POST':
            self.server.reports[url.path] = json.loads(body)
            if url.path == '/done':
                self.server.done.set()
            return self.reply(204, b'')
        if url.path == '/':
            page = (FIXTURES / self.server.page).read_text().replace('__CANARY__', str(self.server.canary))
            return self.reply(200, page.encode(), {'Content-Type': 'text/html'})
        if url.path in ('/worker.js', '/sw.js'):
            name = 'shell_probe_worker.js' if url.path == '/worker.js' else 'shell_probe_sw.js'
            return self.reply(200, (FIXTURES / name).read_bytes(), {'Content-Type': 'text/javascript'})
        if url.path == '/ok':
            return self.reply(200, b'webview')
        if url.path == '/redirect-out':
            return self.reply(302, b'', {'Location': 'http://example.com/redirected'})
        if url.path == '/download':
            return self.reply(200, b'probe', {'Content-Disposition': 'attachment; filename=probe.txt'})
        return self.reply(404, b'')

    def reply(self, status, body, headers=None):
        lines = [f'HTTP/1.1 {status} X', f'Content-Length: {len(body)}', 'Connection: close']
        lines += [f'{name}: {value}' for name, value in (headers or {}).items()]
        self.wfile.write(('\r\n'.join(lines) + '\r\n\r\n').encode() + body)


@unittest.skipUnless(electron_executable(), 'no Electron: set ELECTRON_SHELL_TEST_ELECTRON or run code-server.sh prepare')
class EnforcementTest(unittest.TestCase):
    def test_probe_page_reaches_nothing_but_the_agent(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        canary = socket.create_server(('127.0.0.1', 0))
        self.addCleanup(canary.close)
        canary.settimeout(0.2)
        recorder = Recorder(canary.getsockname()[1])
        self.addCleanup(recorder.server_close)
        threading.Thread(target=recorder.serve_forever, daemon=True).start()
        self.addCleanup(recorder.shutdown)
        # The shell as it is, but for Chromium's single-instance lock: it
        # needs a Unix socket, which sandboxes such as Claude Code's refuse.
        app = tmp / 'app'
        app.mkdir()
        (app / 'package.json').write_text('{"main": "main.js"}')
        (app / 'main.js').write_text("require('electron').app.requestSingleInstanceLock = () => true;\n"
                                     f'require({json.dumps(str(SHELL / "main.js"))});\n')
        # Anything that opens an external application leaves a marker.
        bin = tmp / 'bin'
        bin.mkdir()
        for name in ('xdg-open', 'gio', 'kde-open', 'gnome-open'):
            (bin / name).write_text(f'#!/bin/sh\necho "$@" >> {tmp}/opened\n')
            (bin / name).chmod(0o755)
        home = tmp / 'home'
        home.mkdir()
        env = {'PATH': f'{bin}:{os.environ["PATH"]}', 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
               'XDG_DOWNLOAD_DIR': str(home / 'Downloads'), 'CLAUDE_DEVCONTAINER_VSCODE_TOKEN': TOKEN}
        log = open(tmp / 'electron.log', 'w')
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [electron_executable(), str(app), f'--proxy-port={recorder.server_address[1]}', '--folder=/workspace',
             f'--user-data-dir={tmp / "profile"}', '--ozone-platform=headless', '--disable-gpu'],
            env=env, stdout=subprocess.DEVNULL, stderr=log)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        connected = []
        deadline = time.monotonic() + 90
        while not recorder.done.is_set() and time.monotonic() < deadline and process.poll() is None:
            try:
                connected.append(canary.accept()[0])
            except TimeoutError:
                pass
        self.assertTrue(recorder.done.is_set(), (tmp / 'electron.log').read_text()[-3000:])
        results = recorder.reports['/report']

        self.assertEqual(recorder.requests[0], ('GET', f'http://localhost:18000/?folder=%2Fworkspace'))
        # The token goes to the server with every request, those of a worker
        # included, and nowhere else.
        for target, cookie in recorder.cookies.items():
            if target.startswith('http://localhost:18000/'):
                self.assertIn(f'code-server-session={TOKEN}', cookie, target)
            else:
                self.assertNotIn('code-server-session', cookie, target)
        self.assertIn('http://localhost:18000/sw.js', recorder.cookies)
        self.assertIn('http://localhost:18000/ok', recorder.cookies)
        self.assertEqual(results['fetch_webviews'], 200)
        targets = [target for method, target in recorder.requests]
        # Only the agent's origins reach the proxy; nothing reaches the host's loopback.
        self.assertEqual([t for t in targets if not t.startswith(('http://localhost:', 'localhost:'))], [])
        self.assertEqual(connected, [])
        self.assertFalse((tmp / 'opened').exists())
        self.assertEqual(results['fetch_app'], 200)
        for name in ('fetch_external', 'fetch_loopback', 'fetch_localhost_subdomain', 'fetch_https'):
            self.assertTrue(str(results[name]).startswith('error:'), (name, results[name]))
        self.assertEqual(results['websocket_external'], 'error')
        self.assertEqual(results['websocket_loopback'], 'error')
        self.assertIn(('CONNECT', 'localhost:3000'), recorder.requests)
        self.assertEqual(results['window_open'], 'null')
        self.assertNotIn('http://localhost:18000/popup', targets)
        self.assertTrue(results['clipboard_read'].startswith('error:'), results['clipboard_read'])
        self.assertEqual(results['notification'], 'denied')
        self.assertTrue(results['geolocation'].startswith('denied'), results['geolocation'])
        self.assertEqual(results['worker'], {'external': 'error:TypeError', 'app': 200})
        self.assertEqual(json.loads(results['service_worker']), {'external': 'error:TypeError', 'app': '200'})
        self.assertEqual([c for c in results['webrtc'] if ' udp ' in c.lower()], [])
        # The allowed frame loads; the redirect out of the agent stops at the agent.
        self.assertIn('http://localhost:3000/frame', targets)
        self.assertIn('http://localhost:18000/redirect-out', targets)
        # Neither the external protocol nor the external page replaced the
        # page, which went on to /done, and the download saved nothing.
        self.assertIn('http://localhost:18000/download', targets)
        self.assertEqual([path.name for path in home.rglob('probe*')], [])



@unittest.skipUnless(electron_executable(), 'no Electron: set ELECTRON_SHELL_TEST_ELECTRON or run code-server.sh prepare')
class LoginTest(unittest.TestCase):
    """login.js: an app's Basic authentication asks the person, in a window that reaches nothing."""

    def test_an_apps_sign_in_is_asked_for_and_the_proxys_cancelled(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        recorder = Recorder(0, page='shell_login.html')
        self.addCleanup(recorder.server_close)
        threading.Thread(target=recorder.serve_forever, daemon=True).start()
        self.addCleanup(recorder.shutdown)
        prompts = tmp / 'prompts.jsonl'
        # The person, in the sign-in window: what it shows, whether it reaches
        # the network, and then the user name and password.
        app = tmp / 'app'
        app.mkdir()
        (app / 'package.json').write_text('{"main": "main.js"}')
        (app / 'main.js').write_text(f"""
const {{ app }} = require('electron');
const fs = require('node:fs');
app.requestSingleInstanceLock = () => true;
app.on('browser-window-created', (event, window) => {{
  window.webContents.once('did-finish-load', async () => {{
    if (!window.getParentWindow()) {{
      return;
    }}
    const shown = await window.webContents.executeJavaScript(`(async () => ({{
      url: location.href, title: document.title,
      origin: document.getElementById('origin').textContent,
      realm: document.getElementById('realm').innerHTML,
      fetch: await fetch('http://localhost:3000/from-prompt').then((r) => r.status, (e) => 'error'),
    }}))()`);
    shown.windowTitle = window.getTitle();
    shown.modal = window.isModal();
    shown.visible = window.isVisible();
    fs.appendFileSync({json.dumps(str(prompts))}, JSON.stringify(shown) + '\\n');
    window.webContents.executeJavaScript(`document.getElementById('username').value = 'user';
      document.getElementById('password').value = 'pa ss';
      document.querySelector('button[type=submit]').click();`);
  }});
}});
require({json.dumps(str(SHELL / 'main.js'))});
""")
        home = tmp / 'home'
        home.mkdir()
        env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
               'CLAUDE_DEVCONTAINER_VSCODE_TOKEN': TOKEN}
        log = open(tmp / 'electron.log', 'w')
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [electron_executable(), str(app), f'--proxy-port={recorder.server_address[1]}', '--folder=/workspace',
             f'--user-data-dir={tmp / "profile"}', '--ozone-platform=headless', '--disable-gpu'],
            env=env, stdout=subprocess.DEVNULL, stderr=log)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertTrue(recorder.done.wait(60), (tmp / 'electron.log').read_text()[-3000:])
        results = recorder.reports['/done']

        shown = [json.loads(line) for line in prompts.read_text().splitlines()]
        # One window for the app; none for the proxy's 407.
        self.assertEqual(len(shown), 1, shown)
        self.assertEqual(shown[0]['origin'], 'http://localhost:3000')
        self.assertEqual(shown[0]['windowTitle'], 'Sign in to http://localhost:3000 [agent]')
        self.assertTrue(shown[0]['modal'])
        self.assertTrue(shown[0]['visible'])
        # The realm is the server's text, not markup.
        self.assertEqual(shown[0]['realm'], '&lt;b&gt;probe&lt;/b&gt;')
        self.assertTrue(shown[0]['url'].startswith('file://'), shown[0]['url'])
        self.assertEqual(shown[0]['fetch'], 'error')
        targets = [target for method, target in recorder.requests]
        self.assertNotIn('http://localhost:3000/from-prompt', targets)
        # The answer went to the app, which the frame then showed.
        self.assertIn(LOGIN_AUTHORIZATION, recorder.authorization['http://localhost:3000/secret'])
        self.assertEqual(results['secret'], 'loaded')
        # The proxy's sign-in is cancelled: nothing is sent again with credentials.
        self.assertEqual([value for value in recorder.authorization.get('http://localhost:3001/proxy-auth', []) if value], [])
        self.assertIn('sign-in cancelled for the proxy', (tmp / 'electron.log').read_text())

if __name__ == '__main__':
    unittest.main()
