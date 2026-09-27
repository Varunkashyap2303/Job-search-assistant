"""Finds people to contact at a startup from public, non-LinkedIn sources: people named in news,
the company's own team/about/contact pages, and email addresses the company publishes."""

import logging
import re
import urllib.parse

from pydantic import BaseModel

from jobsearch import llm
from jobsearch.config import preferences
from jobsearch.intel.enrich import page_text

log = logging.getLogger(__name__)

TEAM_PATHS = ["/about", "/about-us", "/team", "/our-team", "/company", "/people", "/contact", "/careers"]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NOT_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|example\.|sentry|wixpress", re.I)


class TeamMember(BaseModel):
    name: str
    role: str


class TeamPage(BaseModel):
    people: list[TeamMember]


def name_parts(name: str) -> list[str]:
    """Lowercase name tokens without honorifics: 'Dr Tom Kelly' -> ['tom', 'kelly']."""
    return [x for x in re.split(r"[^a-z]+", name.lower()) if x and x not in ("dr", "mr", "ms", "mrs", "prof")]


def domain_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def linkedin_people_search(*terms: str) -> str:
    """A link for the user to click; we never fetch LinkedIn ourselves."""
    q = " ".join(t for t in terms if t)
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(q)


def site_pages(website: str) -> dict[str, str]:
    pages = {}
    for path in TEAM_PATHS:
        _, text = page_text(website.rstrip("/") + path, 5000)
        if len(text) > 200:
            pages[path] = text
    return pages


def published_emails(texts: list[str], domain: str) -> list[str]:
    found = set()
    for t in texts:
        for e in _EMAIL.findall(t):
            e = e.strip(".").lower()
            if domain and e.endswith("@" + domain) and not _NOT_EMAIL.search(e):
                found.add(e)
    return sorted(found)


def email_pattern(emails: list[str], people: list[dict]) -> str:
    """Infer the company's address format only from a published address that matches a known person."""
    for p in people:
        parts = name_parts(p["name"])
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        for e in emails:
            local = e.split("@")[0]
            for pattern, value in (("first.last", f"{first}.{last}"), ("firstlast", f"{first}{last}"),
                                   ("first", first), ("flast", f"{first[0]}{last}"), ("first_last", f"{first}_{last}")):
                if local == value:
                    return pattern
    return ""


def apply_pattern(pattern: str, name: str, domain: str) -> str:
    parts = name_parts(name)
    if not (pattern and domain and len(parts) >= 2):
        return ""
    first, last = parts[0], parts[-1]
    local = {"first.last": f"{first}.{last}", "firstlast": f"{first}{last}", "first": first,
             "flast": f"{first[0]}{last}", "first_last": f"{first}_{last}"}[pattern]
    return f"{local}@{domain}"


def team_from_pages(pages: dict[str, str], company: str) -> list[dict]:
    text = "\n\n".join(f"[{path}]\n{body}" for path, body in pages.items() if path != "/careers")
    if len(text) < 300:
        return []
    result = llm.parse(
        agent="outreach.people",
        model=preferences()["models"]["default"],
        system="You list the people named on a company's own website pages, with their role. "
               "Include only people clearly presented as working at the company (founders, executives, "
               "engineering/AI leaders, talent/people team). Never guess names or roles.",
        user=f"Company: {company}\n\n{text[:12000]}",
        output_format=TeamPage,
        max_tokens=1500,
        effort="low",
    )
    return [{"name": m.name, "role": m.role, "source": "company website"} for m in result.people]


def discover(startup) -> dict:
    """Everything we know about who to contact, with where each item came from."""
    profile = startup.profile or {}
    people = [{"name": p["name"], "role": p["role"], "source": p.get("source", "news")}
              for p in profile.get("people", [])]
    pages, emails, domain = {}, [], ""
    if startup.website:
        domain = domain_of(startup.website)
        pages = site_pages(startup.website)
        try:
            for member in team_from_pages(pages, startup.name):
                if all(member["name"].lower() != p["name"].lower() for p in people):
                    people.append(member)
        except llm.LLMError as e:
            log.warning("Team extraction failed for %s: %s", startup.name, e)
        emails = published_emails([startup.site_excerpt, *pages.values()], domain)
    pattern = email_pattern(emails, people)
    for p in people:
        parts = name_parts(p["name"])
        exact = next((e for e in emails if parts and e.split("@")[0].startswith(parts[0])), "")
        if exact:
            p["email"], p["email_confidence"] = exact, "published"
        elif pattern:
            p["email"], p["email_confidence"] = apply_pattern(pattern, p["name"], domain), "pattern (unverified)"
        else:
            p["email"], p["email_confidence"] = "", "none"
        p["linkedin_search"] = linkedin_people_search(p["name"], startup.name)
    return {
        "people": people,
        "generic_emails": [e for e in emails if not any(e == p.get("email") for p in people)],
        "email_pattern": pattern,
        "pages_checked": list(pages),
        "website": startup.website,
    }
