@echo off
REM Автозапуск SSH-туннеля к Redis VPS (скрытое окно)
start "" /min powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "E:\CodeProjects\arch-code\start_redis_tunnel.ps1"
