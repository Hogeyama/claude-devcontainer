"""Policy tests; run with mitmproxy 11.1.3 installed."""
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mitmproxy import connection, http
from mitmproxy.test import tflow

from addon import PolicyAddon

CREDENTIALS = ('managed-pat', 'managed-anthropic', 'api-key')


def flow(url='https://api.github.com/graphql', method='POST', body=b'{}', headers=()):
    result = tflow.tflow()
    result.request = http.Request.make(method, url, body)
    for name, value in headers:
        result.request.headers.add(name, value)
    result.response = None
    return result


def messages(**extra):
    return {'model': 'claude', 'max_tokens': 1, 'messages': [{'role': 'user', 'content': 'hello'}], **extra}


def anthropic(body=None, path='/v1/messages', headers=()):
    return flow('https://api.anthropic.com' + path, body=json.dumps(body or messages()).encode(),
                headers=[('Content-Type', 'application/json'), *headers])


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.addon = PolicyAddon()
        self.credentials = CREDENTIALS
        patcher = patch('addon.read_snapshot', side_effect=self.read_snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read_snapshot(self):
        if self.credentials is None:
            raise FileNotFoundError()
        return self.credentials

    def send(self, f):
        self.addon.requestheaders(f)
        self.addon.request(f)
        return f

    def allowed(self, f):
        self.assertIsNone(self.send(f).response)
        return f.request.headers

    def denied(self, f):
        self.assertEqual(self.send(f).response.status_code, 403)

    def test_github_credentials_replace_client_credentials(self):
        attacker = [('Authorization', 'Bearer attacker'), ('Authorization', 'Basic attacker'),
                    ('Cookie', 'session=attacker'), ('X-Api-Key', 'attacker'), ('Host', 'github.com')]
        headers = self.allowed(flow(headers=attacker))
        self.assertEqual(headers.get_all('Authorization'), ['Bearer managed-pat'])
        self.assertEqual(headers.get_all('Host'), ['api.github.com'])
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('X-Api-Key', headers)
        headers = self.allowed(flow('https://github.com/owner/repo.git/git-upload-pack', headers=attacker))
        self.assertEqual(headers['Authorization'], 'Basic ' + base64.b64encode(b'x-access-token:managed-pat').decode())

    def test_github_endpoints(self):
        for url, method in [('https://api.github.com/user', 'GET'), ('https://api.github.com/repos/o/r/issues', 'POST'),
                            ('https://api.github.com/repos/o/r/contents/a%20b', 'PUT'),
                            ('https://github.com/o/r.git/info/refs?service=git-upload-pack', 'GET'),
                            ('https://github.com/o/r.git/git-receive-pack', 'POST')]:
            with self.subTest(url=url):
                self.allowed(flow(url, method))
        for url, method in [('https://api.github.com/gists', 'POST'), ('https://api.github.com/user', 'POST'),
                            ('https://api.github.com/repos/a/../../gists', 'POST'),
                            ('https://api.github.com/repos/a/%2e%2e/%2e%2e/gists', 'POST'),
                            ('https://api.github.com/repos/a/%252e%252e/gists', 'POST'),
                            ('https://github.com/login', 'GET'), ('http://api.github.com/user', 'GET'),
                            ('https://api.github.com:8443/user', 'GET'), ('https://example.com/user', 'GET')]:
            with self.subTest(url=url):
                self.denied(flow(url, method))
        self.denied(flow(headers=[('Upgrade', 'websocket')]))

    def test_anthropic_local_tools_and_inline_content(self):
        body = messages(system=[{'type': 'text', 'text': 'system', 'cache_control': {'type': 'ephemeral'}}],
                        tools=[{'name': 'Bash', 'input_schema': {'properties': {'mcp_servers': {}}}}], stream=True)
        body['messages'] += [
            {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 't', 'signature': 's'},
                                              {'type': 'tool_use', 'id': '1', 'name': 'Bash', 'input': {'source': {'type': 'url'}}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': '1', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aGk='}},
                {'type': 'document', 'source': {'type': 'text', 'media_type': 'text/plain', 'data': 'local'}}]}]}]
        headers = self.allowed(anthropic(body, headers=[('X-Api-Key', 'attacker'), ('anthropic-beta', 'claude-code-20250219')]))
        self.assertEqual(headers.get_all('x-api-key'), ['managed-anthropic'])
        self.assertNotIn('Authorization', headers)
        # Fields Claude Code sends that are unknown here but cannot reach other hosts.
        body = messages(safeguards=[{'type': 'dangerous_tool_use'}], output_config={'effort': 'medium'})
        body['messages'].append({'role': 'system', 'content': 'mid-conversation', 'output_config': {'effort': 'low'}})
        self.allowed(anthropic(body, headers=[('anthropic-beta', 'per-turn-control-2026-07-01,future-2099-01-01')]))
        self.allowed(anthropic({'model': 'claude', 'messages': []}, '/v1/messages/count_tokens?beta=true'))
        self.allowed(flow('https://api.anthropic.com/api/hello', 'GET', b''))

    def test_anthropic_oauth(self):
        self.credentials = ('managed-pat', 'managed-oauth', 'oauth')
        headers = self.allowed(anthropic(headers=[('X-Api-Key', 'attacker'), ('Authorization', 'Bearer attacker')]))
        self.assertEqual(headers.get_all('Authorization'), ['Bearer managed-oauth'])
        self.assertNotIn('X-Api-Key', headers)
        self.assertIn('oauth-2025-04-20', headers['anthropic-beta'])

    def test_anthropic_artifacts(self):
        self.credentials = ('managed-pat', 'managed-oauth', 'oauth')
        for path in ['/v1/mcp_servers?limit=1000', '/api/claude_code/policy_limits', '/api/oauth/profile', '/api/artifacts/mcp',
                     '/api/claude_code/settings']:
            with self.subTest(path=path):
                f = flow('https://api.anthropic.com' + path, 'GET', b'', [('Authorization', 'Bearer attacker')])
                del f.request.headers['Content-Length']
                self.assertEqual(self.allowed(f).get_all('Authorization'), ['Bearer managed-oauth'])
        mcp = [('Mcp-Session-Id', 's'), ('MCP-Protocol-Version', 'v'), ('X-Upload-Token', 't'), ('X-Frame-CP', 'go')]
        headers = self.allowed(flow('https://api.anthropic.com/api/artifacts/mcp', 'POST', b'{}', mcp))
        self.assertEqual([headers[name] for name, _ in mcp], ['s', 'v', 't', 'go'])
        self.allowed(flow('https://api.anthropic.com/api/artifacts/mcp/upload/u1', 'PUT', b'page'))
        self.allowed(flow('https://api.anthropic.com/api/frame/deploy/direct?attempt_id=a', 'POST', b'{}'))
        self.allowed(flow('https://api.anthropic.com/api/frame/frames?limit=200', 'GET', b''))
        self.allowed(flow('https://api.anthropic.com/api/frame/permalink', 'GET', b''))
        for url, method in [('https://api.anthropic.com/api/artifacts/mcp/other', 'POST'),
                            ('https://api.anthropic.com/api/frame/', 'GET'),
                            ('https://api.anthropic.com/api/frame/perm/slug', 'PATCH'),
                            ('https://api.anthropic.com/api/frame/perm/slug?org=o', 'GET'),
                            ('https://api.anthropic.com/api/frame/PERM/slug', 'PATCH'),
                            ('https://api.anthropic.com/api/frame/perm', 'PATCH'),
                            ('https://api.anthropic.com/api/frame/./perm/slug', 'PATCH'),
                            ('https://api.anthropic.com/api/frame//perm/slug', 'PATCH'),
                            ('https://api.anthropic.com/api/frame/%70erm/slug', 'PATCH'),
                            ('https://api.anthropic.com/v1/mcp_servers', 'POST'),
                            ('https://api.anthropic.com/api/oauth/account/settings', 'GET')]:
            with self.subTest(url=url):
                self.denied(flow(url, method))

    def test_anthropic_rejects_ways_to_reach_other_hosts(self):
        variants = [messages(mcp_servers=[]), messages(container='c'),
                    messages(tools=[{'type': 'web_fetch_20250910', 'name': 'web_fetch'}])]
        for block in [{'type': 'image', 'source': {'type': 'url', 'url': 'https://attacker.example'}},
                      {'type': 'document', 'source': {'type': 'file', 'file_id': 'f'}}]:
            for content in [[block], [{'type': 'tool_result', 'tool_use_id': '1', 'content': [block]}]]:
                variants.append(messages(messages=[{'role': 'user', 'content': content}]))
        variants.append(messages(system=[{'type': 'image', 'source': {'type': 'url', 'url': 'https://attacker.example'}}]))
        for body in variants:
            with self.subTest(body=body):
                self.denied(anthropic(body))
        for raw in [b'{', b'[]', b'{"model":"a","mcp_servers":[],"mcp_servers":null,"messages":[]}']:
            f = anthropic()
            f.request.content = raw
            self.denied(f)
        self.denied(anthropic(headers=[('Content-Encoding', 'gzip')]))
        self.denied(anthropic(path='/v1/files'))
        self.denied(flow('https://api.anthropic.com/api/event_logging/v2/batch', 'POST'))

    def test_anthropic_feature_flags(self):
        self.allowed(flow('https://api.anthropic.com/api/eval/sdk-abc', 'POST', b'{"attributes":{}}'))
        for url, method in [('https://api.anthropic.com/api/eval/sdk-abc', 'GET'),
                            ('https://api.anthropic.com/api/eval/', 'POST'),
                            ('https://api.anthropic.com/api/eval/sdk-abc/more', 'POST'),
                            ('https://api.anthropic.com/api/features/sdk-abc', 'GET'),
                            ('https://api.anthropic.com/sub/sdk-abc', 'GET')]:
            with self.subTest(url=url, method=method):
                self.denied(flow(url, method))

    def test_anthropic_bodies_are_not_bounded(self):
        chunked = anthropic()
        del chunked.request.headers['Content-Length']
        chunked.request.headers['Transfer-Encoding'] = 'chunked'
        large = anthropic(messages(messages=[{'role': 'user', 'content': 'x' * (32 * 1024 * 1024)}]))
        for f in (chunked, large):
            self.allowed(f)

    def test_denied_body_is_refused_before_buffering(self):
        f = anthropic(path='/v1/files')
        self.addon.requestheaders(f)
        self.assertEqual(f.response.status_code, 403)
        self.assertIsNotNone(f.error)
        self.credentials = None
        f = flow()
        self.addon.requestheaders(f)
        self.assertEqual(f.response.status_code, 403)
        self.assertIsNotNone(f.error)

    def test_missing_credentials_are_explained_to_claude(self):
        self.credentials = None
        f = anthropic()
        self.addon.requestheaders(f)
        self.assertIsNone(f.error, 'an Anthropic body is read so that Claude sees the reason')
        self.assertEqual(f.response.status_code, 403)
        self.assertIn(b'inject-credentials.py', f.response.content)
        headers = self.allowed(flow('https://api.anthropic.com/api/hello', 'GET', b'', [('X-Api-Key', 'attacker')]))
        self.assertNotIn('X-Api-Key', headers)

    def test_connect_and_upstream_only_to_allowed_hosts(self):
        for authority, allowed in [('api.github.com:443', True), ('api.github.com:444', False), ('example.com:443', False)]:
            f = flow()
            f.request = http.Request.make('CONNECT', 'https://' + authority)
            self.addon.http_connect(f)
            self.assertEqual(f.response is None, allowed, authority)
        for address, allowed in [(('api.anthropic.com', 443), True), (('example.com', 443), False), (None, False)]:
            data = SimpleNamespace(server=connection.Server(address=address))
            self.addon.server_connect(data)
            self.assertEqual(data.server.error is None, allowed, address)


if __name__ == '__main__':
    unittest.main()
