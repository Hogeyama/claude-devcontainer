"""The subordinate IDs that dind/subids.sh gives rootless, as rootlesskit maps them."""
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.devcontainer/claude/dind/subids.sh'


def subids(agent):
    return subprocess.run(['sh', str(SCRIPT), str(agent)], capture_output=True, text=True)


def container_ids(lines):
    """{container ID: ID outside} as rootlesskit maps them: its own ID to 0, then
    the subordinate ranges in file order from 1."""
    mapping, inside = {}, 1
    for line in lines.splitlines():
        user, start, count = line.split(':')
        assert user == 'rootless'
        for offset in range(int(count)):
            mapping[inside] = int(start) + offset
            inside += 1
    return mapping


class SubIdsTest(unittest.TestCase):
    def test_the_agents_id_is_itself_and_the_others_are_as_before(self):
        for agent in (1, 2, 999, 1000, 4242, 65534, 65535):
            with self.subTest(agent=agent):
                result = subids(agent)
                self.assertEqual(result.returncode, 0, result.stderr)
                mapping = container_ids(result.stdout)
                self.assertEqual(sorted(mapping), list(range(1, 65537)))
                self.assertEqual(mapping.pop(agent), agent)
                # Without the mapping, container ID n is 100000 + n - 1.
                self.assertEqual({inside: outside for inside, outside in mapping.items()
                                  if outside != 100000 + inside - 1}, {})
                # Nothing outside but the agent's ID and the subordinate IDs.
                self.assertTrue(all(100000 <= outside < 165536 for outside in mapping.values()))

    def test_refuses_ids_that_containers_do_not_have(self):
        for agent in (0, 65536, 100000):
            with self.subTest(agent=agent):
                result = subids(agent)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
