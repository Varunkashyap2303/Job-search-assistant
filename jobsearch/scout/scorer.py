"""Scores each job against the master profile and extracts the requirements the resume
tailor (phase 2) will target. One cached-prefix Sonnet call per job."""

import logging
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from jobsearch import llm, persona
from jobsearch.config import preferences, profile, strip_todos

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 40_000


class JobAssessment(BaseModel):
    fit_score: int = Field(description="0-100 per the rubric")
    recommendation: Literal["apply_now", "apply", "maybe", "skip"]
    seniority: Literal["graduate", "junior", "mid", "senior", "lead_plus", "unclear"]
    seniority_fit: Literal["good", "stretch", "too_senior", "too_junior"]
    work_rights: Literal["ok", "citizen_or_pr_required", "clearance_required", "sponsorship_offered", "unclear"]
    role_focus: str = Field(description="e.g. 'LLM application engineering', 'ML platform', 'research'")
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    key_responsibilities: list[str]
    ats_keywords: list[str] = Field(description="exact phrases from the ad an ATS would match on")
    matched_strengths: list[str] = Field(description="candidate facts that satisfy requirements")
    gaps: list[str]
    salary: str = Field(description="salary as stated in the ad, or empty string")
    summary: str = Field(description="two sentences: what the role is and why it does or doesn't fit")


def build_system_prompt() -> str:
    prefs = preferences()
    search = prefs["search"]
    locs = search["locations"]
    candidate = yaml.safe_dump(strip_todos(profile()), sort_keys=False, allow_unicode=True)
    looking_for = search.get("looking_for", "").strip()
    return f"""You screen job ads for one candidate and judge how well each role fits them.

<candidate_profile>
{candidate}</candidate_profile>

<candidate_preferences>
- In their own words: {looking_for or "(not given)"}
- Preferred locations: {", ".join(locs.get("preferred", [])) or "anywhere in Australia"} (candidate is based in {persona.home_location() or "Australia"}). Other Australian cities{" and remote-in-Australia" if locs.get("allow_remote_au", True) else ""} are acceptable but less preferred.
- Target roles: {", ".join(search.get("target_roles", []))}.
- Not keen on: {search.get("avoid_roles") or "nothing specified"}. Score these lower even when the skills overlap.
- Work rights: {persona.work_rights_text()}
</candidate_preferences>

<rubric>
fit_score:
- 85-100: core stack and responsibilities match the candidate's demonstrated work, seniority fits. Apply now.
- 70-84: good match with minor, learnable gaps.
- 50-69: partial match; notable gaps in required skills or a seniority stretch.
- 0-49: poor match.
Hard caps:
- Requires citizenship, permanent residency or a security clearance that the candidate's work rights (above) can't meet: work_rights accordingly, fit_score <= 15, recommendation "skip".
- Requires substantially more years of experience than the candidate has: seniority_fit "too_senior", fit_score <= 55.
Judge seniority from the candidate's actual dated experience, not from their title. Postings from aggregators may contain only a snippet of the ad; when the description is partial, score conservatively on what's visible and say so in the summary.
Only list matched_strengths that are supported by facts in the candidate profile.
</rubric>"""


def build_user_prompt(job) -> str:
    desc = job.description or "(no description available)"
    note = ""
    if len(desc) > MAX_DESCRIPTION_CHARS:
        log.info("Description for job %s truncated from %d chars", job.id, len(desc))
        desc = desc[:MAX_DESCRIPTION_CHARS]
        note = "\n[description truncated for length]"
    partial = any(s["source"] in ("adzuna", "jooble") for s in job.sources) and not any(
        s["source"] not in ("adzuna", "jooble") for s in job.sources
    )
    return f"""<job>
Title: {job.title}
Company: {job.company}
Location: {job.location_raw} (classified: {job.city}, {job.location_class})
Posted: {job.posted_at.date() if job.posted_at else "unknown"}
Employment type: {job.employment_type or "unknown"}
Salary: {job.salary_text or "not stated"}
Description is {"a partial snippet from an aggregator" if partial else "the full ad"}.

{desc}{note}
</job>

Assess this job for the candidate."""


def score(job, system_prompt: str) -> JobAssessment:
    model = preferences()["scoring"]["model"]
    result = llm.parse(
        agent="scout.scorer",
        model=model,
        system=system_prompt,
        user=build_user_prompt(job),
        output_format=JobAssessment,
        max_tokens=2500,
        effort="low",
    )
    result.fit_score = max(0, min(100, result.fit_score))
    return result
