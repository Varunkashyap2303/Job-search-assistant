"""ATS rules for tailored resumes. Recruiters search an applicant tracking system like a database, so a
resume has to parse cleanly and match the words they search for.

Deterministic and free: `enforce` fixes what code can fix (exact job title in the header, no icons or
emojis); `report` measures the rest (keyword count, date format) for the Approvals page. Nothing here
adds a claim, so the fact checker's guarantees still hold."""

import re
import unicodedata

from jobsearch.tailor.coverage import resume_text
from jobsearch.tailor.facts import Catalogue, fmt_range
from jobsearch.tailor.tailor import TailoredResume

KEYWORD_MIN, KEYWORD_MAX = 25, 35   # fewer rarely surfaces in searches; more reads as keyword stuffing
MAX_REPEATS = 5                     # one term used more often than this looks stuffed
SECTION_HEADERS = ("Professional Summary", "Skills", "Work Experience", "Projects", "Education")

_DATE = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}|Present")
_ICON_CATEGORIES = {"So", "Sk", "Cs", "Co"}          # symbols, emoji, private-use icon fonts
_INVISIBLE = {"​", "‌", "‍", "︎", "️", "⁠"}


# Trailing parts of a board's title line that aren't the title: place, work mode, contract terms
_CLUTTER = re.compile(r"\b(remote|hybrid|on-?site|wfh|contract|permanent|casual|temp|full[- ]time|part[- ]time|"
                      r"months?|australia|nsw|vic|qld|wa|sa|act|tas|nt|sydney|melbourne|brisbane|perth|adelaide|"
                      r"canberra|hobart|darwin)\b", re.I)


def clean_title(title: str, city: str = "") -> str:
    """The posting's job title without board clutter: 'AI Engineer (Hybrid) - Sydney' -> 'AI Engineer'.
    A suffix that is part of the title stays: 'AI Engineer - AI Services' is returned whole."""
    t = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", str(title or ""))
    parts = re.split(r"(\s+[-–—|]\s+)", t)
    while len(parts) > 1 and (_CLUTTER.search(parts[-1]) or (city and city.lower() in parts[-1].lower())):
        parts = parts[:-2]
    return " ".join("".join(parts).split()).strip(" -–—|,")


def strip_icons(text: str) -> str:
    """Remove emojis and icon glyphs: a parser reads them as noise or drops the text next to them."""
    kept = "".join(ch for ch in text if ch not in _INVISIBLE and unicodedata.category(ch) not in _ICON_CATEGORIES)
    return re.sub(r"[ \t]{2,}", " ", kept).strip()


def _contains(term: str, text: str) -> bool:
    """Literal, case-insensitive match on word boundaries, the way an ATS keyword filter matches."""
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text))


def enforce(r: TailoredResume, job) -> tuple[TailoredResume, list[str]]:
    """Apply the fixes code can make safely. Returns the fixed resume and a list of what changed."""
    r = r.model_copy(deep=True)
    fixes: list[str] = []

    before = r.model_dump_json()
    r.headline = strip_icons(r.headline)
    r.summary.text = strip_icons(r.summary.text)
    for g in r.skills:
        g.label = strip_icons(g.label)
        g.items = [x for x in (strip_icons(i) for i in g.items) if x]
    for b in (*(b for c in (*r.experience, *r.projects) for b in c.bullets), *r.cover_letter):
        b.text = strip_icons(b.text)
    if r.model_dump_json() != before:
        fixes.append("removed icons or emojis")

    # The headline names the role being applied for; the titles actually held stay as the profile has them.
    title = clean_title(job.title, getattr(job, "city", "") or "")
    if title and not _contains(title, r.headline.lower()):
        rest = [p.strip() for p in r.headline.split("|") if p.strip() and p.strip().lower() not in title.lower()]
        r.headline = " | ".join([title, *rest[:2]])
        fixes.append(f"put the exact job title '{title}' in the header")
    return r, fixes


def keyword_report(r: TailoredResume, job) -> dict:
    """Count the ad's own terms that appear word for word in the resume."""
    a = job.assessment or {}
    ad = " ".join([job.title or "", job.description or "", *a.get("must_have_skills", []),
                   *a.get("nice_to_have_skills", []), *a.get("ats_keywords", [])]).lower()
    text = resume_text(r).lower()
    pool = [*r.ad_terms, *a.get("ats_keywords", []), *a.get("must_have_skills", []), *a.get("nice_to_have_skills", [])]
    used, seen = [], set()
    for term in pool:
        key = " ".join(str(term).lower().split())
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        if _contains(key, ad) and _contains(key, text):
            used.append(" ".join(str(term).split()))
    repeated = [t for t in used
                if len(re.findall(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", text)) > MAX_REPEATS]
    n = len(used)
    status = "below" if n < KEYWORD_MIN else "above" if n > KEYWORD_MAX else "within"
    return {"count": n, "status": status, "terms": used, "repeated": repeated}


def date_issues(r: TailoredResume, cat: Catalogue) -> list[str]:
    """Dates that aren't 'Mon YYYY': mixed formats make an ATS miscount years of experience."""
    shown = [(cat.entries[c.ref].heading, cat.entries[c.ref].dates)
             for c in (*r.experience, *r.projects) if c.ref in cat.entries]
    shown += [(str(ed.get("degree", "")), fmt_range(ed.get("dates", ""))) for ed in cat.education]
    out = []
    for where, dates in shown:
        parts = [p.strip() for p in dates.split(" - ")] if dates else []
        if any(not _DATE.fullmatch(p) for p in parts):
            out.append(f"{where}: '{dates}'")
    return out


def report(r: TailoredResume, cat: Catalogue, job, fixes: list[str] | None = None) -> dict:
    title = clean_title(job.title, getattr(job, "city", "") or "")
    return {
        "title": title,
        "title_in_header": bool(title) and _contains(title, r.headline.lower()),
        "keywords": keyword_report(r, job),
        "date_issues": date_issues(r, cat),
        "fixes": list(fixes or []),
    }
