"""Getting a new user set up: read their resume into a structured profile, and turn a plain-English
description of the jobs they want into search settings. Both produce drafts the user reviews."""

import io
import re

import pypdfium2 as pdfium
from pydantic import BaseModel, Field

from jobsearch import llm
from jobsearch.config import preferences

# ---------- resume -> text ----------


def resume_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        doc = pdfium.PdfDocument(data)
        try:
            return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
        finally:
            doc.close()
    if name.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
        return "\n".join(parts)
    return data.decode("utf-8", errors="replace")


# ---------- text -> profile ----------


class SkillGroupIn(BaseModel):
    group: str
    items: list[str]


class ExperienceIn(BaseModel):
    title: str
    company: str
    location: str
    start: str = Field(description="YYYY-MM, or YYYY if only the year is given, or empty")
    end: str = Field(description="YYYY-MM, YYYY, 'present', or empty")
    project: str = Field(description="named product/project the role centred on, if any, else empty")
    stack: list[str]
    facts: list[str] = Field(description="one entry per resume bullet or achievement, wording kept")


class ProjectIn(BaseModel):
    name: str
    dates: str = Field(description="e.g. '2025-07 to 2025-11', '2024', or empty")
    award: str
    stack: list[str]
    facts: list[str]


class EducationIn(BaseModel):
    degree: str
    institution: str
    location: str
    dates: str = Field(description="e.g. '2019-02 to 2022-11' or empty")


class ProfileIn(BaseModel):
    name: str
    headline: str
    email: str
    phone: str
    location: str
    linkedin: str
    github: str
    portfolio: str
    work_rights_line: str = Field(description="work rights as stated on the resume, or empty")
    summary: str
    skills: list[SkillGroupIn]
    experience: list[ExperienceIn]
    projects: list[ProjectIn]
    education: list[EducationIn]
    certifications: list[str]
    awards: list[str]


IMPORT_SYSTEM = """You convert a resume into a structured profile. This profile becomes the candidate's source
of truth: every later resume, cover letter and application answer may only use what you record here.

- Copy facts faithfully. Split each bullet or achievement into its own fact; keep the candidate's wording and
  every number exactly. Never add, infer, merge in, or embellish anything.
- Leave a field empty when the resume doesn't state it. Don't guess dates, locations or links.
- Dates as YYYY-MM where the month is given, otherwise YYYY. Current roles end "present".
- stack: technologies/tools the resume ties to that role or project.
- Skills: keep the resume's own groupings if it has them, otherwise group sensibly."""


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "", text.lower())[:16] or "item"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}{n}", n + 1
    taken.add(slug)
    return slug


def import_resume(text: str) -> dict:
    """Returns profile sections (identity, summary, skills, experience, projects, education, ...)
    ready to merge into profile.yaml. Uses the tailoring model because this is the one-off foundation."""
    result = llm.parse(
        agent="onboarding.import",
        model=preferences()["models"]["tailoring"],
        system=IMPORT_SYSTEM,
        user=f"<resume>\n{text[:40000]}\n</resume>",
        output_format=ProfileIn,
        max_tokens=12000,
        effort="low",
    )
    taken: set[str] = set()
    return {
        "identity": {"name": result.name, "headline": result.headline, "email": result.email,
                     "phone": result.phone, "location": result.location, "linkedin": result.linkedin,
                     "github": result.github, "portfolio": result.portfolio},
        "resume_line": result.work_rights_line,
        "summary": result.summary,
        "skills": {g.group.lower().replace(" & ", "_").replace(" ", "_"): g.items for g in result.skills if g.items},
        "experience": [{"id": _slug(e.company or e.title, taken), "title": e.title, "company": e.company,
                        "location": e.location, "start": e.start, "end": e.end or "present",
                        "project": e.project, "stack": e.stack, "facts": e.facts} for e in result.experience],
        "projects": [{"id": _slug(p.name, taken), "name": p.name, "dates": p.dates, "award": p.award,
                      "stack": p.stack, "facts": p.facts} for p in result.projects],
        "education": [e.model_dump() for e in result.education],
        "certifications": result.certifications,
        "awards": result.awards,
    }


