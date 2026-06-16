# schedule.ps1
# PowerShell script to register news-bot in Windows Task Scheduler

# Ensure we are in the script's directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

# 1. Locate pythonw.exe (runs Python scripts silently without popping up a console window)
$pythonwPath = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonwPath) {
    # Fallback to the detected path in GAMEPLAN
    $fallbackPath = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\pythonw.exe"
    if (Test-Path $fallbackPath) {
        $pythonwPath = $fallbackPath
    } else {
        # Fallback to standard pythonw search
        $pythonwPath = "pythonw.exe"
    }
}

$scriptFile = Join-Path $scriptDir "generate.py"
$arguments = "`"$scriptFile`" --silent"

Write-Host "Registering background news-bot task..."
Write-Host "Python executable: $pythonwPath"
Write-Host "Working directory: $scriptDir"
Write-Host "Arguments: $arguments"

# 2. Check and unregister existing task if it exists
$taskName = "NewsBotDigestUpdate"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Found existing task '$taskName'. Removing it first..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# 3. Define the scheduled task parameters
# Run once starting now, and repeat every 4 hours indefinitely
$trigger = New-ScheduledTaskTrigger -At (Get-Date) -Once -RepetitionInterval (New-TimeSpan -Hours 4)

# Create task action
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $arguments -WorkingDirectory $scriptDir

# Create task settings (crucial for laptops: run even on battery, wake if needed, start when available)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# 4. Register the task
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Silently compiles the news-bot HTML digest in the background every 4 hours." -ErrorAction Stop
    Write-Host -ForegroundColor Green "Success: Task '$taskName' has been registered. It will refresh your news digest silently in the background every 4 hours."
    Write-Host "You can view/manage it in the Windows 'Task Scheduler' utility under Task Scheduler Library."
} catch {
    Write-Warning "Failed to register scheduled task: $_"
    Write-Warning "Ensure you are running PowerShell with appropriate permissions if this error persists."
}
