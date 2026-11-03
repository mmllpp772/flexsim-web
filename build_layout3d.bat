@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 依 CAD 產生 3D 佈置圖（DWG 和 Factory_Equipment_3D_Library.html 放在這個資料夾）
python tools\build_layout3d.py %*
pause
