param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $Python) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $Python = (& py -3.12 -c 'import sys; print(sys.executable)')
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, or use setup.ps1 -Python with an interpreter path.' }
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $Python = (& python -c 'import sys; print(sys.executable)')
            if ($LASTEXITCODE -ne 0) { throw 'Python could not be started.' }
        } else { throw 'Install Python 3.12 first.' }
    }
    & $Python -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python version check failed.' }
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    Write-Host 'Setup complete. Run start.bat to open the local web service.'
    Write-Host 'Configure FFmpeg, API access, and optional camera detection as described in README.md.'
} finally { Pop-Location }
