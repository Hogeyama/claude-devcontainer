param([int]$WaitSeconds=15,[string]$DiagnosticFlags='',[switch]$DebugLaunch,[switch]$NodeMode,[switch]$MappedDrives,[switch]$ExtensionProbe,[string]$BinaryDirectory='vscode',[switch]$RemoteProbe,[switch]$ConptyDll)
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
Add-Type -Path (Join-Path $PSScriptRoot 'ProxyFirewall.cs')
$name='BrowserIdeDemo.StandardIsolation'
$sid=[BrowserAppContainer]::Profile($name)
[ProxyFirewall]::Verify($sid)
$source=Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code'
$bin=Join-Path $env:LOCALAPPDATA ('BrowserIdeDemoBin\'+$BinaryDirectory)
if(-not(Test-Path (Join-Path $bin 'Code.exe'))) {
    New-Item -ItemType Directory -Force $bin | Out-Null
    Copy-Item (Join-Path $source '*') $bin -Recurse
}
$acl=Get-Acl $bin
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl $bin $acl
$root=Join-Path $env:LOCALAPPDATA ('BrowserIdeDemoAC\VSCode-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $root | Out-Null
$physicalRoot=$root
$hostCanary=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoSetup\native-host-canary.txt'
if($ExtensionProbe){'harmless-host-sentinel' | Set-Content $hostCanary -Encoding ASCII}
$mapped=@()
if($MappedDrives) {
    $free=@(90..68 | ForEach-Object {([char]$_).ToString()+':'} | Where-Object {([IO.Directory]::GetLogicalDrives() -notcontains ($_+'\'))})
    if($free.Count -lt 2){throw 'Need two unused drive letters for isolated path experiment'}
    & subst.exe $free[0] $bin; if($LASTEXITCODE -ne 0){throw 'Binary drive mapping failed'}
    $mapped+= $free[0]
    & subst.exe $free[1] $root; if($LASTEXITCODE -ne 0){& subst.exe $free[0] /D;throw 'Data drive mapping failed'}
    $mapped+= $free[1]
    $bin=$free[0]+'\'; $root=$free[1]+'\'
}
$env:TEMP=Join-Path $root 'Temp'; $env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:APPDATA=Join-Path $root 'Roaming'; $env:LOCALAPPDATA=Join-Path $root 'Local'
$env:USERPROFILE=Join-Path $root 'Home'; $env:HOME=$env:USERPROFILE
foreach($directory in @($env:APPDATA,$env:LOCALAPPDATA,$env:USERPROFILE)) {New-Item -ItemType Directory -Force $directory | Out-Null}
New-Item -ItemType Directory -Force (Join-Path $env:LOCALAPPDATA 'Packages\browseridedemo.standardisolation\AC\Temp') | Out-Null
$exe=Join-Path $bin 'Code.exe'
$arguments='--new-window --user-data-dir="'+(Join-Path $root 'User')+'" --extensions-dir="'+(Join-Path $root 'Extensions')+'" --skip-welcome --skip-release-notes --disable-updates --disable-telemetry --proxy-server=http://127.0.0.1:18081 --proxy-bypass-list=<-loopback> --disable-quic --enable-logging --log-file="'+(Join-Path $root 'electron.log')+'" '+$DiagnosticFlags
if($NodeMode) {
    $env:ELECTRON_RUN_AS_NODE='1'
    $script=Join-Path $root 'node-check.cjs'
    "const fs=require('fs');const log=process.env.TEMP+'/node-check.json';fs.writeFileSync(log,JSON.stringify({pid:process.pid,versions:process.versions}));setTimeout(()=>fs.appendFileSync(log,' timer'),1000);fs.promises.readFile(__filename).then(()=>fs.appendFileSync(log,' read'));setTimeout(()=>process.exit(0),3000);" | Set-Content $script -Encoding ASCII
    $arguments='"'+$script+'"'
}
if($ExtensionProbe) {
    $ext=Join-Path $root 'Extensions\local-demo.native-boundary-probe-0.0.1'
    New-Item -ItemType Directory -Force $ext | Out-Null
    Copy-Item (Join-Path $PSScriptRoot '..\native-extension\*') $ext
    $manifest=@(@{identifier=@{id='local-demo.native-boundary-probe'};version='0.0.1';location=@{'$mid'=1;path=('/'+$ext.Replace('\','/'));scheme='file'};relativeLocation='local-demo.native-boundary-probe-0.0.1'})
    [IO.File]::WriteAllText((Join-Path $root 'Extensions\extensions.json'),(ConvertTo-Json -InputObject $manifest -Depth 5),[Text.UTF8Encoding]::new($false))
    $settings=Join-Path $root 'User\User'
    New-Item -ItemType Directory -Force $settings | Out-Null
    '{"terminal.integrated.defaultProfile.windows":"Command Prompt","terminal.integrated.profiles.windows":{"Command Prompt":{"path":"C:\\Windows\\System32\\cmd.exe","args":["/d"]}},"security.workspace.trust.enabled":false,"update.mode":"none","telemetry.telemetryLevel":"off"}' | Set-Content (Join-Path $settings 'settings.json') -Encoding UTF8
    $arguments+=' --extensionDevelopmentPath="'+$ext+'"'
    if(-not $RemoteProbe){$arguments+=' "'+$env:USERPROFILE+'"'}
    if($ConptyDll){$file=Join-Path $settings 'settings.json';$config=Get-Content $file -Raw | ConvertFrom-Json;$config | Add-Member 'terminal.integrated.windowsUseConptyDll' $true -Force;[IO.File]::WriteAllText($file,($config | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))}
    $env:BROWSER_IDE_PROBE_ROOT=$root; $env:BROWSER_IDE_HOST_CANARY=$hostCanary
}
if($RemoteProbe) {
    $resolver=Join-Path $root 'Resolver'
    New-Item -ItemType Directory -Force $resolver | Out-Null
    Copy-Item (Join-Path $PSScriptRoot '..\native-resolver\*') $resolver
    $arguments+=' --extensionDevelopmentPath="'+$resolver+'" --enable-proposed-api=local-demo.native-resolver --folder-uri="vscode-remote://isolated-demo+container/workspace"'
}
$result=[BrowserAppContainer]::LaunchDiagnostic($name,$exe,$arguments,$WaitSeconds,(Join-Path $root 'stdio.log'),$DebugLaunch.IsPresent) | ConvertFrom-Json
$result | Add-Member root $root
$result | Add-Member physicalRoot $physicalRoot
$result | Add-Member version ((Get-Item $exe).VersionInfo.ProductVersion)
$result | Add-Member flags $DiagnosticFlags
$result | ConvertTo-Json | Set-Content (Join-Path $root 'launch.json') -Encoding UTF8
$result | ConvertTo-Json
Get-Process -Id $result.pid -ErrorAction SilentlyContinue | Select-Object Id,MainWindowHandle,MainWindowTitle
if(Test-Path (Join-Path $root 'electron.log')) {Get-Content (Join-Path $root 'electron.log') -Tail 30}

Get-Content (Join-Path $root 'stdio.log') -Tail 40
if(Test-Path (Join-Path $root 'stdio.log.debug')) {Get-Content (Join-Path $root 'stdio.log.debug')}

if($result.waitResult -eq 0) {foreach($drive in $mapped){& subst.exe $drive /D}}
