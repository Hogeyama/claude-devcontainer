"""Run native Windows browser probes from WSL; receiver and proxy stay in Docker."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

HERE = Path(__file__).resolve().parent
PS = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
SCRIPT = '\\\\wsl.localhost\\' + os.environ.get('WSL_DISTRO_NAME', 'NixOS') + str(HERE / 'windows/probe-browser.ps1').replace('/', '\\')
OUT = Path('/tmp/browser-ide-windows-evidence')
OUT.mkdir(exist_ok=True)
local_app_data = subprocess.check_output([PS, '-NoProfile', '-NonInteractive', '-Command', '[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath("LocalApplicationData")']).decode('utf-8-sig').strip()
windows_path = PureWindowsPath(local_app_data)
WINDOWS_DATA = Path('/mnt') / windows_path.drive[0].lower() / Path(*windows_path.parts[1:])

def compose(*args, **kwargs):
    return subprocess.check_output(['docker', 'compose', '-f', str(HERE / 'compose.yaml'), *args], **kwargs)

def received():
    return json.loads(compose('exec', '-T', 'receiver', 'python3', '-c', 'import json,pathlib;p=pathlib.Path("/tmp/received.jsonl");print(json.dumps([json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []))'))

results = {'phases': {}}
try:
    for phase in ('control', 'restricted', 'ide-control', 'ide-restricted'):
        compose('up', '-d', 'proxy', env=dict(os.environ, DEMO_CONTROL='1' if phase.endswith('control') else '0'))
        if phase.startswith('ide-'):
            compose('exec', '-T', 'ide', 'rm', '-f', '/tmp/demo-windows-terminal-result')
        before = len(received())
        subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT, '-Mode', phase], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=75)
        # Fixture path belongs to this demo; no ordinary browser/user files are read.
        report = json.loads((WINDOWS_DATA / 'BrowserIdeDemoAC' / ('Probe-' + phase) / 'browser-probe.json').read_text(encoding='utf-8-sig'))
        events = received()[before:]
        results['phases'][phase] = {'browser': report, 'receiverEvents': events, 'count': len(events)}
        assert report['passed'] and report['perMonitorV2'], report
        if phase.startswith('ide-'):
            assert 'markdown-preview-verified' in report['events']
            terminal = compose('exec', '-T', 'ide', 'cat', '/tmp/demo-windows-terminal-result').decode().strip()
            assert terminal == 'Hello from the isolated code-server terminal', terminal
            if phase == 'ide-control':
                assert any(e['path'] == '/webview' and e['method'] == 'POST' for e in events), events
            else:
                assert not events, events
        elif phase == 'control':
            for path in ['/form', '/link', '/redirected', '/fetch', '/beacon', '/auto-beacon', '/image?marker=DEMO_ONLY']:
                assert any(e['path'] == path for e in events), path
            assert any(e['method'] == 'POST' and e['path'] == '/form' and e['body'] == 'marker=DEMO_ONLY' for e in events)
        else:
            assert not events, events
    results['passed'] = True
except Exception as error:
    results['passed'] = False
    results['error'] = repr(error)
    if isinstance(error, subprocess.CalledProcessError):
        print(error.output.decode('utf-8', errors='replace')[-5000:])
    raise
finally:
    compose('up', '-d', 'proxy', env=dict(os.environ, DEMO_CONTROL='0'))
    (OUT / 'browser-verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))
