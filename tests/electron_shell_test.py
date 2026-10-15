"""The editor's Electron shell (code-server/shell/): its URL policy with Node, and its enforcement in Electron.

The Electron run needs Electron 44 for Linux: ELECTRON_SHELL_TEST_ELECTRON
names its executable, or the cache of code-server/electron.py has it. The
shell loads a probe page (fixtures/shell_probe.html) as the IDE from a
recording proxy that stands in for code-server-proxy, and the page tries
every way out it can. What the shell lets out reaches the proxy, or a
listener on the host's loopback that nothing may reach.
"""
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
                'http://localhost:18000/': True, 'ws://localhost:18000/x': True, 'http://localhost:18001/x': True,
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
got.ideUrl = policy.ideUrl('a&b', '/workspace');
console.log(JSON.stringify(got));
'''
        got = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
        for name, inputs in cases.items():
            self.assertEqual(got[name], inputs, name)
        self.assertEqual(got['allowsRedirect'], [True, False, True, False])
        self.assertEqual(got['ideUrl'], 'http://localhost:18000/?tkn=a%26b&folder=%2Fworkspace')


class Recorder(socketserver.ThreadingTCPServer):
    """code-server-proxy's stand-in: it answers for localhost itself, and records every request."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, canary):
        self.canary = canary
        self.requests = []
        self.cookies = {}
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
        if method == 'CONNECT':
            self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
            return
        url = urlsplit(target)
        if url.hostname != 'localhost':
            return self.reply(451, b'blocked')
        if url.port == 18000:
            return self.ide(method, url, body)
        return self.reply(200, b'app', {'Access-Control-Allow-Origin': '*'})

    def ide(self, method, url, body):
        if method == 'POST':
            self.server.reports[url.path] = json.loads(body)
            if url.path == '/done':
                self.server.done.set()
            return self.reply(204, b'')
        if url.path == '/':
            page = (FIXTURES / 'shell_probe.html').read_text().replace('__CANARY__', str(self.server.canary))
            return self.reply(200, page.encode(), {'Content-Type': 'text/html'})
        if url.path in ('/worker.js', '/sw.js'):
            name = 'shell_probe_worker.js' if url.path == '/worker.js' else 'shell_probe_sw.js'
            return self.reply(200, (FIXTURES / name).read_bytes(), {'Content-Type': 'text/javascript'})
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

        self.assertEqual(recorder.requests[0], ('GET', f'http://localhost:18000/?tkn={TOKEN}&folder=%2Fworkspace'))
        # The token goes to the server with every request, those of a worker
        # included, and nowhere else.
        for target, cookie in recorder.cookies.items():
            if target.startswith(('http://localhost:18000/', 'http://localhost:18001/')):
                self.assertIn(f'vscode-tkn={TOKEN}', cookie, target)
            else:
                self.assertNotIn('vscode-tkn', cookie, target)
        self.assertIn('http://localhost:18000/sw.js', recorder.cookies)
        self.assertIn('http://localhost:18001/ok', recorder.cookies)
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


if __name__ == '__main__':
    unittest.main()
