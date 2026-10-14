param([string]$BinaryDirectory='vscode')
# Compatibility experiment: modify only the dedicated copy, never the installed VS Code.
$ErrorActionPreference='Stop'
$bin=Join-Path $env:LOCALAPPDATA ('BrowserIdeDemoBin\'+$BinaryDirectory)
$old='\\\\.\\pipe\\'
$new='\\\\.\\pipe\\LOCAL\\'
$count=0
@((Get-Item $bin)) + @(Get-ChildItem $bin -Directory) | ForEach-Object {Join-Path $_.FullName 'resources\app\out'} | Where-Object {Test-Path $_} | ForEach-Object {Get-ChildItem $_ -Recurse -Filter '*.js' -File} | ForEach-Object {
    $text=[IO.File]::ReadAllText($_.FullName)
    if($text.Contains($old)) {
        $updated=$text.Replace($new,$old).Replace($old,$new)
        if($updated -ne $text) {
            if(-not(Test-Path ($_.FullName+'.before-appcontainer'))){Copy-Item $_.FullName ($_.FullName+'.before-appcontainer')}
            [IO.File]::WriteAllText($_.FullName,$updated,[Text.UTF8Encoding]::new($false))
            $count++
        }
    }
}
Write-Output "Patched $count JavaScript files in dedicated copy"
