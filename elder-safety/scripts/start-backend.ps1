param([int]$Port = 8010, [string]$BindAddress = '127.0.0.1')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
        & $pythonPath scripts/configure.py
        if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed.' }
    }
    $env:PYTHONPATH = Join-Path $projectRoot 'backend'
    & $pythonPath -m uvicorn safety_backend.main:create_app --factory --host $BindAddress --port $Port --no-access-log
    if ($LASTEXITCODE -ne 0) { throw 'Backend stopped with an error.' }
} finally { Pop-Location }
