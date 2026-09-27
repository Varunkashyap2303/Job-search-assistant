"""Cheap, deterministic filters applied before any LLM call, to keep token spend down."""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from jobsearch.scout.models import RawPosting

_NON_AU = re.compile(
    r"\b(united states|usa|u\.s\.|us\b|united kingdom|uk\b|england|scotland|canada|florida|fl\b|"
    r"new zealand|nz\b|singapore|india|ireland|germany|nova scotia)\b",
    re.I,
)
# Only distinctive state codes: "WA" is also Washington, "SA"/"ACT"/"NT" are too ambiguous.
_AU = re.compile(r"\b(australia|au|aus|anz|apac|nsw|vic|qld)\b", re.I)
_REMOTE = re.compile(r"\bremote|anywhere|distributed|work from home|wfh\b", re.I)

CLASS_RANK = {"preferred": 3, "remote_au": 2, "acceptable": 1}


def classify_location(location: str, prefs: dict) -> Optional[tuple[str, str]]:
    """Return (city, location_class) for the best Australian option in a location string,
    or None when the job isn't in Australia."""
    locs = prefs["search"]["locations"]
    best: Optional[tuple[str, str]] = None
    for part in re.split(r"[;|/]", location or ""):
        part = part.strip()
        if not part:
            continue
        found = None
        for city in locs["preferred"]:
            if re.search(rf"\b{re.escape(city)}\b", part, re.I):
                found = (city, "preferred")
                break
        if not found:
            for city in locs["acceptable"]:
                if re.search(rf"\b{re.escape(city)}\b", part, re.I):
                    found = (city, "acceptable")
                    break
        if found and _NON_AU.search(part) and not _AU.search(part):
            found = None  # e.g. "Melbourne, FL" or "Perth, Scotland"
        if not found and _AU.search(part) and not _NON_AU.search(part):
            if _REMOTE.search(part):
                found = ("Remote (AU)", "remote_au") if locs.get("allow_remote_au", True) else None
            elif re.search(r"\baustralia\b", part, re.I):
                found = ("Australia", "acceptable")
        if found and (best is None or CLASS_RANK[found[1]] > CLASS_RANK[best[1]]):
            best = found
    return best


def title_passes(title: str, ai_native: bool, prefs: dict) -> tuple[bool, str]:
    s = prefs["search"]
    for pat in s["title_exclude"]:
        if re.search(pat, title, re.I):
            return False, f"title excluded ({pat})"
    includes = s["title_include"] + (s.get("ai_native_title_include", []) if ai_native else [])
    if any(re.search(pat, title, re.I) for pat in includes):
        return True, ""
    return False, "title not AI/ML"


def too_old(posted_at: Optional[datetime], prefs: dict, now: Optional[datetime] = None) -> bool:
    if posted_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - posted_at > timedelta(days=prefs["search"]["max_age_days"])


FRESHNESS_TIERS = {"A": "< 24h", "B": "1-7 days", "C": "7-30 days"}


def freshness_tier(posted_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if posted_at is None:
        return "C"
    age = (now or datetime.now(timezone.utc)) - posted_at
    if age <= timedelta(hours=24):
        return "A"
    return "B" if age <= timedelta(days=7) else "C"


def work_rights_blocker(text: str, prefs: dict) -> str:
    """Return the matched pattern if the posting requires something the user's work rights can't meet
    (derived from their profile; see persona.hard_exclude_patterns)."""
    from jobsearch import persona

    for pat in persona.hard_exclude_patterns():
        if re.search(pat, text or "", re.I):
            return pat
    return ""


def prefilter(p: RawPosting, prefs: dict) -> tuple[Optional[tuple[str, str]], str]:
    """Apply all metadata-only filters. Returns (location, reject_reason)."""
    if too_old(p.posted_at, prefs):
        return None, "older than max_age_days"
    ok, reason = title_passes(p.title, p.ai_native, prefs)
    if not ok:
        return None, reason
    loc = classify_location(p.location, prefs)
    if loc is None:
        return None, "not in Australia"
    return loc, ""
