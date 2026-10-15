"""Exercise the host boundary over real bidirectional JSON-RPC pipes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.devcontainer/claude/scripts/acp-agent.py'
spec = importlib.util.spec_from_file_location('acp_agent', SCRIPT)
acp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acp)


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.client, self.agent = io.BytesIO(), io.BytesIO()
        self.bridge = acp.Bridge(Path('/host/project space'), self.client, self.agent)

    def messages(self, stream):
        result = [json.loads(line) for line in stream.getvalue().splitlines()]
        stream.seek(0)
        stream.truncate()
        return result

    def test_client_capabilities_cannot_enable_host_execution(self):
        for name in ('agent-shell', 'avante.nvim', 'other-client'):
            self.bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': 1, 'clientInfo': {'name': name},
                           '_meta': {'terminal-auth': True},
                           'clientCapabilities': {'fs': {'readTextFile': True, 'writeTextFile': True},
                                                  'terminal': True, 'auth': {'terminal': True}}}})
            message = self.messages(self.agent)[0]
            self.assertEqual(message['params']['clientCapabilities'], {})
            self.assertEqual(message['params']['clientInfo']['name'], name)
            self.assertNotIn('_meta', message['params'])

    def test_host_tool_requests_are_denied_even_without_negotiation(self):
        for method in ('fs/read_text_file', 'fs/write_text_file', 'terminal/create',
                       'terminal/output', 'terminal/release', '_open_url', 'elicitation/create'):
            self.bridge.from_agent({'jsonrpc': '2.0', 'id': 'host-op', 'method': method,
                                    'params': {'path': '/host/secret', 'command': 'touch /host/pwned'}})
            self.assertEqual(self.messages(self.client), [])
            self.assertEqual(self.messages(self.agent)[0]['error']['code'], -32601)

    def test_client_mcp_credentials_never_reach_the_agent(self):
        servers = [
            {'name': 'stdio', 'command': 'gh-mcp', 'args': ['--token', 'SECRET_ARG'],
             'env': [{'name': 'GITHUB_TOKEN', 'value': 'SECRET_ENV'}]},
            {'name': 'http', 'type': 'http', 'url': 'https://example.com/?token=SECRET_URL',
             'headers': [{'name': 'Authorization', 'value': 'Bearer SECRET_HEADER'}]},
            {'name': 'sse', 'type': 'sse', 'url': 'https://example.com/sse',
             'headers': [{'name': 'Authorization', 'value': 'SECRET_SSE'}]},
        ]
        for method in ('session/new', 'session/load', 'session/resume', 'session/fork'):
            with self.subTest(method=method):
                self.bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': method,
                    '_meta': {'token': 'SECRET_TOP_LEVEL'}, 'params': {
                        'sessionId': 's', 'cwd': '/host/project space', 'mcpServers': servers,
                        '_meta': {'claudeCode': {'options': {
                            'mcpServers': {'private': servers[0]},
                            'env': {'ANTHROPIC_API_KEY': 'SECRET_SDK_ENV'}}}}}})
                self.assertNotIn(b'SECRET', self.agent.getvalue())
                message = self.messages(self.agent)[0]
                self.assertEqual(message['params']['mcpServers'], [])
                self.assertEqual(message['params']['cwd'], '/workspace')
                self.assertNotIn('_meta', message)
                self.assertNotIn('_meta', message['params'])

    def test_permissions_and_updates_still_work_but_extensions_do_not(self):
        request = {'jsonrpc': '2.0', 'id': 'allow', 'method': 'session/request_permission',
                   'params': {'sessionId': 's', 'toolCall': {'title': 'Bash',
                              '_meta': {'terminal': {'command': 'evil'}}},
                              'options': [{'optionId': 'yes', 'kind': 'allow_once', 'name': 'Allow'}]}}
        self.bridge.from_agent(request)
        delivered = self.messages(self.client)[0]
        self.assertNotIn('_meta', delivered['params']['toolCall'])
        reply = {'jsonrpc': '2.0', 'id': 'allow',
                 'result': {'outcome': {'outcome': 'selected', 'optionId': 'yes'}}}
        self.bridge.from_client(reply)
        self.assertEqual(self.messages(self.agent), [reply])
        self.bridge.from_client(reply)
        self.assertEqual(self.messages(self.agent), [])
        notification = {'jsonrpc': '2.0', 'method': 'session/update', 'params': {
            'sessionId': 's', 'update': {'sessionUpdate': 'agent_message_chunk',
                                        'content': {'type': 'text', 'text': 'hello'}}}}
        self.bridge.from_agent(notification)
        self.assertEqual(self.messages(self.client), [notification])
        self.bridge.from_agent({'jsonrpc': '2.0', 'method': '_execute', 'params': {}})
        self.assertEqual(self.messages(self.client), [])

    def test_session_and_resource_paths_without_rewriting_prompt_text(self):
        for method in ('session/new', 'session/load', 'session/resume', 'session/list'):
            self.bridge.from_client({'jsonrpc': '2.0', 'id': 4, 'method': method,
                'params': {'cwd': '/host/project space/sub', 'sessionId': 's',
                           'mcpServers': [], 'additionalDirectories': ['/host/project space/other']}})
            params = self.messages(self.agent)[0]['params']
            self.assertEqual(params['cwd'], '/workspace/sub')
            self.assertEqual(params['additionalDirectories'], ['/workspace/other'])
        self.bridge.from_client({'jsonrpc': '2.0', 'id': 5, 'method': 'session/prompt',
            'params': {'sessionId': 's', 'prompt': [
                {'type': 'resource_link', 'uri': 'file:///host/project%20space/a%20b.py', 'name': 'a'},
                {'type': 'text', 'text': 'literal /host/project space'}]}})
        prompt = self.messages(self.agent)[0]['params']['prompt']
        self.assertEqual(prompt[0]['uri'], 'file:///workspace/a%20b.py')
        self.assertEqual(prompt[1]['text'], 'literal /host/project space')
        self.assertEqual(self.bridge.container_path('/workspace/sub'), '/workspace/sub')

    def test_outside_cwd_and_auth_fail_locally(self):
        for path in ('relative', '/host/project space/../secret', '/host/project space-other', '/etc'):
            self.bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': 'session/new',
                                     'params': {'cwd': path}})
            self.assertEqual(self.messages(self.client)[0]['error']['code'], -32602)
            self.assertEqual(self.messages(self.agent), [])
        self.bridge.from_client({'jsonrpc': '2.0', 'id': 2, 'method': 'authenticate', 'params': {}})
        self.assertEqual(self.messages(self.client)[0]['error']['code'], -32602)
        self.assertEqual(self.messages(self.agent), [])

    def test_symlink_workspace_paths_and_resource_uris(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / 'real project'
            (real / 'sub').mkdir(parents=True)
            alias = base / 'alias'
            alias.symlink_to(real, target_is_directory=True)
            for root in (real, alias):
                bridge = acp.Bridge(root, self.client, self.agent)
                for path in (real, alias):
                    bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': 'session/new',
                                        'params': {'cwd': str(path / 'sub'), 'mcpServers': []}})
                    self.assertEqual(self.messages(self.agent)[0]['params']['cwd'], '/workspace/sub')
                    bridge.from_client({'jsonrpc': '2.0', 'id': 2, 'method': 'session/prompt',
                        'params': {'sessionId': 's', 'prompt': [
                            {'type': 'resource_link', 'uri': (path / 'sub/new file').as_uri(), 'name': 'new'}]}})
                    prompt = self.messages(self.agent)[0]['params']['prompt']
                    self.assertEqual(prompt[0]['uri'], 'file:///workspace/sub/new%20file')
                with self.assertRaises(ValueError):
                    bridge.container_path(str(base / 'other'))

    def test_agent_cannot_offer_host_login_commands_or_unsolicited_responses(self):
        self.bridge.pending[1] = 'initialize'
        self.bridge.from_agent({'jsonrpc': '2.0', 'id': 1, 'result': {
            'protocolVersion': 1, 'authMethods': [{'type': 'terminal', 'args': ['--cli']}],
            '_meta': {'terminal-auth': {'command': 'evil'}}}})
        result = self.messages(self.client)[0]['result']
        self.assertEqual(result['authMethods'], [])
        self.assertNotIn('_meta', result)
        self.bridge.from_agent({'jsonrpc': '2.0', 'id': 88, 'result': {}})
        self.assertEqual(self.messages(self.client), [])

    def test_mixed_messages_cannot_bypass_response_filtering(self):
        for method in ('session/update', 'session/request_permission'):
            with self.assertRaises(ValueError):
                acp.validate_message({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                      'params': {}, 'result': {'authMethods': [{'type': 'terminal'}]}})
        for message in ([], {'jsonrpc': '2.0', 'method': None},
                        {'jsonrpc': '2.0', 'id': [], 'result': {}},
                        {'jsonrpc': '2.0', 'id': 1, 'result': {}, 'error': {}}):
            with self.assertRaises(ValueError):
                acp.validate_message(message)

    def test_agent_paths_reach_the_editor_as_host_paths(self):
        update = {'jsonrpc': '2.0', 'method': 'session/update', 'params': {
            'sessionId': 's', 'update': {
                'sessionUpdate': 'tool_call', 'toolCallId': 't', 'title': 'Edit /workspace/a.py',
                'locations': [{'path': '/workspace/a.py', 'line': 3}, {'path': '/workspace/sub/../b.py'}],
                'content': [
                    {'type': 'diff', 'path': '/workspace/a.py', 'oldText': 'x', 'newText': 'y'},
                    {'type': 'content', 'content': {'type': 'resource_link', 'name': 'c',
                                                    'uri': 'file:///workspace/c%20d.py'}},
                    {'type': 'content', 'content': {'type': 'resource', 'resource': {
                        'uri': 'file:///workspace/e.py', 'text': '/workspace/e.py'}}}],
                'rawInput': {'file_path': '/workspace/a.py', 'path': '/workspace'}}}}
        self.bridge.from_agent(update)
        delivered = self.messages(self.client)[0]['params']['update']
        self.assertEqual(delivered['locations'], [{'path': '/host/project space/a.py', 'line': 3},
                                                  {'path': '/host/project space/b.py'}])
        self.assertEqual(delivered['content'][0]['path'], '/host/project space/a.py')
        self.assertEqual(delivered['content'][1]['content']['uri'], 'file:///host/project%20space/c%20d.py')
        self.assertEqual(delivered['content'][2]['content']['resource'],
                         {'uri': 'file:///host/project%20space/e.py', 'text': '/workspace/e.py'})
        # Text and the tool's own input are what the agent wrote.
        self.assertEqual(delivered['title'], 'Edit /workspace/a.py')
        self.assertEqual(delivered['rawInput'], {'file_path': '/workspace/a.py', 'path': '/workspace'})

        self.bridge.from_agent({'jsonrpc': '2.0', 'id': 'p', 'method': 'session/request_permission',
            'params': {'sessionId': 's', 'options': [], 'toolCall': {
                'toolCallId': 't', 'locations': [{'path': '/workspace'}]}}})
        tool = self.messages(self.client)[0]['params']['toolCall']
        self.assertEqual(tool['locations'], [{'path': '/host/project space'}])

        self.bridge.pending[7] = 'session/list'
        self.bridge.from_agent({'jsonrpc': '2.0', 'id': 7, 'result': {'sessions': [
            {'sessionId': 'a', 'cwd': '/workspace/sub'}, {'sessionId': 'b', 'cwd': '/elsewhere'}]}})
        sessions = self.messages(self.client)[0]['result']['sessions']
        self.assertEqual([s['cwd'] for s in sessions], ['/host/project space/sub', '/elsewhere'])

    def test_paths_outside_the_shared_tree_are_not_mapped(self):
        # The agent's .git is its own volume, and other paths are the container's.
        for path in ('/workspace/.git', '/workspace/.git/config', '/workspace/sub/../.git/HEAD',
                     '/workspace/../etc/passwd', '/workspace-other/a', '/home/node/.claude/plan.md',
                     'relative/a.py'):
            with self.subTest(path=path):
                self.bridge.from_agent({'jsonrpc': '2.0', 'method': 'session/update', 'params': {
                    'sessionId': 's', 'update': {'sessionUpdate': 'tool_call_update', 'toolCallId': 't',
                                                'locations': [{'path': path}]}}})
                update = self.messages(self.client)[0]['params']['update']
                self.assertEqual(update['locations'], [{'path': path}])
        self.assertEqual(self.bridge.host_path('/workspace/.gitignore'), '/host/project space/.gitignore')

    def test_client_paths_into_the_hosts_git_are_refused(self):
        for path in ('/host/project space/.git', '/host/project space/.git/hooks', '/workspace/.git'):
            with self.subTest(path=path):
                self.bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': 'session/new',
                                         'params': {'cwd': path, 'mcpServers': []}})
                self.assertEqual(self.messages(self.client)[0]['error']['code'], -32602)
                self.assertEqual(self.messages(self.agent), [])
        uri = 'file:///host/project%20space/.git/COMMIT_EDITMSG'
        self.bridge.from_client({'jsonrpc': '2.0', 'id': 2, 'method': 'session/prompt', 'params': {
            'sessionId': 's', 'prompt': [{'type': 'resource_link', 'uri': uri, 'name': 'msg'}]}})
        self.assertEqual(self.messages(self.agent)[0]['params']['prompt'][0]['uri'], uri)

    def test_optional_list_filter_can_be_null(self):
        self.bridge.from_client({'jsonrpc': '2.0', 'id': 1, 'method': 'session/list',
                                 'params': {'cwd': None}})
        self.assertIsNone(self.messages(self.agent)[0]['params']['cwd'])


class TransportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        docker = self.root / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
args = sys.argv[1:]
if args[0] == 'ps':
    print(os.environ.get('CONTAINERS', 'abc123'))
    sys.exit()
if args[0] == 'inspect':
    # What git-agent.py verifies: the running container's mode and mounts.
    mode = os.environ.get('MODE', 'shared')
    mounts = ([{'Destination': '/workspace', 'Type': 'bind', 'RW': True, 'Source': os.environ['ROOT']},
               {'Destination': '/workspace/.git', 'Type': 'volume', 'RW': True, 'Name': 'p_agent-git'}]
              if mode == 'shared' else
              [{'Destination': '/workspace', 'Type': 'volume', 'RW': True, 'Name': 'p_workspace'}])
    print(json.dumps([{'Config': {'Labels': {'com.docker.compose.project': 'p',
        'dev.claude-devcontainer.git-isolation': mode}}, 'Mounts': mounts}]))
    sys.exit()
assert args == ['exec', '-i', '--user', 'node', '--workdir', '/workspace',
                '--env', 'CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude',
                'abc123', '/usr/local/bin/claude-agent-acp'], args
for line in sys.stdin:
    m = json.loads(line)
    if m.get('method') == 'initialize':
        assert m['params']['clientCapabilities'] == {}
        if 'INVALID_JSON_NUMBER' in os.environ:
            print('{"jsonrpc":"2.0","id":1,"result":{"number":' + os.environ['INVALID_JSON_NUMBER'] + '}}', flush=True)
            sys.exit()
        # A malicious server tries to call back into the host anyway.
        print(json.dumps({'jsonrpc':'2.0','id':'attack','method':'terminal/create','params':{}}), flush=True)
    elif m.get('id') == 'attack':
        assert m['error']['code'] == -32601
        print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':1,'authMethods':[]}}), flush=True)
    elif m.get('method') == 'session/new':
        assert m['params']['cwd'] == '/workspace'
        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'sessionId':'test'}}), flush=True)
    elif m.get('method') == 'session/cancel':
        print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'cancelled':True}}), flush=True)
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                        ROOT=str(self.root.resolve()))

    def test_bidirectional_protocol_cancel_and_eof(self):
        process = subprocess.Popen([sys.executable, str(SCRIPT), '--workspace-folder', str(self.root)],
            env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        selector = selectors.DefaultSelector()
        self.addCleanup(selector.close)
        selector.register(process.stdout, selectors.EVENT_READ)

        def receive():
            self.assertTrue(selector.select(timeout=5), 'bridge did not respond')
            return json.loads(process.stdout.readline())

        acp.send(process.stdin, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                 'params': {'protocolVersion': 1, 'clientCapabilities': {'terminal': True}}})
        self.assertEqual(receive()['result']['protocolVersion'], 1)
        acp.send(process.stdin, {'jsonrpc': '2.0', 'id': 2, 'method': 'session/new',
                                 'params': {'cwd': str(self.root), 'mcpServers': []}})
        self.assertEqual(receive()['result']['sessionId'], 'test')
        acp.send(process.stdin, {'jsonrpc': '2.0', 'method': 'session/cancel', 'params': {'sessionId': 'test'}})
        self.assertTrue(receive()['params']['cancelled'])
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0, process.stderr.read().decode())
        self.assertEqual(process.stdout.read(), b'')
        process.stdout.close()
        process.stderr.close()

    def test_nonfinite_numbers_are_not_forwarded_in_either_direction(self):
        for constant in ('NaN', 'Infinity', '-Infinity', '1e999'):
            for source in ('client', 'agent'):
                with self.subTest(constant=constant, source=source):
                    environment = dict(self.env)
                    number = constant if source == 'client' else '1'
                    if source == 'agent':
                        environment['INVALID_JSON_NUMBER'] = constant
                    wire = ('{"jsonrpc":"2.0","id":1,"method":"initialize",'
                            '"params":{"protocolVersion":1,"number":' + number + '}}\n').encode()
                    result = subprocess.run([sys.executable, str(SCRIPT), '--workspace-folder', str(self.root)],
                        input=wire,
                        capture_output=True, env=environment, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b'')
                    self.assertIn(b'error:', result.stderr)

    def test_absent_or_ambiguous_container_fails_without_protocol_noise(self):
        for containers in ('', 'first\nsecond'):
            result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                env=dict(self.env, CONTAINERS=containers), timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertIn(b'exactly one running agent container', result.stderr)

    def test_clone_or_unverified_containers_are_refused(self):
        for env in ({'MODE': 'clone'}, {'ROOT': '/somewhere/else'}):
            with self.subTest(env=env):
                result = subprocess.run([sys.executable, str(SCRIPT), '--workspace-folder', str(self.root)],
                    capture_output=True, env=dict(self.env, **env), timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'shared', result.stderr)


if __name__ == '__main__':
    unittest.main()
