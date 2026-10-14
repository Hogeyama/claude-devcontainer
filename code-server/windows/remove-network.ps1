# Removes the network isolation that prepare.ps1 installed, with the copy of
# configure-network.ps1 in %LOCALAPPDATA%\ClaudeCodeServerSetup. Asks for
# Administrator approval (UAC).
$ErrorActionPreference = 'Stop'
$setup = Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerSetup'
$script = Join-Path $setup 'configure-network.ps1'
Write-Output 'Removing network isolation (Windows UAC approval required)...'
# Hidden and NonInteractive for the same reasons as in prepare.ps1.
$arguments = @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', ('"' + $script + '"'), '-Remove'
)
$p = Start-Process (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
if ($p.ExitCode -ne 0) {
    throw ('Network removal failed (exit ' + $p.ExitCode + '); see ' + (Join-Path $setup 'network-setup.log'))
}
Write-Output 'Network isolation removed.'
