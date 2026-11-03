param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $Python = $command.Source }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw 'Python not found. Activate the intended environment or pass -Python with its interpreter path.'
}
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
