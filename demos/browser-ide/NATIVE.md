# Native VS Code / AppContainer experiment

This is a diagnostic checkpoint, not a production launcher or a supported stock
VS Code configuration. The existing WebView2 demo remains separate.

## Observed on Windows (2026-10-15)

With a dedicated copy of VS Code 1.138.0, the native window connects to the Linux
server through the allowlisted proxy. The remote workspace extension invokes
`workbench.action.terminal.newLocal` and `sendSequence`; PowerShell actually writes
`remote-triggered.txt` in its AppContainer temporary directory. Inspection of the
main process, UI extension host and that PowerShell process returned the same
`BrowserIdeDemo.StandardIsolation` AppContainer SID.

The UI probe also observed EPERM on reading/writing a benign host canary, EACCES
on direct TCP to port 18082, and successful execution of a harmless child command.
These observations do not constitute a complete boundary audit: the remote-created
terminal has not yet run the full positive-control network/protocol test suite.
Native IME behavior and GitHub Settings Sync are not validated here.

## Compatibility changes

Only `%LOCALAPPDATA%\BrowserIdeDemoBin\vscode` is patched, never the normal VS Code
installation. The patch scripts retain backups and are diagnostic only:

- `windows/patch-vscode-pipes.ps1`: add the AppContainer `LOCAL` namespace to VS Code
  JavaScript IPC pipe names.
- `windows/patch-vscode-libuv.py`: replace the embedded libuv pipe-name literal in
  Code.exe with an equal-length LOCAL name. This invalidates Authenticode; it is
  not a source build of the [upstream fix](https://github.com/libuv/libuv/pull/5181).
- `windows/patch-vscode-conpty.py`: change node-pty's pipe prefix inside
  node_modules.asar to LOCAL, allowing ConPTY to start.

VS Code's corrupt-installation warning is expected after these changes and has
not been suppressed. A maintained source build is needed instead of binary edits.
The working launch also uses `--no-sandbox --disable-gpu` and
`terminal.integrated.windowsUseConptyDll=true`. The outer AppContainer remains,
but Electron's internal Chromium sandbox is disabled. This loses an additional
containment layer; it must not be described as equivalent to normal sandboxing.

## Experimental components

`windows/probe-vscode.ps1` requires the existing demo AppContainer/WFP setup. Its
working switches are `-MappedDrives -ExtensionProbe -RemoteProbe -ConptyDll
-DiagnosticFlags '--no-sandbox --disable-gpu'`. It creates a fresh data directory
and maps binary/data directories to unused drive letters to avoid Node path
resolution failures. Live sessions retain those mappings; cleanup is currently
manual after closing their processes. The script does not apply patches itself.

`compose.native.yaml` overlays `compose.yaml`. Set `NATIVE_SERVER_DIR` to an
extracted official Linux VS Code server matching the client's commit
(`7debcd0e2acdea1c52de81bf9ee1620444407dda` in this run). The overlay enables only
one additional proxy destination, native.localhost:8002. Its fixed demo token is
not a secret or production authentication design.

`native-resolver` supplies the experimental remote resolver using a fixed proxy
CONNECT destination. `native-extension` runs the local probes.
`native-remote-probe` must be installed in the server's temporary extensions
directory (with an extensions.json entry); it intentionally triggers a harmless
local command on activation. Server tmpfs and that installation disappear on
container recreation. Setup is not yet automated end to end.
