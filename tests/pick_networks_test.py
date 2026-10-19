"""How pick-networks.py chooses the internal subnets; no Docker needed."""
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pick_networks', ROOT / '.devcontainer/claude/scripts/pick-networks.py')
pick_networks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pick_networks)

PROXY, DIND = pick_networks.NAMES


def nets(*subnets):
    return [ipaddress.ip_network(subnet) for subnet in subnets]


class ChooseTest(unittest.TestCase):
    def test_first_free_pair(self):
        used = nets('10.231.70.0/24', '10.231.71.0/24', '172.17.0.0/16')
        self.assertEqual(pick_networks.choose({}, {}, used), {PROXY: '10.231.72', DIND: '10.231.73'})

    def test_kept_while_free(self):
        kept = {PROXY: '10.231.90', DIND: '10.231.91'}
        self.assertEqual(pick_networks.choose({}, kept, nets('10.231.70.0/24')), kept)

    def test_chosen_again_when_taken(self):
        kept = {PROXY: '10.231.70', DIND: '10.231.71'}
        self.assertEqual(pick_networks.choose({}, kept, nets('10.231.71.0/24')), {PROXY: '10.231.70', DIND: '10.231.72'})
        self.assertEqual(pick_networks.choose({}, {PROXY: '192.168.1', DIND: 'junk'}, []),
                         {PROXY: '10.231.70', DIND: '10.231.71'})

    def test_set_values_are_kept_out(self):
        self.assertEqual(pick_networks.choose({PROXY: '10.231.70', DIND: ''}, {}, []), {DIND: '10.231.71'})
        self.assertEqual(pick_networks.choose({PROXY: '10.231.72', DIND: '10.231.73'}, {}, []), {})

    def test_no_free_subnet(self):
        with self.assertRaises(SystemExit):
            pick_networks.choose({}, {}, nets('10.231.0.0/16'))


class MainTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cwd = os.getcwd()
        os.chdir(directory.name)
        self.addCleanup(os.chdir, self.cwd)
        self.networks = [
            # This workspace's own, from an earlier `up`: not in the way.
            {'Labels': {'com.docker.compose.project': 'mine'}, 'IPAM': {'Config': [{'Subnet': '10.231.70.0/24'}]}},
            {'Labels': {'com.docker.compose.project': 'other'}, 'IPAM': {'Config': [{'Subnet': '10.231.71.0/24'}]}},
            {'Labels': None, 'IPAM': {'Config': [{'Subnet': 'fd00::/64'}, {'Subnet': '10.231.72.0/24'}]}},
            {'Labels': {}, 'IPAM': {'Config': None}}]

    def docker(self, *arguments):
        if arguments[0] == 'ps':
            return 'mine\n'
        if arguments[:2] == ('network', 'ls'):
            return 'a\nb\nc\nd\n'
        return json.dumps(self.networks)

    def run_main(self, *argv):
        with mock.patch.object(pick_networks, 'docker', side_effect=self.docker), \
                mock.patch('sys.argv', ['pick-networks.py', *argv]):
            pick_networks.main()
        return pick_networks.out().read_text()

    def test_writes_an_ignored_env_file(self):
        text = self.run_main('', '')
        self.assertIn(f'{PROXY}=10.231.70\n{DIND}=10.231.73\n', text)
        self.assertEqual((pick_networks.out().parent / '.gitignore').read_text(), '*\n')
        # Kept on the next run.
        self.networks.append({'Labels': {}, 'IPAM': {'Config': [{'Subnet': '10.231.74.0/24'}]}})
        self.assertEqual(self.run_main('', ''), text)

    def test_host_mode_writes_to_the_state_directory(self):
        state = Path(os.getcwd()) / 'state'
        with mock.patch.dict(os.environ, {'CLAUDE_DEVCONTAINER_CONFIG_DIR': str(state)}):
            self.run_main('', '')
        self.assertTrue((state / '.generated/networks.env').is_file())
        self.assertFalse(Path('.devcontainer').exists())

    def test_set_values_are_not_written(self):
        text = self.run_main('10.231.80', '10.231.81')
        self.assertNotIn('=', text.replace('# ', ''))


if __name__ == '__main__':
    unittest.main()
