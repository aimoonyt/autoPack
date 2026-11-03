# One-click launch for Windows: tunnel + bot, WEBAPP_URL is updated automatically.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "No .env file next to start.bat. Create it (BOT_TOKEN=...) and run again." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found. Install: winget install --id Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "First run: installing dependencies..." -ForegroundColor Yellow
    python -m venv .venv
    & .venv\Scripts\pip install -r requirements.txt
}

# Log goes next to the bot: TEMP breaks on non-latin user names (C:\Users\A5A3~1)
$dataDir = Join-Path $PSScriptRoot "data"
if (-not (Test-Path -LiteralPath $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }

# If the window was closed with the X, the hidden tunnel from the last run is still alive.
# Kill it, otherwise it keeps the old URL and locks the log file.
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*localhost:8080*" } |
    ForEach-Object {
        Write-Host "Stopping old tunnel (pid $($_.ProcessId))..." -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Milliseconds 500

# Old logs: delete what we can, skip what is still locked
Get-ChildItem -LiteralPath $dataDir -Filter "cloudflared*.log" -ErrorAction SilentlyContinue | ForEach-Object {
    try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop } catch { }
}
$log = Join-Path $dataDir ("cloudflared-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
Write-Host "Starting tunnel..." -ForegroundColor Yellow
$cf = Start-Process cloudflared -ArgumentList "tunnel", "--url", "http://localhost:8080" `
    -RedirectStandardError $log -WindowStyle Hidden -PassThru

try {
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path -LiteralPath $log) {
            $m = Select-String -LiteralPath $log -Pattern "https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value + "/" }
        }
    }
    if (-not $url) {
        Write-Host "Tunnel did not start in 60s. Log: $log" -ForegroundColor Red
        exit 1
    }
    Write-Host "Tunnel: $url" -ForegroundColor Green

    # The URL is printed before Cloudflare can route it (error 1033 if opened too early).
    # Wait until cloudflared reports a registered connection.
    Write-Host "Waiting for Cloudflare to register the tunnel..." -ForegroundColor Yellow
    $ready = $false
    for ($i = 0; $i -lt 45 -and -not $ready; $i++) {
        if (Select-String -LiteralPath $log -Pattern "Registered tunnel connection" -Quiet) { $ready = $true }
        else { Start-Sleep -Seconds 1 }
    }
    if ($ready) { Start-Sleep -Seconds 3; Write-Host "Tunnel is up." -ForegroundColor Green }
    else { Write-Host "Cloudflare is slow today; the mini app may need a minute before it opens." -ForegroundColor Yellow }

    $utf8 = New-Object System.Text.UTF8Encoding $false
    $lines = [System.IO.File]::ReadAllLines((Resolve-Path ".env"), $utf8) | Where-Object { $_ -notmatch "^\s*WEBAPP_URL\s*=" }
    $lines = @($lines) + "WEBAPP_URL=$url"
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $lines, $utf8)

    Write-Host "Starting bot. Close this window or press Ctrl+C to stop." -ForegroundColor Green
    & .venv\Scripts\python -m app.main
}
finally {
    if ($cf -and -not $cf.HasExited) { Stop-Process -Id $cf.Id -Force }
}
