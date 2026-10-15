"""Real workbench, webview, Simple Browser and Ports in headless Electron 44.

Set CODE_SERVER_BROWSER_BINARY to code-server's executable and
ELECTRON_SHELL_TEST_ELECTRON to Electron 44. Requires Docker and the built
CODE_SERVER_PROXY_TEST_IMAGE (defaults to claude-code-server-proxy:test).
Uses local ports 18000 and 3000; all state and Docker resources are disposable.
"""
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from urllib.parse import urlsplit

from electron_shell_test import electron_executable, SHELL, FIXTURES


class Preview(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.paths.append(self.path)
        self.send_response(200)
        self.send_header('Content-Type', 'text/javascript' if self.path == '/asset.js' else 'text/html')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        if self.path == '/asset.js':
            self.wfile.write(b'document.body.append("Asset loaded")')
        else:
            self.wfile.write(b'<h1>Migration preview loaded</h1><script src="/asset.js"></script>')

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get('CODE_SERVER_BROWSER_BINARY') and electron_executable(),
                     'set CODE_SERVER_BROWSER_BINARY and ELECTRON_SHELL_TEST_ELECTRON')
class BrowserTest(unittest.TestCase):
    def test_workbench_webview_preview_and_ports(self):
        with tempfile.TemporaryDirectory(prefix='editor-browser-') as directory:
            self.run_browser(Path(directory))

    def run_browser(self, tmp):
        workspace = tmp / 'workspace'
        workspace.mkdir()
        extension = tmp / 'extensions' / 'migration-probe'
        extension.mkdir(parents=True)
        (extension / 'package.json').write_text(json.dumps({
            'name': 'migration-probe', 'publisher': 'test', 'version': '1.0.0',
            'engines': {'vscode': '^1.70.0'}, 'main': './extension.js',
            'activationEvents': ['onStartupFinished'], 'extensionKind': ['workspace'],
        }))
        (extension / 'extension.js').write_text((FIXTURES / 'editor_probe_extension.js').read_text())
        machine = tmp / 'data' / 'Machine'
        machine.mkdir(parents=True)
        (machine / 'settings.json').write_text(json.dumps({
            'workbench.startupEditor': 'none', 'remote.autoForwardPorts': False,
        }))
        app = tmp / 'shell'
        app.mkdir()
        (app / 'package.json').write_text('{"main":"main.js"}')
        (app / 'main.js').write_text((FIXTURES / 'editor_probe_shell.js').read_text())
        token = secrets.token_hex(32)
        result = tmp / 'result.json'
        frames_file = tmp / 'frames.json'
        env = dict(os.environ, HOME=str(tmp), XDG_CONFIG_HOME=str(tmp / 'config'),
                   HASHED_PASSWORD=token, EDITOR_PROBE_RESULT=str(result),
                   EDITOR_PROBE_DIRECT=str(tmp / 'direct-preview'),
                   EDITOR_PROBE_FRAMES=str(frames_file), EDITOR_PROBE_SHELL=str(SHELL / 'main.js'),
                   CLAUDE_DEVCONTAINER_VSCODE_TOKEN=token)
        # Match the shipped server's flags without depending on /home/node paths.
        server_log = open(tmp / 'server.log', 'w+')
        browser_log = open(tmp / 'browser.log', 'w+')
        processes = []
        proxy_name = 'editor-browser-' + uuid.uuid4().hex[:10]
        preview = http.server.ThreadingHTTPServer(('127.0.0.1', 3000), Preview)
        preview.paths = []
        threading.Thread(target=preview.serve_forever, daemon=True).start()
        try:
            processes.append(subprocess.Popen([
                os.environ['CODE_SERVER_BROWSER_BINARY'], '--auth', 'password', '--bind-addr', '127.0.0.1:18000',
                '--disable-workspace-trust', '--disable-telemetry', '--disable-update-check',
                '--disable-getting-started-override', '--user-data-dir', str(tmp / 'data'),
                '--extensions-dir', str(tmp / 'extensions'), str(workspace),
            ], env=env, stdout=server_log, stderr=subprocess.STDOUT))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                proxy_port = sock.getsockname()[1]
            subprocess.run([
                'docker', 'run', '-d', '--name', proxy_name, '--network', 'host',
                '--add-host', 'agent-code-server:127.0.0.1', '--entrypoint', 'mitmdump',
                os.environ.get('CODE_SERVER_PROXY_TEST_IMAGE', 'claude-code-server-proxy:test'),
                '--listen-host', '127.0.0.1', '--listen-port', str(proxy_port),
                '--set', 'connection_strategy=lazy', '--set', 'websocket=false',
                '-s', '/opt/code-server-proxy.py',
            ], check=True, capture_output=True)
            for port in (18000, proxy_port):
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=1):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            self.fail(f'port {port} did not start')
                        time.sleep(.2)
            processes.append(subprocess.Popen([
                electron_executable(), str(app), f'--proxy-port={proxy_port}', f'--folder={workspace}',
                f'--user-data-dir={tmp / "profile"}', '--ozone-platform=headless', '--disable-gpu',
            ], env=env, stdout=browser_log, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 90
            state, frames = {}, []
            app_visible = ports_visible = False
            while time.monotonic() < deadline:
                try:
                    state = json.loads(result.read_text())
                    frames = json.loads(frames_file.read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(.2)
                    continue
                if state.get('error'):
                    break
                app_visible = any(urlsplit(f['url']).netloc == 'localhost:3000'
                                  and 'forwarded=1' in f['url'] and 'Asset loaded' in f['text'] for f in frames)
                ports_visible = any('3000' in f['text'] and 'Forwarded Address' in f['text'] for f in frames)
                if state.get('commands') and app_visible and ports_visible:
                    break
                time.sleep(.2)
            server_log.seek(0)
            browser_log.seek(0)
            evidence = {'extension': state, 'frames': frames,
                        'server': server_log.read()[-4000:], 'browser': browser_log.read()[-4000:]}
            self.assertTrue(state.get('connected'), evidence)
            self.assertTrue(state.get('webview'), evidence)
            self.assertNotIn('error', state, evidence)
            self.assertEqual(state.get('externalUri'), 'http://localhost:18000/proxy/3000/', evidence)
            self.assertTrue(app_visible, evidence)
            self.assertTrue(ports_visible, evidence)
            port_links = [link['text'] for frame in frames for link in frame['links']]
            self.assertIn('http://localhost:18000/proxy/3000/', port_links, evidence)
            self.assertTrue((tmp / 'direct-preview').is_file())
            self.assertIn('/asset.js', preview.paths)
            logs = subprocess.check_output(['docker', 'logs', proxy_name], text=True, stderr=subprocess.STDOUT)
            self.assertIn('/proxy/3000/', logs)
            print('Verified: workbench connection, webview message, Simple Browser app and asset, Ports 3000, proxy redirect.')
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            subprocess.run(['docker', 'rm', '-f', proxy_name], capture_output=True)
            preview.shutdown()
            preview.server_close()
            server_log.close()
            browser_log.close()


if __name__ == '__main__':
    unittest.main()
