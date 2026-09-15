param(
    [switch]$SkipAssetGeneration
)

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
    if ($SkipAssetGeneration) {
        Write-Host 'Skipping asset generation; keeping the existing approved icons and sounds.'
    } else {
        & (Join-Path $projectRoot 'build-taskbar-icons.ps1')
        & (Join-Path $projectRoot 'build-milestone-sound.ps1')
    }

    # Python 3.14 on Windows ships Tcl/Tk as zip archives that PyInstaller's
    # Tcl/Tk hooks cannot collect. Stage them explicitly when present; normal
    # (unzipped) Tcl/Tk installations are left to the standard hooks.
    $addData = @('assets;assets')
    $prepareTk = Join-Path $projectRoot 'scripts\prepare_tk_data.py'
    $buildDir = Join-Path $projectRoot 'build'
    $tkInfoJson = & $python.Source $prepareTk '--build-dir' $buildDir | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Tcl/Tk staging failed with exit code $LASTEXITCODE. Do not publish the existing executable."
    }
    $tkInfo = $tkInfoJson | ConvertFrom-Json
    if ($tkInfo.zip_based) {
        if (-not $tkInfo.tcl_library -or -not $tkInfo.tk_library) {
            throw 'Tcl/Tk staging reported zip-based libraries but did not return both paths.'
        }
        Write-Host "Staging zip-based Tcl/Tk data from $($tkInfo.tcl_source) and $($tkInfo.tk_source)"
        $addData += "$($tkInfo.tcl_library);_tcl_data"
        $addData += "$($tkInfo.tk_library);_tk_data"
    }

    $pyiArgs = @(
        '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'CodexUsageCounter',
        '--icon', $icon
    )
    foreach ($entry in $addData) {
        $pyiArgs += '--add-data'
        $pyiArgs += $entry
    }
    $pyiArgs += 'codex_usage_counter.py'

    & $python.Source -m PyInstaller @pyiArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE. Do not publish the existing executable."
    }
} finally {
    Pop-Location
}
