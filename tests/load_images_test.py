"""How load-images.py reads docker.images of claude.toml; no Docker needed."""
import json
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('load_images', ROOT / '.devcontainer/claude/scripts/load-images.py')
load_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_images)


class ListTest(unittest.TestCase):
    def test_reads_references_without_comments_and_blank_lines(self):
        content = b'# header\n\npostgres:16  # database\n  testcontainers/ryuk:0.14.0\n'
        self.assertEqual(load_images.images(content), ['postgres:16', 'testcontainers/ryuk:0.14.0'])

    def test_marker_matches_the_name_wait_ready_computes(self):
        content = b'postgres:16\n'
        shell = subprocess.run(['sh', '-c', 'sha256sum | cut -c1-16'], input=content, capture_output=True, check=True)
        self.assertEqual(load_images.marker(content), 'images-' + shell.stdout.decode().strip())

    def test_list_is_what_wait_ready_reads(self):
        # Their marker names must agree: initializeCommand writes the file, and
        # load-images.py reads the settings, which may have changed since.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.devcontainer').mkdir()
            (root / '.devcontainer/claude.toml').write_text('[docker]\nimages = ["busybox:1.37", "postgres:16"]\n')
            with mock.patch.object(load_images, 'ROOT', root):
                listed = load_images.listed()
            settings_spec = importlib.util.spec_from_file_location('settings', ROOT / '.devcontainer/claude/scripts/settings.py')
            settings = importlib.util.module_from_spec(settings_spec)
            settings_spec.loader.exec_module(settings)
            settings.generate(root, {})
            self.assertEqual(listed, (root / '.devcontainer/.generated/images.txt').read_bytes())
            self.assertEqual(load_images.images(listed), ['busybox:1.37', 'postgres:16'])


class SharedBaseTest(unittest.TestCase):
    """load() over docker.shared_base: the base's images are named and counted as loaded."""

    def fake_dind(self, named, record):
        """in_dind for a DinD that names images in named and keeps record; its calls go to self.calls."""
        self.calls = []

        def in_dind(dind, script, *arguments, data=None, check=True):
            self.calls.append((script, arguments))
            result = mock.Mock(returncode=0, stdout='')
            if 'image inspect' in script:
                result.returncode = 0 if arguments[0] in named else 1
            elif script.startswith('docker tag'):
                named.add(arguments[1])
            elif script.startswith('cat >'):
                record.clear()
                record.update(json.loads(data))
            elif script.startswith('cat '):
                result.stdout = json.dumps(record)
            return result
        return in_dind

    def test_names_the_base_images_that_dind_does_not_and_loads_nothing_twice(self):
        # This DinD named its own image before generation 2 added busybox.
        named, record = {'own:1'}, {'own:1': 'sha256:own'}
        base = {'busybox:1.36': {'host': 'sha256:host-busybox', 'dind': 'sha256:dind-busybox'}}
        hosts = {'busybox:1.36': 'sha256:host-busybox', 'own:1': 'sha256:own'}
        with mock.patch.object(load_images, 'in_dind', side_effect=self.fake_dind(named, record)), \
             mock.patch.object(load_images, 'host_id', side_effect=hosts.get), \
             mock.patch.object(load_images.subprocess, 'Popen') as save:
            failures = load_images.load('dind', ['busybox:1.36', 'own:1'], base=base)
        self.assertEqual(failures, [])
        self.assertIn(('docker tag "$1" "$2"', ('sha256:dind-busybox', 'busybox:1.36')), self.calls)
        save.assert_not_called()
        self.assertEqual(record, {'busybox:1.36': 'sha256:host-busybox', 'own:1': 'sha256:own'})

    def test_loads_an_image_whose_host_id_moved_past_the_base(self):
        named, record = {'busybox:1.36'}, {}
        base = {'busybox:1.36': {'host': 'sha256:old', 'dind': 'sha256:dind-old'}}
        with mock.patch.object(load_images, 'in_dind', side_effect=self.fake_dind(named, record)), \
             mock.patch.object(load_images, 'host_id', return_value='sha256:new'), \
             mock.patch.object(load_images.subprocess, 'Popen') as save, \
             mock.patch.object(load_images.subprocess, 'run', return_value=mock.Mock(returncode=0, stderr='')):
            save.return_value.wait.return_value = 0
            failures = load_images.load('dind', ['busybox:1.36'], base=base)
        self.assertEqual(failures, [])
        save.assert_called_once()
        self.assertEqual(record, {'busybox:1.36': 'sha256:new'})


class StartupTest(unittest.TestCase):
    def run_startup(self, recreate_dind):
        clock = [0]
        marks = []
        def sleep(seconds):
            clock[0] += 10
        def find(service):
            if service == 'agent':
                return 'old-agent' if clock[0] < 90 else 'new-agent'
            return 'new-dind' if recreate_dind and clock[0] >= 90 else 'old-dind'
        def ready(dind, *args, **kwargs):
            return subprocess.CompletedProcess([], 1 if dind == 'new-dind' and clock[0] < 110 else 0)
        def finish(agent, content, failures=None):
            marks.append((clock[0], agent, failures))
        with mock.patch.object(load_images, 'WAIT_SECONDS', 180), \
             mock.patch.object(load_images.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(load_images.time, 'sleep', side_effect=sleep), \
             mock.patch.object(load_images, 'find', side_effect=find), \
             mock.patch.object(load_images, 'in_dind', side_effect=ready), \
             mock.patch.object(load_images, 'load', return_value=[]) as load, \
             mock.patch.object(load_images, 'finish', side_effect=finish):
            load_images.wait_and_load(b'busybox:1.37\n')
        return marks, load.call_args_list

    def test_build_longer_than_a_minute_still_marks_the_replacement_agent(self):
        marks, loads = self.run_startup(False)
        self.assertEqual(marks, [(0, 'old-agent', None), (0, 'old-agent', []),
                                 (90, 'new-agent', None), (90, 'new-agent', [])])
        self.assertEqual(loads, [mock.call('old-dind', ['busybox:1.37'])])

    def test_replacement_dind_is_checked_before_reporting_completion(self):
        marks, loads = self.run_startup(True)
        self.assertEqual(marks, [(0, 'old-agent', None), (0, 'old-agent', []),
                                 (90, 'new-agent', None), (110, 'new-agent', [])])
        self.assertEqual(loads, [mock.call('old-dind', ['busybox:1.37']),
                                 mock.call('new-dind', ['busybox:1.37'])])


if __name__ == '__main__':
    unittest.main()
