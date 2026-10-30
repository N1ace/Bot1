# Registers a Windows scheduled task that starts the news & event monitor when you log in:
#     python -m monitor
# It runs while the PC is on, with no window, and restarts if it crashes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_monitor_task.ps1           # install / update + start
#   powershell -ExecutionPolicy Bypass -File scripts\install_monitor_task.ps1 -Remove   # stop and uninstall
#
# - Runs as you, only while you are logged in (no password stored).
# - While the PC is off or asleep it watches nothing. A VPS would fix that later.

param([switch]$Remove)

$TaskName = "cryptobot-monitor"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\pythonw.exe"

if ($Remove) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'."
    exit 0
}
if (-not (Test-Path $Python)) { throw "Python venv not found at $Python" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m monitor" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "cryptobot news & event monitor: alerts and briefings (no orders)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started '$TaskName': state $($task.State)"
