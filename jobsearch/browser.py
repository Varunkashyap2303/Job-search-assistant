"""One way to start a browser on Windows, macOS and Linux. Used for PDF rendering and form filling.

Tries, in order: Microsoft Edge, Google Chrome, then Playwright's own Chromium
(install it with `python -m playwright install chromium`; on Linux add `--with-deps`).
Set JOBSEARCH_BROWSER=msedge|chrome|chromium in .env to force one."""

import os

from playwright.sync_api import Browser, Error as PlaywrightError, Playwright

CHANNELS = ["msedge", "chrome", "chromium"]
_working: str | None = None   # remembered after the first successful launch


class NoBrowser(RuntimeError):
    pass


def _order() -> list[str]:
    forced = os.getenv("JOBSEARCH_BROWSER", "").strip().lower()
    if forced:
        return [forced]
    return ([_working] if _working else []) + [c for c in CHANNELS if c != _working]


def launch(p: Playwright, headless: bool = True) -> Browser:
    global _working
    errors = []
    for channel in _order():
        try:
            browser = p.chromium.launch(channel=None if channel == "chromium" else channel, headless=headless)
            _working = channel
            return browser
        except PlaywrightError as e:
            errors.append(f"{channel}: {str(e).splitlines()[0][:120]}")
    raise NoBrowser("No Chromium-based browser could be started. Install Microsoft Edge or Google Chrome, or run "
                    "`python -m playwright install chromium` (Linux: add --with-deps). Tried: " + "; ".join(errors))
