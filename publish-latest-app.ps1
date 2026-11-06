<# Run this script as the same Windows user that runs the backend. #>
[CmdletBinding()]
param(
    [string]$Notes = '',
    [string]$ToolsRoot = 'D:\AndroidDev'
)
$ErrorActionPreference = 'Stop'
$inputDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'App最新版安装包'
if (-not (Test-Path -LiteralPath $inputDir -PathType Container)) {
    throw "APK folder not found: $inputDir"
}
$apks = @(Get-ChildItem -LiteralPath $inputDir -Filter '*.apk' -File | Sort-Object Name)
if (-not $apks.Count) { throw "No APK files in: $inputDir" }
if (-not $Notes) { $Notes = '安居视界版本更新' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$publishArgs = @((Join-Path $PSScriptRoot 'scripts\publish_app_update.py'),
    '--sdk', (Join-Path $ToolsRoot 'sdk'), '--java', (Join-Path $ToolsRoot 'jdk\bin\java.exe'),
    '--notes', $Notes)
foreach ($apk in $apks) { $publishArgs += @('--apk', $apk.FullName) }
& $python @publishArgs
if ($LASTEXITCODE -ne 0) { throw "APK publication failed (exit $LASTEXITCODE)." }
