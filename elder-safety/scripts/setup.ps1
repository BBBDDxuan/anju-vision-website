param([string]$Python = 'D:\Anaconda\python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    & $venvPython -m pip install --cache-dir runtime/pip-cache -r backend/requirements-dev.txt -r ai-service/requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not (Test-Path -LiteralPath '.env')) {
        & $venvPython scripts/configure.py
        if ($LASTEXITCODE -ne 0) { throw 'Configuration generation failed.' }
    }
    Write-Host 'Setup complete. External providers remain mock by default.'
} finally { Pop-Location }
