# ────────────────────────────────────────────────────────────────
#  start_redis_tunnel.ps1 — SSH-туннель к Redis на VPS (efrolov-dev)
#  Redis на VPS слушает ТОЛЬКО 127.0.0.1 (без пароля).
#  Воркер arch-code на ПК подключается к redis://127.0.0.1:6379/0.
#
#  Автозапуск:  schtasks /Create /TN "RedisTunnel" /TR "powershell -WindowStyle Hidden -File E:\CodeProjects\arch-code\start_redis_tunnel.ps1" /SC ONLOGON /RL HIGHEST /F
#  Удаление:    schtasks /Delete /TN "RedisTunnel" /F
# ────────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"
$Port = 6379
$HostAlias = "efrolov-dev"   # алиас из ~/.ssh/config

# Если порт уже слушается — выходим (туннель уже работает)
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Port $Port already in use (tunnel likely running) — exit"
    exit 0
}

# Проверяем SSH-доступность
if (-not (Test-NetConnection -ComputerName $HostAlias -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Write-Host "SSH host $HostAlias unreachable — retry in 30s"
    Start-Sleep -Seconds 30
}

# Запускаем туннель (бесконечный, -N не выполняет команд)
while ($true) {
    Write-Host "Starting SSH tunnel: $HostAlias → localhost:$Port"
    ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -N -L "${Port}:127.0.0.1:${Port}" $HostAlias
    Write-Host "Tunnel died — restarting in 10s..."
    Start-Sleep -Seconds 10
}
