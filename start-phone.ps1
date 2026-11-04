param([string]$Address = '')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup-team.ps1 first.' }
if (-not $Address) {
    $addresses = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } |
        ForEach-Object { $_.IPv4Address.IPAddress } | Where-Object {
            $_ -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)'
        } | Select-Object -Unique)
    if ($addresses.Count -ne 1) { throw 'Supply -Address with this computer''s LAN IPv4 address.' }
    $Address = $addresses[0]
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $python scripts/phone_server.py --address $Address
    if ($LASTEXITCODE -ne 0) { throw 'Phone server stopped with an error. Inspect runtime/phone-server logs.' }
} finally { Pop-Location }
