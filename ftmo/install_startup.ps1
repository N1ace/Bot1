# Starts MT5 and the bot each time you log in to Windows.
#
#   powershell -ExecutionPolicy Bypass -File ftmo\install_startup.ps1                      # bot in dry run, no orders
#   powershell -ExecutionPolicy Bypass -File ftmo\install_startup.ps1 -DemoLogin 123456    # bot places demo orders
#   powershell -ExecutionPolicy Bypass -File ftmo\install_startup.ps1 -Remove              # uninstall
#
# Only pass -DemoLogin once the dry run is clean, and never with a paid account's login.
# The bot opens in its own window. Closing it with the X does not close a trade, so use Ctrl+C in that window.

param([switch]$Remove, [long]$DemoLogin = 0)

$MT5Task = "ftmo-mt5"
$BotTask = "ftmo-bot"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $BotTask -Confirm:$false
    Unregister-ScheduledTask -TaskName $MT5Task -Confirm:$false
    Write-Output "Removed '$MT5Task' and '$BotTask'."
    exit 0
}
if (-not (Test-Path $Python)) { throw "Python venv not found at $Python" }

$Terminal = Join-Path ${env:ProgramFiles} "MetaTrader 5\terminal64.exe"
$Found = (Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1).Path
if ($Found) { $Terminal = $Found }
if (-not (Test-Path $Terminal)) { throw "MT5 not found. Open MT5 and run this again." }

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$mt5Trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$mt5Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName $MT5Task -Action (New-ScheduledTaskAction -Execute $Terminal) -Trigger $mt5Trigger `
    -Settings $mt5Settings -Description "Starts MetaTrader 5 at login" -Force | Out-Null

$arguments = "-m ftmo.bot"
if ($DemoLogin -gt 0) { $arguments += " --place-demo-orders --login $DemoLogin" }
$botAction = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $Repo
$botTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$botTrigger.Delay = "PT2M"  # gives MT5 time to log in
$botSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $BotTask -Action $botAction -Trigger $botTrigger -Settings $botSettings `
    -Description "FTMO demo bot" -Force | Out-Null

$mode = if ($DemoLogin -gt 0) { "DEMO ORDERS on login $DemoLogin" } else { "dry run, no orders" }
Write-Output "Installed '$MT5Task' and '$BotTask'. Bot mode: $mode."
