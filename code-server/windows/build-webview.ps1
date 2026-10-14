# Compiles BrowserHost.exe (BrowserHost.cs + AppContainer.cs) with the .NET
# Framework C# compiler into %LOCALAPPDATA%\ClaudeCodeServerBin\final, which
# the AppContainer may read and run but not change. Then launches it, unless
# -BuildOnly. -Sdk is the extracted WebView2 SDK NuGet package.
param(
    [Parameter(Mandatory=$true)][string]$Sdk,
    [string]$Url = 'http://ide.localhost:8000/?folder=/workspace',
    [switch]$BuildOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
$sid = [BrowserAppContainer]::Profile('ClaudeDevcontainer.CodeServer')

$bin = Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerBin\final'
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $Sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll') $bin
Copy-Item (Join-Path $Sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll') $bin
Copy-Item (Join-Path $Sdk 'runtimes\win-x64\native\WebView2Loader.dll') $bin
Copy-Item (Join-Path $PSScriptRoot 'BrowserHost.exe.config') $bin

$acl = Get-Acl $bin
$appContainer = [Security.Principal.SecurityIdentifier]::new($sid)
$acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $appContainer, 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
Set-Acl $bin $acl

# BrowserHost.exe refuses to run outside the AppContainer with this SID.
$identity = Join-Path $bin 'ExpectedIdentity.cs'
('internal static class CodeServerIdentity { public const string Sid="' + $sid + '"; }') |
    Set-Content $identity -Encoding ASCII

$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$arguments = @(
    '/nologo'
    '/target:winexe'
    '/platform:x64'
    ('/win32manifest:' + (Join-Path $PSScriptRoot 'BrowserHost.manifest'))
    ('/out:' + (Join-Path $bin 'BrowserHost.exe'))
    '/reference:System.Windows.Forms.dll'
    '/reference:System.Drawing.dll'
    '/reference:System.Web.Extensions.dll'
    ('/reference:' + (Join-Path $bin 'Microsoft.Web.WebView2.Core.dll'))
    ('/reference:' + (Join-Path $bin 'Microsoft.Web.WebView2.WinForms.dll'))
    (Join-Path $PSScriptRoot 'BrowserHost.cs')
    (Join-Path $PSScriptRoot 'AppContainer.cs')
    $identity
)
& $csc @arguments
if ($LASTEXITCODE -ne 0) { throw 'WebView host compilation failed' }

if ($BuildOnly) {
    Write-Output $bin
    return
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch.ps1') -Url $Url
if ($LASTEXITCODE -ne 0) { throw 'Launch failed' }
