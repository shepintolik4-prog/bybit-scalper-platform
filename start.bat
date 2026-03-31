@echo off
setlocal
cd /d "%~dp0"

echo [start] backend uvicorn :8000
start "scalper-backend" cmd /k "cd /d %~dp0backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

timeout /t 2 /nobreak >nul

echo [start] frontend vite :5173
start "scalper-frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Открыты два окна: backend и frontend. Метрики: GET http://localhost:8000/metrics
echo Документация мониторинга: docs\AUTONOMOUS_FUND.md
pause
