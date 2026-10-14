# Administrator only. Scope: ClaudeDevcontainer.CodeServer SID; no other app rules changed.
# Persistent filters survive the launching shell. Remove the exemption before removing filters.
param([switch]$Remove)
$ErrorActionPreference='Stop'
$log=Join-Path $PSScriptRoot 'network-setup.log'
Start-Transcript -Path $log -Force | Out-Null
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
    Add-Type -Path (Join-Path $PSScriptRoot 'ProxyFirewall.cs')
    $sid=[BrowserAppContainer]::Profile('ClaudeDevcontainer.CodeServer')
    if($Remove) {
        & CheckNetIsolation.exe LoopbackExempt -d "-p=$sid"
        if($LASTEXITCODE -ne 0) {throw 'Failed removing loopback exemption; filters retained'}
        [ProxyFirewall]::Remove()
    } else {
        [ProxyFirewall]::Install($sid)
        [ProxyFirewall]::Verify($sid)
        & CheckNetIsolation.exe LoopbackExempt -a "-p=$sid"
        if($LASTEXITCODE -ne 0) {throw 'Loopback exemption failed; blocking filters retained'}
    }
    $(if($Remove){'REMOVED'}else{'SUCCESS'}) | Set-Content (Join-Path $PSScriptRoot 'network-setup.status')
} catch {
    ($_ | Out-String) | Set-Content (Join-Path $PSScriptRoot 'network-setup.status')
    throw
} finally { Stop-Transcript | Out-Null }
