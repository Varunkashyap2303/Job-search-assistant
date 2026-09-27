"""Schedules the daily runs on any OS: startup intel at 06:00, the job scout every 2 hours 07:00-23:00.

- Windows: Task Scheduler (scripts/schedule_scout.ps1)
- macOS:   two launchd agents in ~/Library/LaunchAgents
- Linux:   two lines in your crontab (marked so they can be found and removed)

Nothing is installed until you run `python -m jobsearch schedule install`; `show` prints what it would do."""

import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from jobsearch.config import LOG_DIR, ROOT

CRON_MARK = "# jobsearch-swarm"
LAUNCHD_LABELS = {"intel": "com.jobsearch-swarm.intel", "scout": "com.jobsearch-swarm.scout"}
SCOUT_HOURS = list(range(7, 24, 2))   # 07:00, 09:00, ... 23:00


def system() -> str:
    return {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")


def _python() -> str:
    return sys.executable


# ---------- Linux: cron ----------

def cron_lines(python: str | None = None, root: Path = ROOT, log_dir: Path = LOG_DIR) -> list[str]:
    # cron only runs on POSIX systems, so paths are always written with forward slashes
    py, log, cwd = python or _python(), (log_dir / "scheduled.log").as_posix(), root.as_posix()
    run = lambda cmd: f'cd "{cwd}" && "{py}" -m jobsearch {cmd} >> "{log}" 2>&1'
    return [f"0 6 * * * {run('intel')} {CRON_MARK}",
            f"0 {SCOUT_HOURS[0]}-{SCOUT_HOURS[-1]}/2 * * * {run('scout')} {CRON_MARK}"]


def _crontab() -> list[str]:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _write_crontab(lines: list[str]) -> None:
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


# ---------- macOS: launchd ----------

def launchd_plists(python: str | None = None, root: Path = ROOT, log_dir: Path = LOG_DIR) -> dict[str, dict]:
    py = python or _python()

    def agent(label: str, cmd: str, calendar) -> dict:
        return {"Label": label, "ProgramArguments": [py, "-m", "jobsearch", cmd], "WorkingDirectory": str(root),
                "StartCalendarInterval": calendar, "StandardOutPath": str(log_dir / "scheduled.log"),
                "StandardErrorPath": str(log_dir / "scheduled.log"), "RunAtLoad": False}

    return {
        LAUNCHD_LABELS["intel"]: agent(LAUNCHD_LABELS["intel"], "intel", {"Hour": 6, "Minute": 0}),
        LAUNCHD_LABELS["scout"]: agent(LAUNCHD_LABELS["scout"], "scout", [{"Hour": h, "Minute": 0} for h in SCOUT_HOURS]),
    }


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


# ---------- commands ----------

def show() -> str:
    os_name = system()
    if os_name == "windows":
        return (f"Windows Task Scheduler via {ROOT / 'scripts' / 'schedule_scout.ps1'}:\n"
                "  JobSearch-Intel  daily 06:00\n  JobSearch-Scout  every 2h 07:00-23:00")
    if os_name == "macos":
        out = []
        for label, plist in launchd_plists().items():
            out.append(f"{_launch_agents_dir() / (label + '.plist')}:\n{plistlib.dumps(plist).decode()}")
        return "\n".join(out)
    return "crontab lines:\n" + "\n".join(cron_lines())


def install() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os_name = system()
    if os_name == "windows":
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "scripts" / "schedule_scout.ps1")], check=True)
        return "Registered JobSearch-Intel and JobSearch-Scout in Task Scheduler."
    if os_name == "macos":
        agents = _launch_agents_dir()
        agents.mkdir(parents=True, exist_ok=True)
        uid = os.getuid()
        for label, plist in launchd_plists().items():
            path = agents / f"{label}.plist"
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
            path.write_bytes(plistlib.dumps(plist))
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
        return f"Installed launchd agents in {agents}."
    kept = [line for line in _crontab() if CRON_MARK not in line]
    _write_crontab(kept + cron_lines())
    return "Added 2 lines to your crontab (see `crontab -l`)."


def remove() -> str:
    os_name = system()
    if os_name == "windows":
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "scripts" / "schedule_scout.ps1"), "-Remove"], check=True)
        return "Removed the scheduled tasks."
    if os_name == "macos":
        uid = os.getuid()
        for label in LAUNCHD_LABELS.values():
            path = _launch_agents_dir() / f"{label}.plist"
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
            path.unlink(missing_ok=True)
        return "Removed the launchd agents."
    _write_crontab([line for line in _crontab() if CRON_MARK not in line])
    return "Removed the jobsearch lines from your crontab."
