@echo off
chcp 65001 >nul
setlocal
REM 把 DWG 檔拖到這個檔案上面，就會在同一個資料夾產生同名的 DXF（可以一次拖好幾個）
if "%~1"=="" (
  echo.
  echo   使用方式：把 DWG 檔拖到「DWG轉DXF.bat」上面放開。
  echo   轉好的 DXF 會放在 DWG 旁邊，檔名相同。
  echo.
  pause
  exit /b
)
:next
if "%~1"=="" goto done
set "OUT=%~dpn1.dxf"
set "WORK=%TEMP%\dwg2dxf_%RANDOM%%RANDOM%"
mkdir "%WORK%" >nul 2>&1
echo.
echo 轉檔中：%~nx1 （大檔案可能要幾分鐘）
copy /y "%~1" "%WORK%\in.dwg" >nul
"%~dp0dwg2dxf.exe" -y -o "%WORK%\out.dxf" "%WORK%\in.dwg" 2>"%WORK%\log.txt"
if exist "%WORK%\out.dxf" (
  move /y "%WORK%\out.dxf" "%OUT%" >nul
  echo 完成：%OUT%
) else (
  echo 失敗：%~nx1
  type "%WORK%\log.txt"
  echo 可以先用 DWG TrueView 的「DWG 轉換」存成 AutoCAD 2010 或 2004 格式，再拖一次。
)
rmdir /s /q "%WORK%" >nul 2>&1
shift
goto next
:done
echo.
pause
