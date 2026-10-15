#!/usr/bin/env python3
"""Electron for the editor's shell (shell/), on the trusted host.

linux/client.py and windows/client.py take it from here: the release zip of
VERSION for the platform, downloaded once into a cache and checked against
the SHA-256 that Electron's SHASUMS256.txt gives for it. The shell's
enforcement depends on Electron's security fixes, so VERSION is to follow
Electron's supported releases.
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import urllib.request
import zipfile

VERSION = '44.7.2'
SHA256 = {
    'linux-x64': '777b26a64aee3d185acb9f93c04782bb51eae7c2c61c793963ef24374e5a1539',
    'linux-arm64': 'f8ecb0702b6a0b10274d2c3043dddaa22da77addcab98e7e0b1e0d4f6c2d93e9',
    'win32-x64': '10a89447ef64ac5cb9f782af48ffd83734a40ae91681259d1048ca3c9e6c5da4',
}
URL = 'https://github.com/electron/electron/releases/download/v{version}/electron-v{version}-{platform}.zip'


def fail(message):
    raise SystemExit('error: ' + message)


def linux_platform(machine=None):
    machine = machine or os.uname().machine
    names = {'x86_64': 'linux-x64', 'aarch64': 'linux-arm64'}
    if machine not in names:
        fail(f'no Electron for Linux on {machine}')
    return names[machine]


def executable(platform):
    return 'electron.exe' if platform.startswith('win32') else 'electron'


def download(url, path):
    print(f'downloading {url}', flush=True)
    with urllib.request.urlopen(url) as response, open(path, 'wb') as out:
        shutil.copyfileobj(response, out)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def extract(archive, target):
    """The zip's files with their permission bits, never set-user-ID or a path outside target.

    zipfile drops the bits, and Linux's electron must stay executable.
    chrome-sandbox is not made setuid: Chromium's sandbox takes user
    namespaces instead.
    """
    with zipfile.ZipFile(archive) as zip:
        for member in zip.infolist():
            path = Path(target, member.filename).resolve()
            if not path.is_relative_to(Path(target).resolve()):
                fail(f'{archive}: {member.filename} is outside the archive')
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                fail(f'{archive}: {member.filename} is a link')
            zip.extract(member, target)
            if member.create_system == 3 and not member.is_dir():
                path.chmod(stat.S_IMODE(mode) & 0o755)


def ensure(cache, platform, fetch=download):
    """The directory of Electron for platform under cache, downloaded and checked the first time."""
    if platform not in SHA256:
        fail(f'no Electron for {platform}')
    target = Path(cache) / f'{VERSION}-{platform}'
    if (target / executable(platform)).is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        archive = Path(temporary) / 'electron.zip'
        fetch(URL.format(version=VERSION, platform=platform), archive)
        found = sha256(archive)
        if found != SHA256[platform]:
            fail(f'Electron {VERSION} for {platform} has SHA-256 {found}, not {SHA256[platform]}')
        extract(archive, Path(temporary) / 'electron')
        os.rename(Path(temporary) / 'electron', target)
    return target
