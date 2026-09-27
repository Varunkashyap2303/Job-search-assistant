# Registers the Windows scheduled tasks. Runs as the current user, only while logged on.
#   JobSearch-Intel : 06:00 daily, finds and profiles newly funded AU AI startups
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

$IntelAction = New-ScheduledTaskAction -Execute $Python -Argument "-m jobsearch intel" -WorkingDirectory $Root
$IntelTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am
Register-ScheduledTask -TaskName "JobSearch-Intel" -Action $IntelAction -Trigger $IntelTrigger -Settings $Settings `
    -Description "Job search swarm: daily funded-startup research" -Force | Out-Null

$ScoutAction = New-ScheduledTaskAction -Execute $Python -Argument "-m jobsearch scout" -WorkingDirectory $Root
$ScoutTrigger = New-ScheduledTaskTrigger -Daily -At 7:00am
$ScoutTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 7:00am `
    -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 16)).Repetition
Register-ScheduledTask -TaskName "JobSearch-Scout" -Action $ScoutAction -Trigger $ScoutTrigger -Settings $Settings `
    -Description "Job search swarm: fetch, filter, score jobs and tailor resumes" -Force | Out-Null

Write-Output "Registered JobSearch-Intel (06:00 daily) and JobSearch-Scout (every 2h, 07:00-23:00). Logs: data\logs\jobsearch.log"
