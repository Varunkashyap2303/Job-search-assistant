# Registers the Windows scheduled tasks. Runs as the current user, only while logged on.
#   JobSearch-Intel : 06:00 daily (wakes the PC from sleep), funded AU startups likely to hire AI engineers,
#                     profiles, outreach drafts and the morning brief in data\reports
#   JobSearch-Scout : every 2 hours 07:00-23:00, fetches/scores jobs and tailors resumes
# Remove both with:  .\scripts\schedule_scout.ps1 -Remove
param([switch]$Remove)

$Tasks = @("JobSearch-Intel", "JobSearch-Scout")

if ($Remove) {
    foreach ($t in $Tasks) { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Output "Removed $($Tasks -join ', ')"
    return
}

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\pythonw.exe"   # pythonw: no console window pops up
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

# The morning run wakes the PC (needs wake timers allowed in Power Options) and, if the PC was off at 06:00,
# runs at the next logon. It does more work than a scout run, so it gets a longer time limit.
$IntelSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 60) -MultipleInstances IgnoreNew
$IntelAction = New-ScheduledTaskAction -Execute $Python -Argument "-m jobsearch intel" -WorkingDirectory $Root
$IntelTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am
Register-ScheduledTask -TaskName "JobSearch-Intel" -Action $IntelAction -Trigger $IntelTrigger -Settings $IntelSettings `
    -Description "Job search swarm: morning funded-startup brief with outreach drafts" -Force | Out-Null

$ScoutAction = New-ScheduledTaskAction -Execute $Python -Argument "-m jobsearch scout" -WorkingDirectory $Root
$ScoutTrigger = New-ScheduledTaskTrigger -Daily -At 7:00am
$ScoutTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 7:00am `
    -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 16)).Repetition
Register-ScheduledTask -TaskName "JobSearch-Scout" -Action $ScoutAction -Trigger $ScoutTrigger -Settings $Settings `
    -Description "Job search swarm: fetch, filter, score jobs and tailor resumes" -Force | Out-Null

Write-Output "Registered JobSearch-Intel (06:00 daily, wakes the PC) and JobSearch-Scout (every 2h, 07:00-23:00). Logs: data\logs\jobsearch.log"
