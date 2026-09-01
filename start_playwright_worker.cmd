@echo off
REM Автозапуск Playwright-скрейпера бирж на Windows (скрытое окно)
cd /d E:\CodeProjects\arch-code
start "" /min powershell -WindowStyle Hidden -ExecutionPolicy Bypass -Command "& 'E:\CodeProjects\arch-code\venv\Scripts\python.exe' 'E:\CodeProjects\arch-code\playwright_worker.py' >> 'E:\CodeProjects\arch-code\logs\playwright_worker.log' 2>&1"
