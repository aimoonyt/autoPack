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

$log = Join-Path $env:TEMP "autopack-cloudflared.log"
Remove-Item $log -ErrorAction SilentlyContinue
Write-Host "Starting tunnel..." -ForegroundColor Yellow
$cf = Start-Process cloudflared -ArgumentList "tunnel", "--url", "http://localhost:8080" `
    -RedirectStandardError $log -WindowStyle Hidden -PassThru

try {
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $log) {
            $m = Select-String -Path $log -Pattern "https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value + "/" }
        }
    }
    if (-not $url) {
        Write-Host "Tunnel did not start in 60s. Log: $log" -ForegroundColor Red
        exit 1
    }
    Write-Host "Tunnel: $url" -ForegroundColor Green

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
