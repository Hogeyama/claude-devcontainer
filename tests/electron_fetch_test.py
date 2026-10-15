"""How code-server/electron.py fetches Electron for the editor's shell; no network needed."""
import hashlib
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('electron', ROOT / '.devcontainer/claude/code-server/electron.py')
electron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(electron)


def release(path, members):
    """A zip like Electron's: (name, mode, data) made on Unix."""
    with zipfile.ZipFile(path, 'w') as zip:
        for name, mode, data in members:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            zip.writestr(info, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ElectronTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_ensure_downloads_once_and_keeps_the_executable_bits(self):
        archive = self.tmp / 'release.zip'
        digest = release(archive, [('electron', stat.S_IFREG | 0o4755, b'elf'),
                                   ('resources.pak', stat.S_IFREG | 0o644, b'pak'),
                                   ('locales/ja.pak', stat.S_IFREG | 0o644, b'ja')])
        fetched = []

        def fetch(url, path):
            fetched.append(url)
            path.write_bytes(archive.read_bytes())
        with mock.patch.dict(electron.SHA256, {'linux-x64': digest}):
            directory = electron.ensure(self.tmp / 'cache', 'linux-x64', fetch)
            self.assertEqual(electron.ensure(self.tmp / 'cache', 'linux-x64', fetch), directory)
        self.assertEqual(directory, self.tmp / 'cache' / f'{electron.VERSION}-linux-x64')
        self.assertEqual(fetched, [electron.URL.format(version=electron.VERSION, platform='linux-x64')])
        # Executable, but never set-user-ID.
        self.assertEqual(stat.S_IMODE((directory / 'electron').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((directory / 'resources.pak').stat().st_mode), 0o644)
        self.assertEqual((directory / 'locales/ja.pak').read_bytes(), b'ja')

    def test_ensure_refuses_another_archive(self):
        archive = self.tmp / 'release.zip'
        release(archive, [('electron', stat.S_IFREG | 0o755, b'not the pinned one')])
        with self.assertRaisesRegex(SystemExit, 'SHA-256'):
            electron.ensure(self.tmp / 'cache', 'linux-x64', lambda url, path: path.write_bytes(archive.read_bytes()))
        self.assertEqual(list((self.tmp / 'cache').iterdir()), [])

    def test_extract_refuses_links_and_paths_outside(self):
        for name, mode in [('../escape', stat.S_IFREG | 0o644), ('link', stat.S_IFLNK | 0o777)]:
            with self.subTest(name=name):
                archive = self.tmp / 'bad.zip'
                release(archive, [(name, mode, b'x')])
                with self.assertRaises(SystemExit):
                    electron.extract(archive, self.tmp / 'out')
        self.assertFalse((self.tmp / 'escape').exists())

    def test_platforms(self):
        self.assertEqual(electron.linux_platform('x86_64'), 'linux-x64')
        self.assertEqual(electron.linux_platform('aarch64'), 'linux-arm64')
        with self.assertRaisesRegex(SystemExit, 'no Electron'):
            electron.linux_platform('riscv64')
        self.assertEqual(electron.executable('win32-x64'), 'electron.exe')
        self.assertEqual(set(electron.SHA256), {'linux-x64', 'linux-arm64', 'win32-x64'})


if __name__ == '__main__':
    unittest.main()
