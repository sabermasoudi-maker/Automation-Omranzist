@echo off
chcp 65001 >nul
REM این فایل را با «Run as administrator» اجرا کنید: سامانه را متوقف و با فایل‌های جدید دوباره اجرا می‌کند
cd /d "%~dp0"
schtasks /end /tn "OmranZistOA" >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%~dp0app.py*' -and $_.Name -like 'python*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
schtasks /query /tn "OmranZistOA" >nul 2>nul
if %errorlevel%==0 (
  schtasks /run /tn "OmranZistOA"
  echo Restarted in background. Open the system in the browser and press Ctrl+F5.
) else (
  echo Autostart task not installed; starting in this window...
  where python >nul 2>nul
  if %errorlevel%==0 (python app.py) else (py app.py)
)
pause
