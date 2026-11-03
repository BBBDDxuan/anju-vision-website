param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [string]$Config = '',
    [string]$Output = '',
    [int]$MaxFrames = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$inputPath = (Resolve-Path -LiteralPath $InputFile).Path
if (-not $Config) { $Config = Join-Path $projectRoot 'ai-service\config.local-models.json' }
$configPath = (Resolve-Path -LiteralPath $Config).Path
$env:PYTHONPATH = Join-Path $projectRoot 'ai-service'
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @('-m', 'ai_service.local_validate', '--input', $inputPath, '--config', $configPath)
if ($Output) { $arguments += @('--output', $Output) }
if ($MaxFrames -gt 0) { $arguments += @('--max-frames', $MaxFrames) }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Local model validation failed; inspect output above.' }
