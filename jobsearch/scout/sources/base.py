import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx2 as httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "jobsearch-personal/0.1 (personal job search; low volume)"
_http: httpx.Client | None = None


def http() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    return _http


def get_json(url: str, *, params: dict | None = None, retries: int = 3):
    """GET with polite backoff on 429/5xx. Returns None on persistent failure so one broken
    source never aborts the whole run."""
    for attempt in range(retries):
        try:
            r = http().get(url, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("retry-after", 5 * (attempt + 1)))
                log.warning("HTTP %s from %s, retrying in %.0fs", r.status_code, url, wait)
                time.sleep(min(wait, 60))
                continue
            if r.status_code != 200:
                log.warning("HTTP %s from %s", r.status_code, url)
                return None
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Request to %s failed: %s", url, e)
            time.sleep(2 * (attempt + 1))
    return None


def post_json(url: str, payload: dict):
    try:
        r = http().post(url, json=payload)
        if r.status_code != 200:
            log.warning("HTTP %s from %s", r.status_code, url)
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Request to %s failed: %s", url, e)
        return None


def html_to_text(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def from_epoch_ms(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
