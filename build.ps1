$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$icon = Join-Path $projectRoot 'assets\usage-orbit.ico'

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python is not installed.'
}

& $python.Source -c 'import PyInstaller' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is not installed. Run: python -m pip install pyinstaller'
}

Push-Location $projectRoot
try {
    & (Join-Path $projectRoot 'build-taskbar-icons.ps1')
    & (Join-Path $projectRoot 'build-milestone-sound.ps1')
    & $python.Source -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name CodexUsageCounter `
        --icon $icon `
        --add-data 'assets;assets' `
        codex_usage_counter.py
} finally {
    Pop-Location
}
