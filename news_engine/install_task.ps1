# Registers the news engine's daily job: python -m news_engine.daily
#
#   powershell -ExecutionPolicy Bypass -File news_engine\install_task.ps1           # install / update
#   powershell -ExecutionPolicy Bypass -File news_engine\install_task.ps1 -Remove   # uninstall
#
# Runs as you, daily and 5 minutes after each login. If the PC was off, it runs as soon as possible after.

param([switch]$Remove, [string]$Time = "09:00")

$TaskName = "ftmo-news-engine"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'."
    exit 0
}
if (-not (Test-Path $Python)) { throw "Python venv not found at $Python" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m news_engine.daily" -WorkingDirectory $Repo
$daily = New-ScheduledTaskTrigger -Daily -At $Time
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$login = New-ScheduledTaskTrigger -AtLogOn -User $user
$login.Delay = "PT5M"  # gives MT5 time to log in
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($daily, $login) -Settings $settings `
    -Description "News engine: save event estimates and score them (no orders)" -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Installed '$TaskName': next run $($info.NextRunTime)"
