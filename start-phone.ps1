param([string]$Address = '', [string[]]$AdditionalAddress = @(),
      [string]$TlsCert = '', [string]$TlsKey = '', [string]$TlsCa = '', [switch]$AllowInsecureLan)
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
    $serverArgs = @('scripts/phone_server.py', '--address', $Address)
    foreach ($item in $AdditionalAddress) { $serverArgs += @('--additional-address', $item) }
    if ($TlsCert) { $serverArgs += @('--tls-cert', (Resolve-Path -LiteralPath $TlsCert).Path) }
    if ($TlsKey) { $serverArgs += @('--tls-key', (Resolve-Path -LiteralPath $TlsKey).Path) }
    if ($TlsCa) { $serverArgs += @('--tls-ca', (Resolve-Path -LiteralPath $TlsCa).Path) }
    if ($AllowInsecureLan) { $serverArgs += '--allow-insecure-lan' }
    & $python @serverArgs
    if ($LASTEXITCODE -ne 0) { throw 'Phone server stopped with an error. Inspect runtime/phone-server logs.' }
} finally { Pop-Location }
