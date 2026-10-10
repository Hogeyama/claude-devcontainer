"""Actual Claude Code Bash invocation driven by a local synthetic SSE API."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

requests = []
command = 'set -e; bash /workspace/tests/fixtures/bridge_probe.sh sandbox; bash /workspace/tests/fixtures/sandbox_network_probe.sh; printf "claude-sandbox-ok\\n"; readlink /proc/self/ns/net'

def sse(tool):
    base = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-4-5',
            'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 0}}
    block = {'type': 'tool_use', 'id': 'toolu_fixture', 'name': 'Bash', 'input': {}} if tool else {'type': 'text', 'text': ''}
    delta = {'type': 'input_json_delta', 'partial_json': json.dumps({'command': command, 'description': 'Verify offline Docker bridge'})} if tool else {'type': 'text_delta', 'text': 'fixture-complete'}
    events = [('message_start', {'type': 'message_start', 'message': base}),
              ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': block}),
              ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': delta}),
              ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
              ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if tool else 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 1}}),
              ('message_stop', {'type': 'message_stop'})]
    return ''.join('event: ' + event + '\ndata: ' + json.dumps(data) + '\n\n' for event, data in events).encode()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(body)
        results = [block for message in body.get('messages', []) for block in (message['content'] if isinstance(message['content'], list) else []) if block.get('type') == 'tool_result']
        data = sse(not results)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

with tempfile.TemporaryDirectory(prefix='claude-synthetic-') as temporary:
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A fresh config and explicit dummy auth prevent use of host account state.
    # --bare reads no settings, so it gets the dummy key directly.
    env = {**os.environ, 'ANTHROPIC_API_KEY': 'managed-by-proxy', 'CLAUDE_CONFIG_DIR': temporary + '/config', 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:' + str(server.server_port),
           'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1', 'SHELL': '/bin/bash', 'CLAUDE_CODE_SHELL': '/bin/bash'}
    try:
        result = subprocess.run(['claude', '--bare', '--print', '--model', 'claude-sonnet-4-5', '--tools', 'Bash',
                                 '--no-session-persistence', '--setting-sources', '', '--dangerously-skip-permissions',
                                 '--system-prompt', 'You are a synthetic test client.', 'Run the sandbox probe.'],
                                cwd=temporary, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, (result.stdout, result.stderr)
        results = [block for body in requests for message in body.get('messages', [])
                   for block in (message['content'] if isinstance(message['content'], list) else []) if block.get('type') == 'tool_result']
        assert results, (result.stdout, result.stderr)
        successful = [block for block in results if not block.get('is_error', False) and 'bridge-probe-ok' in json.dumps(block) and 'sandbox-network-probe-ok' in json.dumps(block) and 'claude-sandbox-ok' in json.dumps(block)]
        assert successful, results
        assert os.readlink('/proc/self/ns/net') not in json.dumps(successful), successful
        print('actual-claude-sandbox-and-docker-ok')
    finally:
        server.shutdown()
