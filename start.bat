@echo off
REM 啟動模擬程式：監看 OneDrive 共用資料夾，有新工作就跑 FlexSim
cd /d "%~dp0"
python onedrive_worker.py
pause
