"""Diagnostic-only literal patch; invalidates Code.exe's Authenticode signature.

Upstream fix: https://github.com/libuv/libuv/pull/5181
This shorter equivalent keeps libuv's random/collision-retry field, drops the PID
suffix, and fits the original literal. Never apply to the normal installation.
"""
import argparse
from pathlib import Path
import hashlib
import json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('exe', type=Path)
args = parser.parse_args()
exe = args.exe.resolve()
if 'BrowserIdeDemoBin' not in exe.parts or exe.name != 'Code.exe':
    parser.error('Only a dedicated BrowserIdeDemoBin copy is supported')
old = rb'\\?\pipe\uv\%llu-%lu' + b'\0'
new = rb'\\?\pipe\LOCAL\%llu' + b'\0\0'
data = exe.read_bytes()
if data.count(old) != 1 or len(old) != len(new):
    parser.error('Expected exactly one supported libuv literal')
backup = exe.with_suffix('.exe.before-libuv')
if backup.exists():
    parser.error('Backup already exists; refusing to overwrite it')
backup.write_bytes(data)
updated = data.replace(old, new)
exe.write_bytes(updated)
print(json.dumps({'before_sha256': hashlib.sha256(data).hexdigest(),
                  'after_sha256': hashlib.sha256(updated).hexdigest(),
                  'signature_invalidated': True, 'upstream_fix': 'libuv/libuv#5181'}))
