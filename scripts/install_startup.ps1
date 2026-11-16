# ============================================================================
# Register or Remove JARVIS Personal AI in Windows 11 User Logon Startup
# Runs with standard user privileges (Least Privilege — never requests Admin).
# ============================================================================
param(
    [switch]$Uninstall = $false
)

$TaskName = "JarvisPersonalAI"
$VbsPath  = Join-Path $PSScriptRoot "run_jarvis_silent.vbs"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Removed '$TaskName' from Windows 11 startup." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $VbsPath)) {
    Write-Host "[ERROR] Missing launcher script: $VbsPath" -ForegroundColor Red
    exit 1
}

$Action   = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
$Trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Launch JARVIS Personal Local AI Operator at logon" -Force | Out-Null
Write-Host "[OK] Registered '$TaskName' to start at Windows 11 user logon (least-privilege mode)." -ForegroundColor Green
Write-Host "     To remove later: powershell -File scripts\install_startup.ps1 -Uninstall" -ForegroundColor DarkCyan
