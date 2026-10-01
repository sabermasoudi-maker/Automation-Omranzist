@echo off
chcp 65001 >nul
REM این فایل را با «Run as administrator» اجرا کنید
cd /d "%~dp0"
set PY=
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY ( echo Python not found. Install Python "for all users" and tick "Add to PATH". & pause & exit /b )
schtasks /create /f /tn "OmranZistOA" /sc onstart /ru SYSTEM /rl HIGHEST /tr "\"%PY%\" \"%~dp0app.py\""
set PORT=8080
if exist "%~dp0port.txt" set /p PORT=<"%~dp0port.txt"
netsh advfirewall firewall delete rule name="OmranZist OA" >nul 2>nul
netsh advfirewall firewall add rule name="OmranZist OA" dir=in action=allow protocol=TCP localport=%PORT%
REM پورت HTTPS برای اعلان روی موبایل (اگر در مدیریت سامانه عوض شد، همین‌جا هم عوض کنید)
netsh advfirewall firewall delete rule name="OmranZist OA HTTPS" >nul 2>nul
netsh advfirewall firewall add rule name="OmranZist OA HTTPS" dir=in action=allow protocol=TCP localport=8443
schtasks /run /tn "OmranZistOA"
echo.
echo Done. The system now starts automatically with Windows. Address: http://SERVER-IP:%PORT%
pause
