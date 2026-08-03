param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir 'Codex Usage Counter.lnk'
$executable = Join-Path $projectRoot 'dist\CodexUsageCounter.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    $executable = Join-Path $projectRoot 'CodexUsageCounter.exe'
}

if ($Remove) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "Removed $shortcutPath"
    } else {
        Write-Host 'Startup shortcut was not installed.'
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Build the app first: $projectRoot\build.ps1"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = Split-Path -Parent $executable
$shortcut.Description = 'Always-on-top Codex usage counter'
$shortcut.IconLocation = "$executable,0"
$shortcut.Save()
Write-Host "Installed $shortcutPath"
