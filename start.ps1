param(
    [string]$Python = '',
    [ValidateRange(1, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not $Python) {
    $Python = $env:SHIJI_PYTHON
    foreach ($candidate in @(
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Join-Path $projectRoot 'elder-safety\.venv\Scripts\python.exe')
    )) {
        if (-not $Python -and (Test-Path -LiteralPath $candidate)) { $Python = $candidate }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw 'Python environment not found. Run setup.bat first, or pass -Python with the interpreter path.'
}
Push-Location -LiteralPath $projectRoot
try {
    & $Python -c 'import fastapi, uvicorn, cv2, httpx, jsonschema, dotenv'
    if ($LASTEXITCODE -ne 0) { throw 'Missing dependencies: run pip install -r requirements.txt.' }
    if (Test-Path -LiteralPath (Join-Path $projectRoot 'scripts\prepare_team.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'runtime\team-camera.json'))) {
            $detector = (& $Python -c 'from backend.config import Settings; print(Settings.from_env().safety_python)')
            if ($LASTEXITCODE -ne 0) { throw 'Unable to read the detector interpreter setting.' }
            $configPython = if (Test-Path -LiteralPath $detector) { $detector } else { $Python }
            & $configPython scripts/prepare_team.py --device auto --skip-hashes
            if ($LASTEXITCODE -ne 0) { throw 'Team configuration preparation failed.' }
        }
    }
    if (Test-Path -LiteralPath (Join-Path $projectRoot 'demo-recording\manifest.json')) {
        & $Python scripts/import_demo.py
        if ($LASTEXITCODE -ne 0) { throw 'Bundled real recording integrity check failed.' }
    }
    & $Python -c "from backend.config import Settings; import shutil; from pathlib import Path; s=Settings.from_env(); print('Media tools: ' + ('ready' if all(Path(p).is_file() or shutil.which(p) for p in (s.ffmpeg,s.ffprobe)) else 'configure FFMPEG_PATH and FFPROBE_PATH in .env before uploading'))"
    if ($LASTEXITCODE -ne 0) { throw 'Invalid configuration. Check your .env file.' }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
        Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    Write-Host "Anju Vision: http://127.0.0.1:$Port (Ctrl+C to stop)"
    & $Python -m backend.local_server --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Server stopped with an error. Check the message above.' }
} finally { Pop-Location }
