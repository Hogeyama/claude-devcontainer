"""Tests of proxy.hosts, as policy.py checks it; standard library only."""
import base64
import unittest

import policy


def host(name, **settings):
    return {name: settings}


class ParseTest(unittest.TestCase):
    def test_empty(self):
        for hosts in [None, {}]:
            config = policy.parse(hosts)
            self.assertEqual((config.routes, config.headers, config.inject), ({}, {}, {}))

    def test_routes(self):
        config = policy.parse({
            'fonts.googleapis.com': {'routes': ['GET /css2?']},
            'fonts.gstatic.com': {'routes': ['/s/.+', 'HEAD /']},
        })
        self.assertEqual(config.routes, {'fonts.googleapis.com': [('GET', '/css2?')],
                                         'fonts.gstatic.com': [(None, '/s/.+'), ('HEAD', '/')]})
        self.assertEqual(config.secrets(), [])

    def test_inject_and_headers(self):
        config = policy.parse({
            'api.notion.com': {
                'routes': ['/v1/.+'],
                'headers': ['Notion-Version'],
                'inject': {'Authorization': 'Bearer ${NOTION_API_KEY}', 'X-Pair': '${A}-${B_2}'},
            },
            'github.com': {
                'routes': [r'/[^/]+/[^/]+\.git/info/refs'],
                'inject': {'Authorization': {'basic': 'x-access-token:${GITHUB_TOKEN}'}},
            },
        })
        self.assertEqual(config.headers, {'api.notion.com': {'notion-version'}})
        self.assertEqual(config.secrets(), ['A', 'B_2', 'GITHUB_TOKEN', 'NOTION_API_KEY'])
        secrets = {'NOTION_API_KEY': 'n', 'A': 'a', 'B_2': 'b', 'GITHUB_TOKEN': 'pat'}
        rendered = {host: [(name, policy.render(template, secrets)) for name, template in entries]
                    for host, entries in config.inject.items()}
        self.assertEqual(rendered, {
            'api.notion.com': [('Authorization', 'Bearer n'), ('X-Pair', 'a-b')],
            'github.com': [('Authorization', 'Basic ' + base64.b64encode(b'x-access-token:pat').decode())]})
        with self.assertRaises(KeyError):
            policy.render(config.inject['github.com'][0][1], {})

    def test_http(self):
        config = policy.parse({
            'mirror.example.com': {'routes': ['GET /debian/.+'], 'headers': ['X-A'], 'http': True},
            'fonts.googleapis.com': {'routes': ['GET /css2?'], 'http': False},
        })
        self.assertEqual(config.http, {'mirror.example.com'})
        self.assertEqual(policy.parse({'fonts.googleapis.com': {'routes': ['/']}}).http, set())
        for value in ['true', 1, None]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, r'\.http: expected true or false'):
                policy.parse(host('a.example.com', routes=['/'], http=value))
        with self.assertRaisesRegex(ValueError, r'\.http: plain HTTP would send'):
            policy.parse(host('a.example.com', routes=['/'], inject={'X-A': '${TOKEN}'}, http=True))

    def test_secret_values_are_not_reinterpreted_as_templates_or_escapes(self):
        for value in ['${OTHER}', r'\1', r'\g<1>', '$\\']:
            with self.subTest(value=value):
                template = policy.parse_template('prefix:${TOKEN}${TOKEN}:suffix', 'test')
                self.assertEqual(policy.render(template, {'TOKEN': value}), 'prefix:' + value + value + ':suffix')

    def test_template_rejects_invalid_placeholders_and_control_characters(self):
        for value in ['$TOKEN', '${TOKEN', '${lower}', '$${TOKEN}', '${TOKEN}\n', '\x00${TOKEN}', '\x7f']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.parse_template(value, 'test')

    def test_invalid(self):
        for hosts in [[], 'a.example.com', {'a.example.com': None}, {'a.example.com': {}},
                      host('a.example.com', routes=[]),
                      host('a.example.com', routes=['/'], port=80),
                      host('Example.com', routes=['/']),
                      host('example.com:8443', routes=['/']),
                      host('10.0.0.1', routes=['/']),
                      host('localhost', routes=['/']),
                      host('*.example.com', routes=['/']),
                      host('a.example.com', routes=['get /']),
                      host('a.example.com', routes=['GET']),
                      host('a.example.com', routes=['example']),
                      host('a.example.com', routes=['/(']),
                      host('a.example.com', routes=[{'method': 'GET'}]),
                      host('a.example.com', routes='/'),
                      host('a.example.com', routes=['/'], headers=['Bad Name']),
                      host('a.example.com', routes=['/'], headers='X-A'),
                      host('a.example.com', routes=['/'], inject=['Authorization']),
                      host('a.example.com', routes=['/'], inject={'Host': 'x'}),
                      host('a.example.com', routes=['/'], inject={'Content-Length': '1'}),
                      host('a.example.com', routes=['/'], inject={'X-A': '$HOME'}),
                      host('a.example.com', routes=['/'], inject={'X-A': '${lower}'}),
                      host('a.example.com', routes=['/'], inject={'X-A': ''}),
                      host('a.example.com', routes=['/'], inject={'X-A': {'basic': 'x', 'other': 'y'}}),
                      host('a.example.com', routes=['/'], inject={'X-A': ['x']})]:
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                policy.parse(hosts)

    def test_errors_name_the_setting(self):
        with self.assertRaisesRegex(ValueError, r'^claude.toml: proxy.hosts."a.example.com".routes\[1\]: '):
            policy.parse(host('a.example.com', routes=['/', 'get /']), 'claude.toml: proxy.hosts')

    def test_unpassable_headers(self):
        for name in ['Authorization', 'cookie', 'X-Api-Key', 'Host', 'Proxy-Authorization', 'Upgrade']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'use inject'):
                policy.parse(host('a.example.com', routes=['/'], headers=[name]))

    def test_authenticated_hosts_stay_fixed(self):
        for name in policy.AUTHENTICATED_HOSTS:
            with self.subTest(host=name), self.assertRaisesRegex(ValueError, 'authenticated'):
                policy.parse(host(name, routes=['GET /v1/messages']))


if __name__ == '__main__':
    unittest.main()
