"""Tests of the workspace's routes file; no dependencies."""
import json
from pathlib import Path
import tempfile
import unittest

import workspace_routes


class WorkspaceRoutesTest(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'proxy-routes.json'
            path.write_text(data if isinstance(data, str) else json.dumps(data))
            return workspace_routes.load(path)

    def test_missing_file_adds_nothing(self):
        self.assertEqual(workspace_routes.load('/nonexistent/proxy-routes.json'), {})

    def test_routes(self):
        routes = self.load({'routes': [
            {'method': 'GET', 'host': 'fonts.googleapis.com', 'path': '/css2?'},
            {'method': '*', 'host': 'fonts.gstatic.com', 'path': '/s/.+'},
            {'method': 'HEAD', 'host': 'fonts.gstatic.com', 'path': '/'},
        ]})
        self.assertEqual(routes, {'fonts.googleapis.com': [('GET', '/css2?')],
                                  'fonts.gstatic.com': [(None, '/s/.+'), ('HEAD', '/')]})

    def test_invalid_files(self):
        route = {'method': 'GET', 'host': 'example.com', 'path': '/'}
        for data in ['{', [], {}, {'routes': {}}, {'routes': [], 'extra': 1},
                     '{"routes": [], "routes": []}',
                     {'routes': [{**route, 'port': 80}]},
                     {'routes': [{'host': 'example.com', 'path': '/'}]},
                     {'routes': [{**route, 'host': 'Example.com'}]},
                     {'routes': [{**route, 'host': 'example.com:8443'}]},
                     {'routes': [{**route, 'host': '10.0.0.1'}]},
                     {'routes': [{**route, 'host': 'localhost'}]},
                     {'routes': [{**route, 'host': '*.example.com'}]},
                     {'routes': [{**route, 'method': 'get'}]},
                     {'routes': [{**route, 'method': None}]},
                     {'routes': [{**route, 'path': 'example'}]},
                     {'routes': [{**route, 'path': '/('}]}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)

    def test_authenticated_hosts_stay_fixed(self):
        for host in workspace_routes.AUTHENTICATED_HOSTS:
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'authenticated'):
                self.load({'routes': [{'method': 'GET', 'host': host, 'path': '/gists'}]})


if __name__ == '__main__':
    unittest.main()
