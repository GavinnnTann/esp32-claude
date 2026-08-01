# Registers a Windows Scheduled Task that runs the esp32-claude host script at
# user logon (hidden, no console window kept open). Per-user task — does not
# need to be run elevated.
#
# Usage:   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove:  Unregister-ScheduledTask -TaskName esp32-claude-host -Confirm:$false
# Run now: Start-ScheduledTask -TaskName esp32-claude-host
# Logs:    Get-ScheduledTaskInfo -TaskName esp32-claude-host

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "esp32-claude.py"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "python not found on PATH. Install it (or activate the venv you installed requirements.txt into) before running this script."
}

$action = New-ScheduledTaskAction -Execute $pythonCmd.Source -Argument "`"$scriptPath`"" -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName "esp32-claude-host" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered scheduled task 'esp32-claude-host' (runs '$($pythonCmd.Source) $scriptPath' at logon)."
Write-Host "Start it now with:   Start-ScheduledTask -TaskName esp32-claude-host"
Write-Host "Remove it with:      Unregister-ScheduledTask -TaskName esp32-claude-host -Confirm:`$false"
