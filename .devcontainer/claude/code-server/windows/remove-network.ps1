# Removes the network isolation that the former WebView2 client installed:
# the WFP filters and the loopback exemption of its AppContainer, with the
# copy of its configure-network.ps1 that its setup left in
# %LOCALAPPDATA%\ClaudeCodeServerSetup. Asks for Administrator approval (UAC).
# The Electron shell that replaced that client needs neither.
$ErrorActionPreference = 'Stop'
$setup = Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerSetup'
$script = Join-Path $setup 'configure-network.ps1'
if (-not (Test-Path $script)) {
    Write-Output 'Nothing to remove: the WebView2 client was not set up on this computer.'
    exit 0
}
Write-Output 'Removing network isolation (Windows UAC approval required)...'
# No visible console: selecting text in conhost can pause output until Enter.
# NonInteractive also makes any accidental PowerShell prompt fail instead of waiting.
$arguments = @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', ('"' + $script + '"'), '-Remove'
)
$p = Start-Process (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
if ($p.ExitCode -ne 0) {
    throw ('Network removal failed (exit ' + $p.ExitCode + '); see ' + (Join-Path $setup 'network-setup.log'))
}
Write-Output ('Network isolation removed. The client''s files may be deleted: ClaudeCodeServerSetup, ClaudeCodeServerBin and ClaudeCodeServerAC in ' + $env:LOCALAPPDATA)
