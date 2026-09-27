"""Keyword job-search APIs with free tiers. Each is skipped when its key isn't in .env."""

import logging
import os

from jobsearch.scout.models import RawPosting
from jobsearch.scout.sources.base import get_json, html_to_text, parse_iso, post_json

log = logging.getLogger(__name__)


def adzuna(queries: list[str], max_age_days: int) -> list[RawPosting]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.info("Adzuna skipped: ADZUNA_APP_ID/ADZUNA_APP_KEY not set")
        return []
    out = []
    for q in queries:
        for page in (1, 2):
            data = get_json(
                f"https://api.adzuna.com/v1/api/jobs/au/search/{page}",
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what_phrase": q,
                    "max_days_old": max_age_days,
                    "sort_by": "date",
                    "results_per_page": 50,
                    "content-type": "application/json",
                },
            )
            results = (data or {}).get("results") or []
            for j in results:
                lo, hi = j.get("salary_min"), j.get("salary_max")
                out.append(RawPosting(
                    source="adzuna",
                    external_id=str(j.get("id")),
                    company=(j.get("company") or {}).get("display_name", ""),
                    title=html_to_text(j.get("title", "")),
                    location=(j.get("location") or {}).get("display_name", ""),
                    url=j.get("redirect_url", ""),
                    posted_at=parse_iso(j.get("created")),
                    # Adzuna returns only a ~500 char snippet; the scorer is told when a description is partial
                    description=html_to_text(j.get("description", "")),
                    salary_text=f"${lo:,.0f} - ${hi:,.0f}" if lo and hi else "",
                    employment_type=j.get("contract_time", "") or "",
                ))
            if len(results) < 50:
                break
    return out


def jooble(queries: list[str], max_age_days: int) -> list[RawPosting]:
    key = os.getenv("JOOBLE_API_KEY")
    if not key:
        log.info("Jooble skipped: JOOBLE_API_KEY not set")
        return []
    out = []
    for q in queries:
        data = post_json(f"https://au.jooble.org/api/{key}", {"keywords": q, "location": "Australia"})
        for j in (data or {}).get("jobs") or []:
            out.append(RawPosting(
                source="jooble",
                external_id=str(j.get("id")),
                company=j.get("company", ""),
                title=html_to_text(j.get("title", "")),
                location=j.get("location", ""),
                url=j.get("link", ""),
                posted_at=parse_iso(j.get("updated")),
                description=html_to_text(j.get("snippet", "")),
                salary_text=j.get("salary", ""),
                employment_type=j.get("type", ""),
            ))
    return out
