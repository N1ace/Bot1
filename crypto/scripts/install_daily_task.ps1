# Registers a Windows scheduled task that runs the bot's daily job:
#     python -m cryptobot daily
# every day at 03:30 local time (Israel: always after the 00:00 UTC daily close).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1           # install / update
#   powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1 -Remove   # uninstall
#
# - Runs as you, only while you are logged in (no password stored).
# - "Start when available": if the PC was off at 03:30, it runs as soon as possible after.
# - The daily job itself catches up on missed days of data.

param([switch]$Remove, [string]$Time = "03:30")

$TaskName = "cryptobot-daily"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path (Split-Path -Parent $Repo) ".venv\Scripts\python.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'."
    exit 0
}
if (-not (Test-Path $Python)) { throw "Python venv not found at $Python" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m cryptobot daily" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "cryptobot level 0: data + market state + Telegram report (no orders)" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Installed '$TaskName': state $($task.State), next run $($info.NextRunTime)"
