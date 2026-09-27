"""The same job shows up on the company ATS, Adzuna and Jooble. Collapse them into one row."""

import re

from rapidfuzz import fuzz

_COMPANY_SUFFIX = re.compile(r"\b(pty|ltd|limited|inc|llc|group|holdings|australia|au|co)\b\.?", re.I)
_TITLE_SUBS = [(r"\bsr\b\.?", "senior"), (r"\bjr\b\.?", "junior"), (r"\bml\b", "machine learning")]

TITLE_MATCH_THRESHOLD = 92


def norm_company(name: str) -> str:
    name = _COMPANY_SUFFIX.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]+", "", name)


def norm_title(title: str) -> str:
    t = title.lower()
    for pat, rep in _TITLE_SUBS:
        t = re.sub(pat, rep, t)
    t = re.sub(r"\(.*?\)", " ", t)  # drop parentheticals like "(Remote)" or "(12 month contract)"
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def dedupe_key(company: str, title: str, city: str) -> str:
    return f"{norm_company(company)}|{norm_title(title)}|{city.lower()}"


def is_same_job(a_title: str, a_city: str, b_title: str, b_city: str) -> bool:
    """Caller guarantees the companies already match."""
    if a_city.lower() != b_city.lower():
        return False
    return fuzz.token_sort_ratio(norm_title(a_title), norm_title(b_title)) >= TITLE_MATCH_THRESHOLD
