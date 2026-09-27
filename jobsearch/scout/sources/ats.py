"""Public job-board APIs of the ATS vendors most AU startups use. No auth, no scraping:
these endpoints exist so companies can embed their job lists on their own sites."""

import html
import logging
from datetime import datetime, timezone

from jobsearch.scout.models import RawPosting
from jobsearch.scout.sources.base import from_epoch_ms, get_json, html_to_text, parse_iso

log = logging.getLogger(__name__)


def _posting(company: dict, **kw) -> RawPosting:
    return RawPosting(company=company["name"], is_ats=True, ai_native=bool(company.get("ai_native")), **kw)


def greenhouse(company: dict) -> list[RawPosting]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}/jobs", params={"content": "true"})
    out = []
    for j in (data or {}).get("jobs", []):
        offices = [o.get("name", "").strip() for o in j.get("offices") or []]
        location = "; ".join(filter(None, [(j.get("location") or {}).get("name", ""), *offices]))
        out.append(_posting(
            company,
            source="greenhouse",
            external_id=str(j["id"]),
            title=j.get("title", ""),
            location=location,
            url=j.get("absolute_url", ""),
            posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(html.unescape(j.get("content") or "")),
        ))
    return out


def lever(company: dict) -> list[RawPosting]:
    data = get_json(f"https://api.lever.co/v0/postings/{company['slug']}", params={"mode": "json"})
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location", "")]
        if j.get("workplaceType") == "remote":
            locations = [*locations, f"Remote ({j.get('country', '')})"]
        sections = [j.get("descriptionPlain", "")]
        for lst in j.get("lists") or []:
            sections.append(lst.get("text", ""))
            sections.append(html_to_text(lst.get("content", "")))
        sections.append(j.get("additionalPlain", ""))
        out.append(_posting(
            company,
            source="lever",
            external_id=j["id"],
            title=j.get("text", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("hostedUrl", ""),
            posted_at=from_epoch_ms(j.get("createdAt")),
            description="\n".join(filter(None, sections)),
            employment_type=cats.get("commitment", ""),
        ))
    return out


def ashby(company: dict) -> list[RawPosting]:
    data = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}",
        params={"includeCompensation": "true"},
    )
    out = []
    for j in (data or {}).get("jobs", []):
        if not j.get("isListed", True):
            continue
        locations = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress")) or {}).get("addressCountry", "")
        if j.get("isRemote"):
            locations.append(f"Remote ({country})")
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(_posting(
            company,
            source="ashby",
            external_id=j["id"],
            title=j.get("title", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("jobUrl", ""),
            posted_at=parse_iso(j.get("publishedAt")),
            description=j.get("descriptionPlain", ""),
            salary_text=comp,
            employment_type=j.get("employmentType", ""),
        ))
    return out


def workable(company: dict) -> list[RawPosting]:
    data = get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{company['slug']}",
        params={"details": "true"},
    )
    out = []
    for j in (data or {}).get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]
        location = "; ".join(
            ", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")])) for l in locs
        )
        if j.get("telecommuting"):
            location += f"; Remote ({j.get('country', '')})"
        published = j.get("published_on") or j.get("created_at")
        posted_at = parse_iso(published) if published and "T" in published else (
            datetime.fromisoformat(published).replace(tzinfo=timezone.utc) if published else None
        )
        out.append(_posting(
            company,
            source="workable",
            external_id=j.get("shortcode", j.get("url", "")),
            title=j.get("title", ""),
            location=location,
            url=j.get("url") or j.get("application_url", ""),
            posted_at=posted_at,
            description=html_to_text(j.get("description", "")),
            employment_type=j.get("employment_type", ""),
        ))
    return out


def smartrecruiters(company: dict) -> list[RawPosting]:
    slug = company["slug"]
    out, offset = [], 0
    while True:
        data = get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 100, "offset": offset},
        )
        content = (data or {}).get("content") or []
        for j in content:
            loc = j.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
            if loc.get("remote"):
                location += f"; Remote ({loc.get('country', '').upper()})"
            out.append(_posting(
                company,
                source="smartrecruiters",
                external_id=str(j["id"]),
                title=j.get("name", ""),
                location=location,
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                posted_at=parse_iso(j.get("releasedDate")),
                employment_type=(j.get("typeOfEmployment") or {}).get("label", ""),
                fetch_description=_sr_description(j.get("ref", "")),
            ))
        offset += len(content)
        if not content or offset >= (data or {}).get("totalFound", 0):
            break
    return out


def _sr_description(ref: str):
    def fetch() -> str:
        detail = get_json(ref) if ref else None
        sections = ((detail or {}).get("jobAd") or {}).get("sections") or {}
        return "\n\n".join(
            f"{s.get('title', '')}\n{html_to_text(s.get('text', ''))}" for s in sections.values() if s.get("text")
        )
    return fetch


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
}


def fetch_company(company: dict) -> list[RawPosting]:
    fetcher = FETCHERS.get(company.get("ats", ""))
    if fetcher is None:
        log.warning("Unknown ATS %r for %s", company.get("ats"), company.get("name"))
        return []
    try:
        return fetcher(company)
    except Exception:
        log.exception("Failed to fetch %s (%s)", company.get("name"), company.get("ats"))
        return []
