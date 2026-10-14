param([string]$Url='http://ide.localhost:8000/?folder=/workspace')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
$root=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoAC\Native'
New-Item -ItemType Directory -Force $root | Out-Null
$bin=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoBin\final'
$status=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoSetup\network-setup.status'
if(-not(Test-Path $status) -or (Get-Content $status -Raw).Trim() -ne 'SUCCESS') {throw 'Network setup has not succeeded'}
Add-Type -Path (Join-Path $PSScriptRoot 'ProxyFirewall.cs')
$sid=[BrowserAppContainer]::Profile('BrowserIdeDemo.StandardIsolation')
[ProxyFirewall]::Verify($sid)
$env:TEMP=Join-Path $root 'Temp'; $env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
[BrowserAppContainer]::Launch('BrowserIdeDemo.StandardIsolation',(Join-Path $bin 'BrowserHost.exe'),('"'+$root+'" "'+$Url+'"'),15)
Get-Content (Join-Path $root 'host.log') -Tail 8
