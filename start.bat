@echo off
REM 直連模式：同公司網路的人可透過 http://本機名稱:8000 使用（需開防火牆 TCP 8000）
cd /d "%~dp0"
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
