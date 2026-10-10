@echo off
chcp 65001 >nul
REM این فایل را با «Run as administrator» اجرا کنید (پس از هر ارتقا هم یک بار اجرا کنید)
cd /d "%~dp0"
set PY=
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY ( echo Python not found. Install Python "for all users" and tick "Add to PATH". & pause & exit /b )
REM نسخه ۴.۷.۱: زمان‌بند با تنظیمات درست (بدون توقف پس از ۳ روز یا روی باتری، اجرای دوباره خودکار)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Py "%PY%" -App "%~dp0app.py"
if errorlevel 1 (
  echo PowerShell setup failed; using basic schtasks instead.
  schtasks /create /f /tn "OmranZistOA" /sc onstart /ru SYSTEM /rl HIGHEST /tr "\"%PY%\" \"%~dp0app.py\""
)
set PORT=8080
if exist "%~dp0port.txt" set /p PORT=<"%~dp0port.txt"
set HPORT=8443
if exist "%~dp0https_port.txt" set /p HPORT=<"%~dp0https_port.txt"
netsh advfirewall firewall delete rule name="OmranZist OA" >nul 2>nul
netsh advfirewall firewall add rule name="OmranZist OA" dir=in action=allow protocol=TCP localport=%PORT%
REM پورت HTTPS برای اعلان روی موبایل (از https_port.txt که خود سامانه می‌نویسد)
netsh advfirewall firewall delete rule name="OmranZist OA HTTPS" >nul 2>nul
netsh advfirewall firewall add rule name="OmranZist OA HTTPS" dir=in action=allow protocol=TCP localport=%HPORT%
REM سرور نباید به خواب برود (وقتی به برق وصل است)؛ خواب = قطع سامانه برای همه
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
schtasks /run /tn "OmranZistOA"
echo.
echo Done. The system starts automatically with Windows and restarts itself if it stops.
echo Address: http://SERVER-IP:%PORT%   HTTPS: https://SERVER-NAME:%HPORT%
echo Server log: %~dp0data\logs\server.log
pause
