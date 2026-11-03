param([string]$Python = '', [string]$DetectorPython = '',
      [ValidateSet('cpu','cuda')][string]$Device = 'cpu', [switch]$BackendOnly)
& (Join-Path $PSScriptRoot 'setup-team.ps1') @PSBoundParameters
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
