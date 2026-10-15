#!/usr/bin/env python3
"""Changes to the web server that `code serve-web` runs, for the isolated editor.

agent/Dockerfile runs this at image build on the server (the directory
given). Each change replaces a string that must occur as many times as
stated, so that a VS Code update that moves one fails the build instead of
leaving the editor broken.

Workspace Trust is off. The agent's workspace is the attacker's to change
anyway, as its Claude Code trusts it up front, and the editor's Restricted
Mode would only disable extensions there. serve-web passes the server no
option for it, so the server's launcher (bin/code-server) adds
--disable-workspace-trust.

The webviews are served from http://localhost:18001, a port of the IDE's
site. VS Code puts each webview on a host of its own under vscode-cdn.net,
which the editor's window cannot reach, and which would be another site:
Chromium sends SameSite=Lax cookies to an iframe only when every frame above
it is of the top page's site, and the Simple Browser shows an app in an
iframe of a webview. code-server-proxy sends localhost:18001 to the same
server as the IDE's localhost:18000, which serves the webview's files under
/<quality>-<commit>/static/. Three files change for it:
- workbench.js: the endpoint. The build inlines product.json there, and the
  server passes the page no other.
- server-main.js: the IDE page's Content-Security-Policy, which allows only
  frames of vscode-cdn.net.
- the webview's index.html: it starts only on a host named after a hash of
  the page's origin. Here every page is on localhost, so the check proves
  nothing; it accepts the IDE's origin, and the CSP takes the script's new
  hash.

Usage: vscode-patches.py SERVER_DIRECTORY
"""
import base64
import hashlib
from pathlib import Path
import re
import sys

IDE = 'http://localhost:18000'
WEBVIEWS = 'http://localhost:18001'
PRE = 'out/vs/workbench/contrib/webview/browser/pre/'
CDN = 'https://{{uuid}}.vscode-cdn.net/{{quality}}/{{commit}}/' + PRE
SCRIPT = re.compile(r'(<script async type="module">)([\s\S]*?)(</script>)')


def replace(text, old, new, count, name):
    found = text.count(old)
    if found != count:
        sys.exit(f'error: {name}: expected {count} of {old!r}, found {found}; update agent/vscode-patches.py')
    return text.replace(old, new)


def script_hash(script):
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"


def patch_index(text):
    """The webview's index.html: its origin check, and the CSP hash of the script that holds it."""
    match = SCRIPT.search(text)
    if not match:
        sys.exit(f'error: {PRE}index.html: no module script; update agent/vscode-patches.py')
    script = replace(match[2], "if (hostname === parentOriginHash || hostname.startsWith(parentOriginHash + '.')) {",
                     f"if (location.origin === '{WEBVIEWS}' && parentOrigin === '{IDE}') {{",
                     1, PRE + 'index.html')
    text = replace(text, script_hash(match[2]), script_hash(script), 1, PRE + 'index.html')
    return text[:match.start(2)] + script + text[match.end(2):]


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: vscode-patches.py SERVER_DIRECTORY')
    server = Path(sys.argv[1])
    # The server serves its static files under the commit's root path.
    endpoint = WEBVIEWS + '/{{quality}}-{{commit}}/static/' + PRE
    changes = [
        ('bin/code-server', lambda text: replace(
            text, '"$ROOT/out/server-main.js" "$@"', '"$ROOT/out/server-main.js" --disable-workspace-trust "$@"',
            1, 'bin/code-server')),
        ('out/vs/code/browser/workbench/workbench.js', lambda text: replace(text, CDN, endpoint, 2, 'workbench.js')),
        ('out/server-main.js', lambda text: replace(
            text, "frame-src 'self' https://*.vscode-cdn.net data:;", f"frame-src 'self' {WEBVIEWS} data:;",
            1, 'server-main.js')),
        (PRE + 'index.html', patch_index),
    ]
    # Every check passes before any file changes.
    patched = [(server / name, change((server / name).read_text())) for name, change in changes]
    for path, text in patched:
        path.write_text(text)


if __name__ == '__main__':
    main()
