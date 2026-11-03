$ErrorActionPreference = 'Stop'
$dashboardRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $dashboardRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python -ErrorAction Stop).Source }
$url = 'http://127.0.0.1:8765'
$running = $false
try { $status = Invoke-RestMethod "$url/api/status" -TimeoutSec 2; $running = $null -ne $status.config } catch {}
if (-not $running) {
    $serverPath = Join-Path $dashboardRoot 'app.py'
    $arguments = '"{0}" --port 8765' -f $serverPath
    $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $dashboardRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dashboardRoot 'server.log') -RedirectStandardError (Join-Path $dashboardRoot 'server-error.log')
    $process.Id | Set-Content -LiteralPath (Join-Path $dashboardRoot 'dashboard.pid')
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        Start-Sleep -Milliseconds 300
        try { $null = Invoke-RestMethod "$url/api/status" -TimeoutSec 1; $running = $true } catch {}
    } while (-not $running -and [DateTime]::UtcNow -lt $deadline)
    if (-not $running) { throw 'Server did not start. Install the host package and read server-error.log.' }
}
Start-Process $url
