@echo off
REM Автозапуск arch-code RQ-воркера на Windows (скрытое окно)
REM Воркер подключается к Redis VPS через SSH-туннель (start_redis_tunnel.cmd)
cd /d E:\CodeProjects\arch-code
start "" /min powershell -WindowStyle Hidden -ExecutionPolicy Bypass -Command "& 'E:\CodeProjects\arch-code\venv\Scripts\python.exe' 'E:\CodeProjects\arch-code\rq_worker.py' >> 'E:\CodeProjects\arch-code\logs\worker.log' 2>&1"
