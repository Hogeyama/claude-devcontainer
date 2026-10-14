$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
$bin=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoBin\probes'
New-Item -ItemType Directory -Force $bin | Out-Null
$sid=[BrowserAppContainer]::Profile('BrowserIdeDemo.StandardIsolation')
$acl=Get-Acl $bin
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl $bin $acl
$root=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoAC'
$trusted=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoSetup'
$exe=Join-Path $bin 'BoundaryProbe.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /platform:x64 ('/out:'+$exe) /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'BoundaryProbe.cs')
if($LASTEXITCODE -ne 0) {throw 'Probe build failed'}
$canary=Join-Path $bin 'ProtocolCanary.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:winexe /platform:x64 ('/out:'+$canary) (Join-Path $PSScriptRoot 'ProtocolCanary.cs') (Join-Path $PSScriptRoot 'AppContainer.cs')
if($LASTEXITCODE -ne 0){throw 'Canary build failed'}
$key='HKCU:\Software\Classes\browseridedemo'
if(Test-Path $key){throw 'Existing protocol registration found; refusing to overwrite'}
$marker=Join-Path $trusted 'external-protocol-marker.txt'
$sentinel=Join-Path $trusted 'host-sentinel.txt'
Set-Content $sentinel 'DEMO_ONLY_HOST_FILE'
$tcp4=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,18082)
$tcp6=[Net.Sockets.TcpListener]::new([Net.IPAddress]::IPv6Loopback,18084)
$udp4=[Net.Sockets.UdpClient]::new([Net.IPEndPoint]::new([Net.IPAddress]::Loopback,18083))
$udp6=[Net.Sockets.UdpClient]::new([Net.IPEndPoint]::new([Net.IPAddress]::IPv6Loopback,18085))
try {
    New-Item $key -Force | Out-Null
    Set-Item $key 'Browser IDE harmless test protocol'
    New-ItemProperty $key -Name 'URL Protocol' -Value '' -Force | Out-Null
    New-Item ($key+'\shell\open\command') -Force | Out-Null
    Set-Item ($key+'\shell\open\command') ('"'+$canary+'" "'+$marker+'"')
    Remove-Item $marker -ErrorAction SilentlyContinue
    $tcp4.Start();$tcp6.Start()
    $control=Join-Path $trusted 'boundary-control.json'
    $p=Start-Process $exe -ArgumentList @(('"'+$control+'"'),('"'+$sentinel+'"')) -Wait -PassThru
    if($p.ExitCode -ne 0){throw 'Positive control failed'}
    for($i=0;$i -lt 30 -and -not(Test-Path $marker);$i++){Start-Sleep -Milliseconds 100}
    if(-not(Test-Path $marker)){throw 'Protocol positive control did not execute'}
    $protocolControl=[IO.File]::ReadAllText($marker)
    Remove-Item $marker
    $positive=Get-Content $control -Raw -Encoding UTF8 | ConvertFrom-Json
    if($positive.other_loopback -ne 'connected' -or $positive.ipv6_loopback -ne 'connected') {throw 'TCP listeners did not work in positive control'}
    $ep=[Net.IPEndPoint]::new([Net.IPAddress]::Any,0)
    if($udp4.Available -eq 0 -or $udp6.Available -eq 0){throw 'UDP positive control did not arrive'}
    $null=$udp4.Receive([ref]$ep); $ep=[Net.IPEndPoint]::new([Net.IPAddress]::IPv6Any,0); $null=$udp6.Receive([ref]$ep)
    while($tcp4.Pending()){$tcp4.AcceptTcpClient().Dispose()}; while($tcp6.Pending()){$tcp6.AcceptTcpClient().Dispose()}
    Remove-Item ($sentinel+'.written')
    $env:TEMP=Join-Path $root 'Temp'; $env:TMP=$env:TEMP
    $report=Join-Path $root 'boundary-restricted.json'
    $launch=[BrowserAppContainer]::Launch('BrowserIdeDemo.StandardIsolation',$exe,('"'+$report+'" "'+$sentinel+'"'),15) | ConvertFrom-Json
    if($launch.exitCode -ne 0){throw 'AppContainer probe failed'}
    Start-Sleep -Milliseconds 1500
    $restricted=Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    $result=[ordered]@{date=[DateTime]::UtcNow.ToString('o');launch=$launch;control=$positive;restricted=$restricted;unexpectedTcp=($tcp4.Pending() -or $tcp6.Pending());unexpectedUdp=($udp4.Available -gt 0 -or $udp6.Available -gt 0);hostWrite=(Test-Path ($sentinel+'.written'));protocolControl=$protocolControl;protocolEscaped=(Test-Path $marker)}
    $result | ConvertTo-Json -Depth 5 | ForEach-Object { [IO.File]::WriteAllText((Join-Path $trusted 'boundary-verification.json'),$_); $_ }
    if($restricted.proxy -ne 'connected' -or $result.unexpectedTcp -or $result.unexpectedUdp -or $result.hostWrite -or $result.protocolEscaped -or $restricted.host_sentinel -ne 'UnauthorizedAccessException') {throw 'Boundary verification failed'}
} finally { Remove-Item $key -Recurse -ErrorAction SilentlyContinue; Remove-Item $marker -ErrorAction SilentlyContinue; $tcp4.Stop();$tcp6.Stop();$udp4.Dispose();$udp6.Dispose();Remove-Item $sentinel,($sentinel+'.written') -ErrorAction SilentlyContinue }
