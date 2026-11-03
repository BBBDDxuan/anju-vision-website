param(
    [string]$BackendRoot = '',
    [string]$Python = '',
    [ValidateRange(1, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1, 65535)][int]$FrontendPort = 5173,
    [switch]$NoBrowser,
    [switch]$SmokeTest
)
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$frontendRoot = Join-Path $repoRoot 'frontend'
$backendProcess = $null
$frontendProcess = $null
$backendUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort"
$oldBackendPort = $env:ANJU_BACKEND_PORT

function Test-LocalPort([int]$Port) {
    $socket = [Net.Sockets.TcpClient]::new()
    try {
        $pending = $socket.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) { return $false }
        $socket.EndConnect($pending)
        return $socket.Connected
    } catch { return $false } finally { $socket.Dispose() }
}

function Read-Health([string]$Url) {
    try {
        $health = Invoke-RestMethod "$Url/api/health" -TimeoutSec 2
        if ($health.status -eq 'ok' -and $null -ne $health.cloud_mode -and $null -ne $health.limits) {
            return $health
        }
    } catch { }
    return $null
}

function Wait-Health([string]$Url, $Process) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        if ($Process -and $Process.HasExited) { throw "Service exited: $Url" }
        $health = Read-Health $Url
        if ($health) { return $health }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service did not become ready: $Url"
}

try {
    if ($BackendPort -eq $FrontendPort) { throw 'Frontend and backend need different ports.' }
    if (Test-LocalPort $FrontendPort) {
        throw "Port $FrontendPort is in use. Stop the old frontend with Ctrl+C, then retry."
    }
    if (-not $BackendRoot) {
        $BackendRoot = $repoRoot
        # A nested checkout can reuse its existing configured system without copying secrets or data.
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.env'))) {
            $ancestor = (Get-Item -LiteralPath $repoRoot).Parent
            while ($ancestor) {
                if ((Test-Path -LiteralPath (Join-Path $ancestor.FullName 'backend\main.py')) -and
                    (Test-Path -LiteralPath (Join-Path $ancestor.FullName '.env'))) {
                    $BackendRoot = $ancestor.FullName
                    break
                }
                $ancestor = $ancestor.Parent
            }
        }
    }
    $BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
    if (-not (Test-Path -LiteralPath (Join-Path $BackendRoot 'backend\main.py'))) {
        throw 'BackendRoot must contain backend\main.py.'
    }
    Write-Host "Backend source/config/data: $BackendRoot"
    Write-Host "Frontend source: $frontendRoot"
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $vite = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) {
        $vite = Join-Path $repoRoot 'node_modules\vite\bin\vite.js'
    }
    if (-not (Test-Path -LiteralPath $vite)) {
        Push-Location -LiteralPath $frontendRoot
        try {
            & $npm ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        } finally { Pop-Location }
        $vite = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
    }
    $logRoot = Join-Path $repoRoot ('runtime\dev-start\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    Write-Host "Logs: $logRoot"
    $health = Read-Health $backendUrl
    if ($health) {
        Write-Host "Reusing running backend: $backendUrl (it will remain running on exit)."
    } else {
        if (Test-LocalPort $BackendPort) { throw "Port $BackendPort is occupied by an unrecognized service." }
        if (-not $Python) { $Python = $env:SHIJI_PYTHON }
        foreach ($candidate in @('.venv\Scripts\python.exe', 'elder-safety\.venv\Scripts\python.exe')) {
            if (-not $Python -and (Test-Path -LiteralPath (Join-Path $BackendRoot $candidate))) {
                $Python = Join-Path $BackendRoot $candidate
            }
        }
        if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
            throw 'Python environment missing. Run setup.bat, or supply -Python with an existing environment.'
        }
        $Python = (Resolve-Path -LiteralPath $Python).Path
        & $Python -c 'import fastapi, uvicorn, cv2, httpx, jsonschema, dotenv'
        if ($LASTEXITCODE -ne 0) { throw 'Missing backend dependencies. See setup.bat and requirements.txt.' }
        $backendProcess = Start-Process -FilePath $Python -WorkingDirectory $BackendRoot -WindowStyle Hidden -PassThru `
            -ArgumentList @('-m', 'uvicorn', 'backend.main:create_app', '--factory', '--host', '127.0.0.1', '--port', "$BackendPort") `
            -RedirectStandardOutput (Join-Path $logRoot 'backend.out.log') -RedirectStandardError (Join-Path $logRoot 'backend.err.log')
        $health = Wait-Health $backendUrl $backendProcess
    }
    if ($health.cloud_mode) { throw 'Camera development requires the local backend, not CLOUD_MODE.' }
    $env:ANJU_BACKEND_PORT = "$BackendPort"
    $frontendProcess = Start-Process -FilePath $node -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @(('"' + $vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -RedirectStandardOutput (Join-Path $logRoot 'frontend.out.log') -RedirectStandardError (Join-Path $logRoot 'frontend.err.log')
    $null = Wait-Health $frontendUrl $frontendProcess
    $page = Invoke-WebRequest "$frontendUrl/" -UseBasicParsing -TimeoutSec 5
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch '/src/main.tsx') { throw 'Frontend page check failed.' }
    # Read-only camera check exercises Origin/Host without starting hardware or model calls.
    $null = Invoke-RestMethod "$frontendUrl/api/camera/status" -Headers @{ Origin = $frontendUrl } -TimeoutSec 5
    $rejected = $false
    try {
        $null = Invoke-WebRequest "$frontendUrl/api/camera/status" -Headers @{ Origin = 'https://untrusted.example' } -UseBasicParsing -TimeoutSec 5
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 403) { $rejected = $true }
        else { throw }
    }
    if (-not $rejected) { throw 'Camera proxy failed to reject an untrusted origin.' }
    Write-Host "Ready: $frontendUrl (backend: $backendUrl)"
    Write-Host 'Keep this window open. Ctrl+C stops only services started by this script.'
    if ($SmokeTest) { Write-Host 'Smoke test passed: page, API proxy, same-origin camera status and untrusted-origin rejection.' }
    else {
        if (-not $NoBrowser) { Start-Process $frontendUrl }
        while (-not $frontendProcess.HasExited -and (-not $backendProcess -or -not $backendProcess.HasExited)) {
            Start-Sleep -Seconds 1
        }
        throw 'A service exited. Check the logs.'
    }
} catch {
    if ($logRoot -and (Test-Path -LiteralPath $logRoot)) {
        Get-ChildItem -LiteralPath $logRoot -Filter '*.log' | ForEach-Object {
            Write-Host "--- $($_.Name) ---"
            Get-Content -LiteralPath $_.FullName -Tail 15
        }
    }
    throw
} finally {
    if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id -ErrorAction SilentlyContinue }
    if ($backendProcess -and -not $backendProcess.HasExited) {
        # Release the camera worker before stopping a backend owned by this invocation.
        try { $null = Invoke-RestMethod "$backendUrl/api/camera/stop" -Method Post -TimeoutSec 5 } catch { }
        Stop-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue
    }
    $env:ANJU_BACKEND_PORT = $oldBackendPort
}
