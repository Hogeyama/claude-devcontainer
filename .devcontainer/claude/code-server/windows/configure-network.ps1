# Run as Administrator (prepare.ps1 and remove-network.ps1 elevate it).
# Installs, or with -Remove deletes, the WFP filters of ProxyFirewall.cs and
# the loopback exemption, for the ClaudeDevcontainer.CodeServer AppContainer
# only; no other app's rules change. The filters are persistent, so they
# outlive this shell.
#
# Order matters: the AppContainer may reach loopback only while the filters
# confine it to the proxy. So the filters go in before the exemption, and the
# exemption goes before the filters are removed; on failure the filters stay.
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
# launch.ps1 runs only when this file says SUCCESS.
$status = Join-Path $PSScriptRoot 'network-setup.status'
Start-Transcript -Path (Join-Path $PSScriptRoot 'network-setup.log') -Force | Out-Null
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'AppContainer.cs')
    Add-Type -Path (Join-Path $PSScriptRoot 'ProxyFirewall.cs')
    $sid = [BrowserAppContainer]::Profile('ClaudeDevcontainer.CodeServer')
    if ($Remove) {
        & CheckNetIsolation.exe LoopbackExempt -d "-p=$sid"
        if ($LASTEXITCODE -ne 0) { throw 'Failed removing loopback exemption; filters retained' }
        [ProxyFirewall]::Remove()
        'REMOVED' | Set-Content $status
    } else {
        [ProxyFirewall]::Install($sid)
        [ProxyFirewall]::Verify($sid)
        & CheckNetIsolation.exe LoopbackExempt -a "-p=$sid"
        if ($LASTEXITCODE -ne 0) { throw 'Loopback exemption failed; blocking filters retained' }
        'SUCCESS' | Set-Content $status
    }
} catch {
    ($_ | Out-String) | Set-Content $status
    throw
} finally {
    Stop-Transcript | Out-Null
}
