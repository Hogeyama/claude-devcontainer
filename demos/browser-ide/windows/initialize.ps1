# Creates only the dedicated profile/data directory; no machine policy changes.
$ErrorActionPreference='Stop'
if(-not ('BrowserAppContainer' -as [type])) {Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')}
$sid=[BrowserAppContainer]::Profile('BrowserIdeDemo.StandardIsolation')
$root=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoAC'
New-Item -ItemType Directory -Force $root | Out-Null
$acl=Get-Acl $root
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl $root $acl
& icacls.exe $root /setintegritylevel '(OI)(CI)L' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not set integrity level on the dedicated data directory'}