def merge_import(current: dict, imported: dict) -> dict:
    """Imported resume sections replace the current ones; work rights and application answers are kept."""
    out = dict(current)
    for key in ("summary", "skills", "experience", "projects", "education", "certifications", "awards"):
        out[key] = imported[key]
    ident = dict(current.get("identity") or {})
    ident.update({k: v for k, v in imported["identity"].items() if v})
    out["identity"] = ident
    if imported.get("resume_line"):
        out.setdefault("work_rights", {})["resume_line"] = imported["resume_line"]
    app = dict(out.get("application") or {})
    parts = ident.get("name", "").split()
    if parts and not app.get("first_name"):
        app["first_name"], app["last_name"] = parts[0], " ".join(parts[1:])
    if ident.get("location") and not app.get("location"):
        app["location"], app["city"] = ident["location"], ident["location"].split(",")[0].strip()
    out["application"] = app
    return out


# ---------- description -> search settings ----------


class SearchSettings(BaseModel):
    target_roles: list[str] = Field(description="3-6 job titles to target")
    avoid_roles: str = Field(description="kinds of roles to score lower, one line, or empty")
    queries: list[str] = Field(description="4-8 short phrases to type into a job board search")
    title_keywords: list[str] = Field(description="words/phrases at least one of which should appear in a relevant job title")
    title_exclude_keywords: list[str] = Field(description="words that mark a title as irrelevant (e.g. seniority or function the user rules out)")
    preferred_locations: list[str] = Field(description="Australian cities the user prefers")
    allow_remote: bool
    intel_topic: str = Field(description="industry/technology focus for tracking newly funded startups, e.g. 'AI', 'climate tech', 'fintech'")
    intel_keywords: list[str] = Field(description="3-8 words that identify that focus in news")


PREFS_SYSTEM = """You turn a job seeker's description of what they want into job-search settings for Australia.
Use the profile summary for context on seniority and field. Be specific: prefer the titles employers actually
use in Australian job ads. title_keywords are matched against job titles, so include common variants and
abbreviations (e.g. "machine learning", "ml"). Only exclude what the user rules out or clearly can't do yet."""


def keyword_to_regex(kw: str) -> str:
    """Plain keywords become whole-word, case-insensitive patterns; lines that already look like regex stay."""
    kw = kw.strip()
    if re.search(r"[\\|()\[\]?*+^$]", kw):
        return kw
    return r"\b" + r"\s+".join(re.escape(w) for w in kw.split()) + r"\b"


def generate_search_settings(description: str, profile_summary: str) -> SearchSettings:
    return llm.parse(
        agent="onboarding.preferences",
        model=preferences()["models"]["default"],
        system=PREFS_SYSTEM,
        user=f"<profile_summary>\n{profile_summary}\n</profile_summary>\n\n<what_they_want>\n{description}\n</what_they_want>",
        output_format=SearchSettings,
        max_tokens=3000,
        effort="low",
    )


def apply_search_settings(prefs: dict, description: str, s: SearchSettings) -> dict:
    prefs = dict(prefs)
    search = dict(prefs.get("search") or {})
    search.update({
        "looking_for": description,
        "target_roles": s.target_roles,
        "avoid_roles": s.avoid_roles,
        "queries": s.queries,
        "title_include": [keyword_to_regex(k) for k in s.title_keywords],
        "title_exclude": sorted(set((search.get("title_exclude") or []) + [keyword_to_regex(k) for k in s.title_exclude_keywords])),
    })
    locs = dict(search.get("locations") or {})
    locs["preferred"] = s.preferred_locations
    locs["allow_remote_au"] = s.allow_remote
    search["locations"] = locs
    prefs["search"] = search
    intel = dict(prefs.get("intel") or {})
    intel["topic"] = s.intel_topic
    intel["topic_keywords"] = [keyword_to_regex(k) for k in s.intel_keywords]
    topic_q = " OR ".join(f'"{k}"' if " " in k else k for k in s.intel_keywords[:4])
    intel["google_news_queries"] = [
        f'(raises OR raised OR funding OR seed OR "Series A") ({topic_q}) startup Australia when:7d',
        f'Australian ({topic_q}) startup funding round when:14d',
    ]
    prefs["intel"] = intel
    return prefs
