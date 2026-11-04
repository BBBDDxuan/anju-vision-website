param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$previousPath = $env:PATH
$previousPythonPath = $env:PYTHONPATH
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:PYTHONUTF8 = '1'
    $env:PATH = (Join-Path $PSScriptRoot 'elder-safety\tools\ffmpeg') + ';' + $previousPath
    if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
    & $Python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Video API tests failed.' }
    $env:PYTHONPATH = Join-Path $PSScriptRoot 'elder-safety\backend'
    & $Python -m pytest elder-safety/backend/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Safety API tests failed.' }
    $env:PYTHONPATH = (Join-Path $PSScriptRoot 'elder-safety\backend') + ';' + (Join-Path $PSScriptRoot 'elder-safety\ai-service')
    $detectorRuntime = Join-Path $PSScriptRoot 'elder-safety\runtime'
    New-Item -ItemType Directory -Path $detectorRuntime -Force | Out-Null
    $tempRoot = Join-Path $detectorRuntime ('pytest-' + [guid]::NewGuid().ToString('N'))
    & $Python -m pytest elder-safety/ai-service/tests -q --basetemp $tempRoot
    if ($LASTEXITCODE -ne 0) { throw 'Detector tests failed.' }
} finally {
    $env:PATH = $previousPath
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
