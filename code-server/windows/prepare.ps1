param([switch]$ConfigureNetwork)
$ErrorActionPreference='Stop'
# Run from a trusted host checkout, not from the agent-writable workspace.
$setup=Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerSetup'
New-Item -ItemType Directory -Force $setup | Out-Null
if([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($setup)) {
    Get-ChildItem $PSScriptRoot -File | ForEach-Object {Copy-Item $_.FullName $setup}
}
$version='1.0.4258.31'
$package=Join-Path $setup ('webview2-'+$version+'.zip')
$hash='56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9'
if(-not(Test-Path $package)) {
    Invoke-WebRequest -UseBasicParsing ("https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg") -OutFile $package
}
if((Get-FileHash $package -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash){throw 'WebView2 SDK hash mismatch'}
$sdk=Join-Path $setup ('sdk-'+$version)
if(-not(Test-Path $sdk)){Expand-Archive $package $sdk}
& (Join-Path $setup 'initialize.ps1')
# Build in a separate process so Add-Type definitions never conflict.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setup 'build-webview.ps1') -Sdk $sdk -BuildOnly
if($LASTEXITCODE -ne 0){throw 'Build failed'}
if($ConfigureNetwork) {
    Write-Output 'Configuring network isolation (Windows UAC approval required)...'
    # No visible console: selecting text in conhost can pause output until Enter.
    # NonInteractive also makes any accidental PowerShell prompt fail instead of waiting.
    $p=Start-Process (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $setup 'configure-network.ps1')+'"')) -Wait -PassThru
    if($p.ExitCode -ne 0){throw ('Network setup failed (exit '+$p.ExitCode+'); see '+(Join-Path $setup 'network-setup.log'))}
    Write-Output 'Network isolation configured.'
}
Write-Output ('Prepared. Launch: & "'+(Join-Path $setup 'launch.ps1')+'"')
