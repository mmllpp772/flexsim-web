@echo off
REM OneDrive 中轉模式：監看共用資料夾，有新工作就跑 FlexSim（不需開 port）
cd /d "%~dp0"
python onedrive_worker.py
pause
