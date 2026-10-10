# keepalive.ps1
# GitHub silently disables scheduled workflows (state "disabled_inactivity") after
# 60 days with no repository activity. A workflow cannot revive itself once disabled,
# so this script runs from the local machine: it finds any workflows GitHub disabled
# for inactivity and re-enables them. Workflows you disabled on purpose
# ("disabled_manually") are never touched.
#
# Usage:
#   .\keepalive.ps1            # check once and re-enable anything disabled for inactivity
#   .\keepalive.ps1 -Install   # register a weekly (Friday) Task Scheduler job that runs this check
#
# Requires the GitHub CLI (gh), already authenticated for this repository.

param(
    [switch]$Install,
    [string]$Repo = "bot-bbio/news-bot"
)

$ErrorActionPreference = "Stop"

function Get-GhPath {
    $cmd = Get-Command gh.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # Task Scheduler may run with a thinner PATH than an interactive shell.
    $fallback = "C:\Program Files\GitHub CLI\gh.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "GitHub CLI (gh) not found."
}

# Pure filter, kept separate so it can be tested without touching GitHub.
function Select-InactivityDisabled {
    param([Parameter(Mandatory)][object[]]$Workflows)
    $Workflows | Where-Object { $_.state -eq "disabled_inactivity" }
}

function Install-KeepaliveTask {
    $taskName = "NewsBotWorkflowKeepalive"
    $scriptFile = $MyInvocation.PSCommandPath
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptFile`" -Repo $Repo"

    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "9:00AM"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory (Split-Path -Parent $scriptFile)
    # StartWhenAvailable: if the PC was off at 9am, run as soon as it is next on.
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Re-enables news-bot GitHub workflows that GitHub disabled for inactivity." | Out-Null
    Write-Host "Registered weekly task '$taskName' (Fridays 9:00 AM, catches up if missed)."
}

if ($Install) {
    Install-KeepaliveTask
    return
}

$gh = Get-GhPath
$json = & $gh workflow list --all --repo $Repo --json id,name,state
if ($LASTEXITCODE -ne 0) { throw "gh workflow list failed (exit $LASTEXITCODE)." }

$stale = @(Select-InactivityDisabled -Workflows @($json | ConvertFrom-Json))
if ($stale.Count -eq 0) {
    Write-Host "$(Get-Date -Format s) All workflows healthy; nothing to revive."
    return
}

foreach ($wf in $stale) {
    Write-Host "$(Get-Date -Format s) Re-enabling '$($wf.name)' (id $($wf.id))..."
    & $gh workflow enable $wf.id --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw "gh workflow enable failed for '$($wf.name)'." }
}
