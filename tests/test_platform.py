"""Cross-platform pieces that can be checked on any OS without installing anything."""

import plistlib
from pathlib import Path

from jobsearch import browser, schedule


def test_cron_lines_are_marked_and_timed():
    lines = schedule.cron_lines(python="/home/u/job/.venv/bin/python", root=Path("/home/u/job"), log_dir=Path("/home/u/job/data/logs"))
    assert lines[0].startswith("0 6 * * * ") and "-m jobsearch intel" in lines[0]
    assert lines[1].startswith("0 7-23/2 * * * ") and "-m jobsearch scout" in lines[1]
    assert all(line.endswith(schedule.CRON_MARK) for line in lines)
    assert all('cd "/home/u/job"' in line and ">>" in line for line in lines)


def test_launchd_plists_are_valid():
    plists = schedule.launchd_plists(python="/Users/u/job/.venv/bin/python", root=Path("/Users/u/job"),
                                     log_dir=Path("/Users/u/job/data/logs"))
    intel, scout = plists[schedule.LAUNCHD_LABELS["intel"]], plists[schedule.LAUNCHD_LABELS["scout"]]
    assert intel["ProgramArguments"][-1] == "intel" and intel["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert [c["Hour"] for c in scout["StartCalendarInterval"]] == [7, 9, 11, 13, 15, 17, 19, 21, 23]
    for p in plists.values():
        assert plistlib.loads(plistlib.dumps(p)) == p   # serialises to a valid plist


def test_browser_order_and_override(monkeypatch):
    monkeypatch.setattr(browser, "_working", None)
    monkeypatch.delenv("JOBSEARCH_BROWSER", raising=False)
    assert browser._order() == ["msedge", "chrome", "chromium"]
    monkeypatch.setattr(browser, "_working", "chrome")
    assert browser._order()[0] == "chrome"                 # remembered after a successful launch
    monkeypatch.setenv("JOBSEARCH_BROWSER", "chromium")
    assert browser._order() == ["chromium"]


def test_no_browser_error_explains_the_fix(monkeypatch):
    class FakeChromium:
        def launch(self, channel=None, headless=True):
            from playwright.sync_api import Error
            raise Error(f"Executable doesn't exist for {channel or 'chromium'}")
    monkeypatch.setattr(browser, "_working", None)
    monkeypatch.delenv("JOBSEARCH_BROWSER", raising=False)
    try:
        browser.launch(type("P", (), {"chromium": FakeChromium()})())
    except browser.NoBrowser as e:
        assert "playwright install chromium" in str(e) and "msedge" in str(e)
    else:
        raise AssertionError("expected NoBrowser")
