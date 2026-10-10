import json
from pathlib import Path
import tempfile
import unittest

import credentials

VALID = dict(github_token='pat', anthropic_token='api', auth_mode='api-key')


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'credentials.json'

    def install(self, value):
        credentials.install_snapshot(json.dumps(value).encode(), self.path)

    def test_install_and_rotate(self):
        self.install(VALID)
        self.assertEqual(credentials.read_snapshot(self.path), ('pat', 'api', 'api-key'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o400)
        self.install(dict(github_token='pat2', anthropic_token='oauth', auth_mode='oauth'))
        self.assertEqual(credentials.read_snapshot(self.path), ('pat2', 'oauth', 'oauth'))

    def test_invalid_input_keeps_previous_credentials(self):
        self.install(VALID)
        for bad in [{}, [], {**VALID, 'extra': 'x'}, {**VALID, 'auth_mode': 'other'}, {**VALID, 'github_token': ''}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.install(bad)
        with self.assertRaises(ValueError):
            credentials.install_snapshot(b'{', self.path)
        self.assertEqual(credentials.read_snapshot(self.path), ('pat', 'api', 'api-key'))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
