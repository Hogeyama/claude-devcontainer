$ErrorActionPreference='Stop'
$script=Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerSetup\configure-network.ps1'
Write-Output 'Removing network isolation (Windows UAC approval required)...'
$p=Start-Process (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$script+'"'),'-Remove')
if($p.ExitCode -ne 0){throw ('Network removal failed (exit '+$p.ExitCode+'); see '+(Join-Path $env:LOCALAPPDATA 'ClaudeCodeServerSetup\network-setup.log'))}
Write-Output 'Network isolation removed.'
