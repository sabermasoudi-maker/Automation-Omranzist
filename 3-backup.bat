@echo off
chcp 65001 >nul
REM مسیر مقصد را به هارد اکسترنال یا کامپیوتر دیگر تغییر دهید
set DEST=E:\OA-Backup
cd /d "%~dp0"
robocopy "%~dp0data" "%DEST%\data" /E /XO /R:1 /W:1 /XF oa.db oa.db-wal oa.db-shm
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%d
copy /Y "%~dp0data\backups\oa-%D%.db" "%DEST%\oa-%D%.db" >nul
echo Backup finished: %DEST%
pause
