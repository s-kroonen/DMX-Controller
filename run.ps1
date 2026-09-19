<#
.SYNOPSIS
  Start the DMX Controller (backend + web UI) on Windows.

.EXAMPLE
  .\run.ps1                 # real dongle on COM7, UI at http://localhost:8000
  .\run.ps1 -Port COM5      # different COM port
  .\run.ps1 -Sim            # no hardware: simulated output
  .\run.ps1 -Lan            # reachable from a phone/tablet on the same network
#>
param(
    [string]$Port = "COM7",
    [switch]$Sim,
    [switch]$Lan,
    [int]$HttpPort = 8000
)

$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
Set-Location $backend

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "First run: creating virtualenv and installing requirements..."
    python -m venv .venv
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
}

if ($Sim) {
    Remove-Item Env:DMX4ALL_PORT -ErrorAction SilentlyContinue
    Write-Host "Simulator mode (no hardware)."
} else {
    $env:DMX4ALL_PORT = $Port
    Write-Host "DMX4ALL on $Port (a bad/busy port falls back to the simulator; see DMX Setup in the UI)."
}

$bind = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
Write-Host "UI: http://localhost:$HttpPort   (Ctrl+C to stop)"
if ($Lan) {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback|Bluetooth' } |
        Select-Object -ExpandProperty IPAddress
    foreach ($ip in $addresses) { Write-Host "From another device on the network: http://${ip}:$HttpPort" }
    Write-Host "There is no login: anyone on this network can control the lights. Use a trusted network."
    Write-Host "If another device can't connect, allow the port through the Windows firewall (admin PowerShell, once):"
    Write-Host "  New-NetFirewallRule -DisplayName 'DMX Controller' -Direction Inbound -Protocol TCP -LocalPort $HttpPort -Action Allow -Profile Private"
}
& .venv\Scripts\python.exe -m uvicorn app.main:app --host $bind --port $HttpPort
