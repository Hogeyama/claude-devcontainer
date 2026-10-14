param([Parameter(Mandatory=$true)][string]$Sdk,[string]$Url='http://ide.localhost:8000/?folder=/workspace',[switch]$BuildOnly)
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
$sid=[BrowserAppContainer]::Profile('BrowserIdeDemo.StandardIsolation')
$bin=Join-Path $env:LOCALAPPDATA 'BrowserIdeDemoBin\final'
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $Sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll') $bin
Copy-Item (Join-Path $Sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll') $bin
Copy-Item (Join-Path $Sdk 'runtimes\win-x64\native\WebView2Loader.dll') $bin
Copy-Item (Join-Path $PSScriptRoot 'BrowserHost.exe.config') $bin
$acl=Get-Acl $bin
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl $bin $acl
('internal static class BrowserDemoIdentity { public const string Sid="'+$sid+'"; }') | Set-Content (Join-Path $bin 'ExpectedIdentity.cs') -Encoding ASCII
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo ('/win32manifest:'+(Join-Path $PSScriptRoot 'BrowserHost.manifest')) /target:winexe /platform:x64 ('/out:'+(Join-Path $bin 'BrowserHost.exe')) /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ('/reference:'+(Join-Path $bin 'Microsoft.Web.WebView2.Core.dll')) ('/reference:'+(Join-Path $bin 'Microsoft.Web.WebView2.WinForms.dll')) (Join-Path $PSScriptRoot 'BrowserHost.cs') (Join-Path $PSScriptRoot 'BrowserIdeChecks.cs') (Join-Path $PSScriptRoot 'AppContainer.cs') (Join-Path $bin 'ExpectedIdentity.cs')
if($LASTEXITCODE -ne 0) { throw 'WebView host compilation failed' }
if($BuildOnly){Write-Output $bin;return}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch.ps1') -Url $Url
if($LASTEXITCODE -ne 0){throw 'Launch failed'}
