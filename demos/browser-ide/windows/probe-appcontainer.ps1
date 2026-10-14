param([int]$WaitSeconds=15,[ValidateSet('Edge','Chrome')][string]$Browser='Edge',[string]$DiagnosticFlags='')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
$name='BrowserIdeDemo.StandardIsolation'
$sid=[BrowserAppContainer]::Profile($name)
$root=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoAC'
New-Item -ItemType Directory -Force $root | Out-Null
$acl=Get-Acl $root
$rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow')
$acl.SetAccessRule($rule)
Set-Acl $root $acl
& icacls.exe $root /setintegritylevel '(OI)(CI)L' | Out-Null
if($LASTEXITCODE -ne 0) { throw 'Could not set integrity level on dedicated demo directory' }
$edge=Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
$env:TEMP=Join-Path $root 'Temp'; $env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
if($Browser -eq 'Chrome') { $edge='C:\Program Files\Google\Chrome\Application\chrome.exe' }
$args='--user-data-dir="'+(Join-Path $root ('Profile'+$Browser))+'" --no-first-run --no-default-browser-check --disable-background-networking --disable-sync --enable-logging --log-file="'+(Join-Path $root ($Browser+'.log'))+'" about:blank'
$args+=' '+$DiagnosticFlags
$result=[BrowserAppContainer]::Launch($name,$edge,$args,$WaitSeconds) | ConvertFrom-Json
$result | Add-Member browser $Browser
$result | Add-Member profileSid $sid
$result | Add-Member directory $root
$result | ConvertTo-Json | Tee-Object -FilePath (Join-Path $root 'launch.json')
Get-Process -Id $result.pid -ErrorAction SilentlyContinue | Select-Object Id,MainWindowHandle,MainWindowTitle
if(Test-Path (Join-Path $root ($Browser+'.log'))) { Get-Content (Join-Path $root ($Browser+'.log')) -Tail 25 }
