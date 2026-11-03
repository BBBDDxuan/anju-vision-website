param(
    [string]$Python = '',
    [string]$DetectorPython = '',
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cuda',
    [switch]$BackendOnly,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Push-Location -LiteralPath $repo
try {
    $cache = Join-Path $repo 'runtime\setup'
    New-Item -ItemType Directory -Path (Join-Path $cache 'tmp') -Force | Out-Null
    $env:PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
    $env:TMP = $env:TEMP = Join-Path $cache 'tmp'
    $env:PYTHONUTF8 = '1'
    $env:PIP_DEFAULT_TIMEOUT = '180'
    if (-not $Python) {
        if (Get-Command py -ErrorAction SilentlyContinue) { $Python = (& py -3 -c 'import sys; print(sys.executable)') }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = (& python -c 'import sys; print(sys.executable)') }
    }
    if (-not $Python) { throw 'Install Python 3.12+, or pass -Python with its absolute path.' }
    & $Python -c 'import sys; assert sys.version_info >= (3,12), "Backend requires Python 3.12+"'
    if ($LASTEXITCODE -ne 0) { throw 'Backend Python version check failed.' }
    $web = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $web)) {
        & $Python -m venv (Join-Path $repo '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Backend environment creation failed.' }
    }
    if (-not $SkipInstall) {
        & $web -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed. Save the complete error output.' }
    }
    foreach ($config in @('.env', 'elder-safety/.env')) {
        if (-not (Test-Path -LiteralPath $config)) {
            Copy-Item -LiteralPath ($config + '.example') -Destination $config
            Write-Host ('Created ' + $config + '; add credentials privately for cloud/camera use.')
        }
    }
    $detector = Join-Path $repo 'elder-safety\.venv-gpu\Scripts\python.exe'
    if (-not $BackendOnly) {
        if (-not $DetectorPython) { $DetectorPython = $Python }
        if (-not (Test-Path -LiteralPath $detector)) {
            & $DetectorPython -m venv (Join-Path $repo 'elder-safety\.venv-gpu')
            if ($LASTEXITCODE -ne 0) { throw 'Detector environment creation failed.' }
        }
        if (-not $SkipInstall) {
            $index = if ($Device -eq 'cuda') { 'https://download.pytorch.org/whl/cu126' } else { 'https://download.pytorch.org/whl/cpu' }
            # Pin the build suffix too: a CPU wheel must not satisfy a CUDA install.
            $build = if ($Device -eq 'cuda') { 'cu126' } else { 'cpu' }
            & $detector -m pip install "torch==2.13.0+$build" "torchvision==0.28.0+$build" --index-url $index
            if ($LASTEXITCODE -ne 0) { throw 'Pinned PyTorch installation failed; do not silently substitute a different version.' }
            & $detector -m pip install -r requirements-detector.txt
            if ($LASTEXITCODE -ne 0) { throw 'Detector dependency installation failed.' }
            & $detector -m pip install --no-deps -e elder-safety/ai-service
            if ($LASTEXITCODE -ne 0) { throw 'Detector package installation failed.' }
        }
        & $detector -c 'import sys, torch; cuda = sys.argv[1] == "cuda"; assert not cuda or (torch.version.cuda is not None and torch.cuda.is_available()), "CUDA unavailable: check NVIDIA driver and CUDA PyTorch, or explicitly select -Device cpu"; print("Detector runtime:", torch.__version__, "CUDA:", torch.cuda.is_available())' $Device
        if ($LASTEXITCODE -ne 0) { throw 'Requested detector device is unavailable; configuration was not switched.' }
        & $detector scripts/prepare_team.py --device $(if ($Device -eq 'cuda') { '0' } else { 'cpu' })
        if ($LASTEXITCODE -ne 0) { throw 'Model/tool verification or configuration generation failed.' }
    } else {
        & $web scripts/prepare_team.py --device cpu
        if ($LASTEXITCODE -ne 0) { throw 'Bundled resource verification failed.' }
    }
    if ($BackendOnly) { & $web scripts/doctor_team.py --backend-only }
    else { & $web scripts/doctor_team.py }
    if ($LASTEXITCODE -ne 0) { throw 'Team runtime self-check failed.' }
    Write-Host 'Team setup complete. Run start.bat for the webpage.'
    Write-Host 'Local safety regression (no cloud): elder-safety\.venv-gpu\Scripts\python.exe scripts\verify_reproduction.py --assets-only'
} finally { Pop-Location }
