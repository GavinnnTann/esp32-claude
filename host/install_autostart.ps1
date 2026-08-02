# Makes the esp32-claude host script start automatically at login.
#
# Uses a Startup-folder shortcut rather than a Scheduled Task. Task Scheduler
# is the tidier mechanism (restart-on-failure, battery policy), but
# Register-ScheduledTask returns "Access is denied" without elevation on a
# standard Windows user account - including with no explicit principal. The
# Startup folder is per-user and needs no admin rights.
#
# Launches via pythonw.exe so no console window appears. Because pythonw has
# no console, stdout is redirected to a log file - and esp32-claude.py forces
# line-buffered stdout, so that log stays current instead of sitting in a
# block buffer.
#
# Usage:   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall
# Log:     %LOCALAPPDATA%\esp32-claude\host.log

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "esp32-claude.py"
$startup    = [Environment]::GetFolderPath('Startup')
$lnkPath    = Join-Path $startup "esp32-claude-host.lnk"

if ($Uninstall) {
    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath -Force
        Write-Host "Removed $lnkPath"
    } else {
        Write-Host "Nothing to remove - $lnkPath does not exist."
    }
    return
}

$pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
if (-not $pythonw) {
    throw "pythonw.exe not found on PATH. Install Python (or activate the venv you installed requirements.txt into) first."
}
if (-not (Test-Path $scriptPath)) {
    throw "Cannot find $scriptPath"
}

$logDir = Join-Path $env:LOCALAPPDATA "esp32-claude"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logPath = Join-Path $logDir "host.log"

# cmd /c wrapper is only there to redirect stdout/stderr to the log; pythonw
# alone has nowhere to write them. Built by concatenation with an explicit
# quote char - nesting escaped quotes inside a PowerShell string here is a
# reliable way to produce an unparseable script.
$q         = [char]34
$target    = $env:ComSpec
$inner     = "$q$($pythonw.Source)$q $q$scriptPath$q >> $q$logPath$q 2>&1"
$arguments = "/c $q$inner$q"

$shell    = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath       = $target
$shortcut.Arguments        = $arguments
$shortcut.WorkingDirectory = $scriptDir
$shortcut.WindowStyle      = 7   # minimised; cmd closes immediately anyway
$shortcut.Description      = "esp32-claude usage display host"
$shortcut.Save()

Write-Host "Installed: $lnkPath"
Write-Host "  runs   : pythonw $scriptPath"
Write-Host "  log    : $logPath"
Write-Host ""
Write-Host "Start it now without logging out by running the shortcut, or reboot."
Write-Host "Remove with:  install_autostart.ps1 -Uninstall"
