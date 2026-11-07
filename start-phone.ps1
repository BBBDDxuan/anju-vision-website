param([string]$Address = '', [string[]]$AdditionalAddress = @(),
      [string]$TlsCert = '', [string]$TlsKey = '', [string]$TlsCa = '', [switch]$AllowInsecureLan,
      [string]$TlsProfile = '')
$ErrorActionPreference = 'Stop'
if ($TlsProfile -and ($TlsCert -or $TlsKey -or $TlsCa -or $AllowInsecureLan)) {
    throw 'Use -TlsProfile by itself, or specify individual TLS files.'
}
$env:PYTHONUTF8 = '1'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup-team.ps1 first.' }
if (-not $Address) {
    # 只取已连通(Up)且有默认网关的网卡；优先有线(802.3)，其次无线，再按接口跃点数排序
    $addresses = @(Get-NetIPConfiguration | Where-Object {
            $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up'
        } | ForEach-Object {
            $adapter = $_.NetAdapter
            $isWired = ($adapter.MediaType -eq '802.3' -and $adapter.PhysicalMediaType -eq '802.3')
            foreach ($ip in $_.IPv4Address) {
                if ($ip.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') {
                    [pscustomobject]@{
                        Address = $ip.IPAddress
                        IsWired = $isWired
                        Metric  = $_.NetIPv4Interface.InterfaceMetric
                    }
                }
            }
        } | Sort-Object -Property @{Expression = 'IsWired'; Descending = $true}, Metric |
        Select-Object -ExpandProperty Address -Unique)
    if ($addresses.Count -eq 0) { throw 'Supply -Address with this computer''s LAN IPv4 address.' }
    $Address = $addresses[0]
    Write-Host "Using LAN address: $Address"
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $serverArgs = @('scripts/phone_server.py', '--address', $Address)
    foreach ($item in $AdditionalAddress) { $serverArgs += @('--additional-address', $item) }
    if ($TlsCert) { $serverArgs += @('--tls-cert', (Resolve-Path -LiteralPath $TlsCert).Path) }
    if ($TlsKey) { $serverArgs += @('--tls-key', (Resolve-Path -LiteralPath $TlsKey).Path) }
    if ($TlsCa) { $serverArgs += @('--tls-ca', (Resolve-Path -LiteralPath $TlsCa).Path) }
    if ($AllowInsecureLan) {
        $serverArgs += '--allow-insecure-lan'
    } elseif ($TlsProfile) {
        $serverArgs += @('--tls-profile', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TlsProfile))
    } elseif (-not ($TlsCert -or $TlsKey -or $TlsCa)) {
        $localData = [Environment]::GetFolderPath('LocalApplicationData')
        if ($localData) {
            $serverArgs += @('--tls-profile', (Join-Path $localData 'AnjuVision\lan-tls'))
        }
    }
    & $python @serverArgs
    if ($LASTEXITCODE -ne 0) { throw 'Phone server stopped with an error. Inspect runtime/phone-server logs.' }
} finally { Pop-Location }
