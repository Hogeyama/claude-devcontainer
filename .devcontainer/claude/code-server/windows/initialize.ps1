# Creates the AppContainer profile and its writable data directory,
# %LOCALAPPDATA%\ClaudeCodeServerAC. No machine-wide policy changes.
$ErrorActionPreference = 'Stop'
if (-not ('BrowserAppContainer' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
}
$sid = [BrowserAppContainer]::Profile('ClaudeDevcontainer.CodeServer')
$root = Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerAC'
New-Item -ItemType Directory -Force $root | Out-Null

$acl = Get-Acl $root
$appContainer = [Security.Principal.SecurityIdentifier]::new($sid)
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $appContainer, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
Set-Acl $root $acl
# Low integrity, inherited, so that the AppContainer can write here.
& icacls.exe $root /setintegritylevel '(OI)(CI)L' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not set integrity level on the dedicated data directory' }
