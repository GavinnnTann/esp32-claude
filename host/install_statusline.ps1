# Installs statusline_usage.py as the Claude Code status line, so the display
# gets quota numbers as fresh as /usage instead of whatever ~/.claude.json
# happens to be caching. See host/statusline_usage.py for why.
#
#   powershell -ExecutionPolicy Bypass -File host\install_statusline.ps1
#   powershell -ExecutionPolicy Bypass -File host\install_statusline.ps1 -Uninstall
#
# Pure ASCII on purpose. PowerShell 5.1 reads a BOM-less file as ANSI, so a
# stray em-dash in a comment is a parse error, not a typo - install_autostart.ps1
# learned that the hard way.

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"

$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
$scriptPath   = Join-Path $PSScriptRoot "statusline_usage.py"

if (-not (Test-Path $settingsPath)) {
    Write-Error "Not found: $settingsPath. Is Claude Code installed?"
    exit 1
}

# Read as raw text and convert, so an unexpected shape fails loudly here rather
# than silently dropping the user's other settings on write-back.
$raw = Get-Content $settingsPath -Raw -Encoding UTF8
try { $settings = $raw | ConvertFrom-Json } catch { Write-Error "settings.json is not valid JSON: $_"; exit 1 }

# Keep a copy the first time we touch it. Cheap insurance on a file that holds
# every other Claude Code preference.
$backup = "$settingsPath.esp32-claude.bak"
if (-not (Test-Path $backup)) { Copy-Item $settingsPath $backup }

if ($Uninstall) {
    if ($settings.PSObject.Properties.Name -contains "statusLine") {
        $settings.PSObject.Properties.Remove("statusLine")
        $settings | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding utf8
        Write-Host "Removed statusLine from $settingsPath"
        Write-Host "Backup of the original is at $backup"
    } else {
        Write-Host "No statusLine entry to remove."
    }
    exit 0
}

if (-not (Test-Path $scriptPath)) { Write-Error "Not found: $scriptPath"; exit 1 }

if ($settings.PSObject.Properties.Name -contains "statusLine") {
    $existing = $settings.statusLine
    if ($existing.command -notlike "*statusline_usage.py*") {
        Write-Warning "settings.json already has a different statusLine:"
        Write-Warning "  $($existing.command)"
        Write-Warning "Refusing to overwrite it. Remove it first, or merge by hand."
        exit 1
    }
}

# pythonw has no console and would swallow stdout, which IS the status line.
# Resolve a real python.exe rather than trusting PATH order.
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) { Write-Error "python.exe not found on PATH."; exit 1 }

$statusLine = [ordered]@{
    type    = "command"
    command = '"{0}" "{1}"' -f $python, $scriptPath
    # 5s. The minimum Claude Code allows is 1, but the display's own BLE push
    # loop only checks every 15s, so anything faster is spent for nothing.
    refreshInterval = 5
}

$settings | Add-Member -NotePropertyName statusLine -NotePropertyValue $statusLine -Force
$settings | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding utf8

Write-Host "Installed status line -> $scriptPath"
Write-Host "  settings : $settingsPath"
Write-Host "  backup   : $backup"
Write-Host "  feeds    : $env:LOCALAPPDATA\esp32-claude\rate_limits.json"
Write-Host ""
Write-Host "Takes effect in NEW Claude Code sessions. The rate_limits fields"
Write-Host "appear only for Pro/Max, and only after the session's first response."
