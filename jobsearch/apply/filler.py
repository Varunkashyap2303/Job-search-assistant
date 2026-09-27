"""Fills a real application form with Playwright (Edge, Chrome or Chromium; see jobsearch/browser.py). It never submits:
the Submit button is always clicked by you.

Two modes, both started by you from the dashboard:
- preview : headless; fill everything, read the values back, take a full-page screenshot, close.
- prefill : visible window; fill everything and hand over. You check the form, attach anything
            missing, and click Submit yourself (solving any CAPTCHA as a normal visitor would).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from jobsearch import browser as browsers

log = logging.getLogger(__name__)


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # "label: what went wrong"
    screenshot: str = ""
    note: str = ""


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _locate(page: Page, key: str):
    loc = page.locator(f'[id="{_q(key)}"]')
    if loc.count() == 0:
        loc = page.locator(f'[name="{_q(key)}"]')
    return loc


def _choose_from_combobox(page: Page, box, value: str) -> None:
    box.click()
    box.fill("")
    box.type(value[:40], delay=20)
    option = page.get_by_role("option", name=re.compile(re.escape(value[:40]), re.I)).first
    option.wait_for(timeout=5000)
    option.click()


def _fill_one(page: Page, ats: str, f: dict) -> None:
    key, typ, value = f["key"], f["type"], f["answer"]
    values = [v.strip() for v in value.split("|")] if isinstance(value, str) and typ in ("multiselect", "checkbox") else [value]
    loc = _locate(page, key)

    if typ == "file":
        if not value or not Path(value).exists():
            raise ValueError(f"file not found: {value}")
        loc.first.set_input_files(value)
        return
    if typ == "radio" or (typ == "checkbox" and ats == "lever"):
        for v in values:
            page.locator(f'[name="{_q(key)}"][value="{_q(v)}"]').check()
        return
    if typ == "boolean":  # Ashby renders Yes/No buttons next to a hidden checkbox
        container = loc.first.locator("xpath=ancestor::*[.//button][1]")
        container.get_by_role("button", name=re.compile(rf"^{re.escape(value)}$", re.I)).click()
        return
    if typ in ("select", "multiselect"):
        el = loc.first
        if el.evaluate("e => e.tagName") == "SELECT":
            el.select_option(label=values if typ == "multiselect" else values[0])
        else:  # searchable combobox (Greenhouse job boards)
            for v in values:
                _choose_from_combobox(page, el, v)
        return
    if typ == "location":
        el = loc.first
        el.fill("")
        el.type(value, delay=30)
        try:
            page.get_by_role("option").filter(has_text=re.compile(re.escape(value.split(",")[0]), re.I)).first.click(timeout=5000)
        except PlaywrightError:
            el.press("ArrowDown")
            el.press("Enter")
        return
    loc.first.fill(str(value))


def _read_back(page: Page, f: dict) -> str:
    """Return a problem description if a typed value doesn't show what was approved."""
    if f["type"] not in ("text", "textarea", "email", "phone") or not f["answer"]:
        return ""
    try:
        actual = _locate(page, f["key"]).first.input_value()
    except PlaywrightError as e:
        return f"couldn't read back ({e.__class__.__name__})"
    if f["type"] == "phone":  # phone widgets reformat the digits
        same = re.sub(r"\D", "", actual)[-9:] == re.sub(r"\D", "", str(f["answer"]))[-9:]
    else:
        same = re.sub(r"\s+", " ", actual).strip() == re.sub(r"\s+", " ", str(f["answer"])).strip()
    return "" if same else f"shows '{actual[:60]}'"


def fill_form(page: Page, ats: str, fields: list[dict], result: FillResult) -> None:
    for f in fields:
        if f.get("answer") in ("", None, []):
            continue
        try:
            _fill_one(page, ats, f)
            result.filled.append(f["label"])
        except (PlaywrightError, ValueError) as e:
            result.problems.append(f"{f['label'][:70]}: couldn't fill ({str(e).splitlines()[0][:120]})")
    result.problems += [f"{f['label'][:70]}: {m}" for f in fields if (m := _read_back(page, f))]


def _open(p, headless: bool):
    browser = browsers.launch(p, headless=headless)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-AU", timezone_id="Australia/Melbourne")
    return browser, ctx.new_page()


def _goto(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightError:
        pass


def preview(url: str, ats: str, fields: list[dict], screenshot_path: Path) -> FillResult:
    result = FillResult()
    with sync_playwright() as p:
        browser, page = _open(p, headless=True)
        try:
            _goto(page, url)
            fill_form(page, ats, fields, result)
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            result.screenshot = str(screenshot_path)
        finally:
            browser.close()
    return result


def prefill(url: str, ats: str, fields: list[dict], wait_minutes: int = 30) -> FillResult:
    """Visible window, filled in and handed over. Returns when you close the window."""
    result = FillResult()
    with sync_playwright() as p:
        browser, page = _open(p, headless=False)
        _goto(page, url)
        fill_form(page, ats, fields, result)
        try:
            page.wait_for_event("close", timeout=wait_minutes * 60_000)
        except PlaywrightError:
            pass
        browser.close()
    result.note = "Window closed. If you submitted the application, click Mark applied."
    return result
