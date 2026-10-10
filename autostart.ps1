# نصب اجرای خودکار سامانه با زمان‌بند ویندوز (نسخه ۴.۷.۱) — از 2-install-autostart-firewall.bat اجرا می‌شود
# تفاوت با schtasks قبلی:
#  - بدون سقف زمان اجرا (قبلاً پیش‌فرض ویندوز: توقف پس از ۳ روز)
#  - با رفتن برق روی باتری یا UPS متوقف نمی‌شود (قبلاً پیش‌فرض: توقف)
#  - اگر برنامه به هر دلیل بسته شد، حداکثر پس از ۵ دقیقه خودکار دوباره اجرا می‌شود
param([Parameter(Mandatory = $true)][string]$Py, [Parameter(Mandatory = $true)][string]$App)
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $App
$action = New-ScheduledTaskAction -Execute $Py -Argument ('"' + $App + '"') -WorkingDirectory $dir
$boot = New-ScheduledTaskTrigger -AtStartup
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew
$pr = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
try {
    # نگهبان بیرونی: هر ۵ دقیقه؛ اگر برنامه در حال اجراست کاری نمی‌کند (IgnoreNew)، وگرنه دوباره اجرایش می‌کند
    $keep = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName 'OmranZistOA' -Action $action -Trigger @($boot, $keep) -Settings $set `
        -Principal $pr -Force | Out-Null
    Write-Host 'Scheduled task OmranZistOA installed (start with Windows + keep-alive every 5 minutes).'
} catch {
    Register-ScheduledTask -TaskName 'OmranZistOA' -Action $action -Trigger $boot -Settings $set -Principal $pr -Force | Out-Null
    Write-Host 'Scheduled task OmranZistOA installed (start with Windows). Keep-alive trigger not supported here:' $_.Exception.Message
}
