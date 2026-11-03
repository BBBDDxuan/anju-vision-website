$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $testRun = Join-Path $projectRoot ('runtime\pytest-' + [guid]::NewGuid().ToString('N'))
    & .\.venv\Scripts\python.exe -m pytest -q --tb=short --basetemp $testRun
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
} finally { Pop-Location }
