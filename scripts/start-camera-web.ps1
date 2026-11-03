param([ValidateRange(1,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'elder-safety\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment is missing.' }
$oldNetwork = $env:VLM_ENABLE_NETWORK
$oldCloud = $env:CLOUD_MODE
$oldEncoding = $env:PYTHONUTF8
$oldDataDir = $env:DATA_DIR
Push-Location -LiteralPath $root
try {
    # Process-scoped only: no changes to existing .env, API budget, or cloud setup.
    $env:VLM_ENABLE_NETWORK = 'false'
    $env:CLOUD_MODE = 'false'
    $env:PYTHONUTF8 = '1'
    $env:DATA_DIR = Join-Path $root 'data'
    Write-Host "Local camera workspace: http://127.0.0.1:$Port/workspace"
    Write-Host 'Camera starts only after clicking the preview button. Cloud model calls are disabled in this launch.'
    & $python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port $Port --no-access-log
    if ($LASTEXITCODE -ne 0) { throw 'Local camera website exited with an error.' }
} finally {
    $env:VLM_ENABLE_NETWORK = $oldNetwork
    $env:CLOUD_MODE = $oldCloud
    $env:PYTHONUTF8 = $oldEncoding
    $env:DATA_DIR = $oldDataDir
    Pop-Location
}
