@echo off
REM 啟動模擬伺服器（同公司網路的人可透過 http://本機名稱:8000 使用）
cd /d "%~dp0"
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
