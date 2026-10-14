param([ValidateSet('control','restricted','ide-control','ide-restricted')][string]$Mode='restricted')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
Add-Type -Path (Join-Path $PSScriptRoot 'ProxyFirewall.cs')
$sid=[BrowserAppContainer]::Profile('BrowserIdeDemo.StandardIsolation')
[ProxyFirewall]::Verify($sid)
$root=Join-Path $env:LOCALAPPDATA ('BrowserIdeDemoAC\Probe-'+$Mode)
New-Item -ItemType Directory -Force $root | Out-Null
$bin=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoBin\final'
$env:TEMP=Join-Path $root 'Temp';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$url=if($Mode.StartsWith('ide-')){'http://ide.localhost:8000/?folder=/workspace'}else{'http://preview.localhost:8001/'}
$launch=[BrowserAppContainer]::Launch('BrowserIdeDemo.StandardIsolation',(Join-Path $bin 'BrowserHost.exe'),('"'+$root+'" "'+$url+'" '+$Mode),60) | ConvertFrom-Json
$report=Join-Path $root 'browser-probe.json'
if($launch.exitCode -ne 0 -or -not(Test-Path $report)) { if(Test-Path (Join-Path $root 'host.log')) {Get-Content (Join-Path $root 'host.log') -Tail 15}; throw 'Browser probe failed or timed out' }
[IO.File]::ReadAllText($report)
