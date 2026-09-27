"""Free enrichment for a newly found startup: website text, careers page, and whether it posts jobs
on a public ATS board. Startups with an ATS board are added to the Job Scout watchlist."""

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from jobsearch.config import CONFIG_DIR, preferences, watchlist
from jobsearch.scout.dedupe import norm_company
from jobsearch.scout.filters import classify_location
from jobsearch.scout.sources import ats
from jobsearch.scout.sources.base import http

log = logging.getLogger(__name__)

TLDS = [".com.au", ".ai", ".com", ".io", ".co", ".app"]
ATS_PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}
UA = {"User-Agent": "Mozilla/5.0 (personal job search)"}
_PARKED = re.compile(r"launching soon|coming soon|domain (is )?for sale|buy this domain|parked|under construction", re.I)


def slugs(name: str) -> list[str]:
    base = re.sub(r"\b(pty|ltd|limited|inc|ai|labs?|technologies|tech|group|hq)\b", " ", name.lower())
    compact = re.sub(r"[^a-z0-9]+", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    full = re.sub(r"[^a-z0-9]+", "", name.lower())
    return list(dict.fromkeys(s for s in (full, compact, hyphen) if len(s) >= 3))


def page_text(url: str, limit: int) -> tuple[str, str]:
    """Return (title, visible text) or ('', '') on failure."""
    try:
        r = http().get(url, headers=UA, timeout=10)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return "", ""
    except Exception:
        return "", ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return title, text[:limit]


def find_website(name: str, stated: str = "") -> tuple[str, str]:
    """Return (url, homepage text). A candidate counts only if the page names the company."""
    key = norm_company(name)
    candidates = []
    if stated:
        candidates.append(stated if stated.startswith("http") else f"https://{stated}")
    candidates += [f"https://{s}{tld}" for s in slugs(name) for tld in TLDS]
    for url in dict.fromkeys(candidates):
        title, text = page_text(url, 4000)
        if len(text) < 400 or _PARKED.search(text[:1500]):
            continue  # parked, placeholder or JS-only pages tell us nothing
        if key and key in norm_company(f"{title} {text[:3000]}"):
            return url, f"{title}\n{text}"
    return "", ""


def careers_text(website: str) -> str:
    for path in ("/careers", "/jobs", "/join-us", "/about"):
        _, text = page_text(website.rstrip("/") + path, 2500)
        if len(text) > 300:
            return f"[{path}] {text}"
    return ""


def detect_ats(name: str) -> dict:
    """Find the company's public job board. Returns {ats, slug, roles} or {}."""
    prefs = preferences()
    for slug in slugs(name):
        for ats_name, template in ATS_PROBES.items():
            try:
                r = http().get(template.format(slug=slug), timeout=10)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            postings = ats.fetch_company({"name": name, "ats": ats_name, "slug": slug})
            # A generic slug can belong to an unrelated overseas company: require an Australian posting
            if not any(classify_location(p.location, prefs) for p in postings):
                continue
            roles = [f"{p.title} ({p.location})" for p in postings[:40]]
            return {"ats": ats_name, "slug": slug, "roles": roles, "open_roles": len(postings)}
    return {}


def add_to_watchlist(name: str, careers: dict, ai_native: bool) -> bool:
    """Append the startup to config/watchlist.yaml so the Job Scout polls its job board."""
    if not careers.get("ats"):
        return False
    existing = {(c.get("ats"), c.get("slug")) for c in watchlist()}
    if (careers["ats"], careers["slug"]) in existing:
        return False
    safe = name.replace('"', "'")
    ats_name, slug = careers["ats"], careers["slug"]
    native = ", ai_native: true" if ai_native else ""
    line = f'  - {{name: "{safe}", ats: {ats_name}, slug: {slug}{native}}}   # added by Startup Intel {date.today()}\n'
    with open(CONFIG_DIR / "watchlist.yaml", "a", encoding="utf-8") as f:
        f.write(line)
    log.info("Added %s (%s/%s) to watchlist", name, careers["ats"], careers["slug"])
    return True
