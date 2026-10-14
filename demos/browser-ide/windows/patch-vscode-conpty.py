"""Diagnostic-only node-pty pipe namespace patch in a dedicated VS Code copy."""
import argparse
from pathlib import Path
import hashlib
import json
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('archive',type=Path)
a=p.parse_args(); f=a.archive.resolve()
if 'BrowserIdeDemoBin' not in f.parts or f.name!='node_modules.asar':p.error('Only dedicated demo archives are supported')
b=f.read_bytes(); old=b'"conpty-".concat(Math.random() * 10000000)'; new=b'"LOCAL\\\\".concat(Math.random() * 10000000)'
if b.count(old)!=1 or len(old)!=len(new):p.error('Unsupported node-pty literal')
backup=f.with_suffix('.asar.before-conpty')
if backup.exists():p.error('Backup already exists')
backup.write_bytes(b); updated=b.replace(old,new);f.write_bytes(updated)
print(json.dumps({'before_sha256':hashlib.sha256(b).hexdigest(),'after_sha256':hashlib.sha256(updated).hexdigest(),'diagnostic_only':True}))
