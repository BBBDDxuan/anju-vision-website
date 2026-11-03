param(
    [string]$Source = 'mock',
    [string]$Backend = 'http://127.0.0.1:8010',
    [string]$Config = '',
    [switch]$Offline,
    [switch]$Gpu,
    [ValidateRange(0, 1000000)][int]$MaxFrames = 0,
    [string]$StateDir = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$previousPath = $env:PATH
$previousPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    if ($Gpu) {
        $pythonPath = Join-Path $projectRoot '.venv-gpu\Scripts\python.exe'
        $venvConfig = Join-Path $projectRoot '.venv-gpu\pyvenv.cfg'
        if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $venvConfig)) {
            throw 'GPU environment is missing; CPU defaults are unchanged.'
        }
        $homeLine = Get-Content -LiteralPath $venvConfig | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
        if (-not $homeLine) { throw 'GPU base environment is not configured.' }
        $gpuHome = ($homeLine -replace '^home\s*=\s*', '').Trim()
        if (-not (Test-Path -LiteralPath (Join-Path $gpuHome 'python.exe'))) { throw 'GPU base interpreter is missing.' }
        $env:PATH = "$gpuHome;$(Join-Path $gpuHome 'Library\bin');$(Join-Path $gpuHome 'Scripts');$previousPath"
        if (-not $Config) { $Config = Join-Path $projectRoot 'ai-service\config.camera-gpu.json' }
    }
    if (-not $StateDir) {
        $StateDir = Join-Path $projectRoot $(if ($Gpu) { 'runtime\ai-gpu' } else { 'runtime\ai' })
    }
    $env:PYTHONPATH = Join-Path $projectRoot 'ai-service'
    $aiArgs = @('--source', $Source, '--backend', $Backend, '--state-dir', $StateDir)
    if ($Config) { $aiArgs += @('--config', $Config) }
    if ($Offline) { $aiArgs += '--offline' }
    if ($MaxFrames -gt 0) { $aiArgs += @('--max-frames', "$MaxFrames") }
    # dotenv runs inside Python so secrets never appear as command line arguments.
    & $pythonPath -c 'from dotenv import load_dotenv; load_dotenv(".env", override=False); from ai_service.cli import main; import sys; sys.exit(main())' @aiArgs
    if ($LASTEXITCODE -ne 0) { throw 'AI process failed; local spool is retained.' }
} finally {
    $env:PATH = $previousPath
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
