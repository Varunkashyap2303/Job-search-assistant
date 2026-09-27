"""Opus writes the in-depth startup profile: vision, product, how AI fits their business, hiring
signals, and fit for the candidate. Grounded in the article, website and job board text we fetched."""

from typing import Literal

import yaml
from pydantic import BaseModel, Field

from jobsearch import llm, persona
from jobsearch.config import preferences, profile, strip_todos


class Person(BaseModel):
    name: str
    role: str
    source: str = Field(description="where this person is named: 'article', 'website' or 'job board'")


class StartupProfile(BaseModel):
    website_consistent: bool = Field(description="false if the website text seems to be a different company")
    one_liner: str
    vision: str = Field(description="what they are ultimately trying to change, 2-3 sentences")
    product: str = Field(description="what they sell today and to whom")
    customers_and_market: str
    business_model: str
    funding_summary: str = Field(description="round, amount, date, lead and other investors, what the money is for")
    ai_centricity: Literal["core", "significant", "peripheral"]
    ai_implementation: str = Field(description=(
        "detailed analysis in 3-5 short paragraphs: where AI sits in the product, the kinds of models and "
        "techniques involved (LLM apps, agents, RAG, speech, vision, classical ML...), their data advantage, "
        "build vs buy, and the hard technical problems they will need to solve as they scale"))
    ai_evidence: list[str] = Field(description="concrete signals from the sources that support the analysis")
    likely_ai_roles: list[str] = Field(description="AI/engineering roles they are likely to hire for after this round")
    hiring_signals: str = Field(description="open roles and growth signals seen in the sources, or 'none found'")
    people: list[Person] = Field(description="founders and leaders named in the sources only; never guess")
    relevance_score: int = Field(description="0-100 fit as an employer for the candidate")
    relevance_reasons: str
    outreach_angle: str = Field(description="which of the candidate's projects or experience to lead with, and why")
    unknowns: list[str] = Field(description="important things the sources don't say")


SYSTEM_TEMPLATE = """You are a startup analyst helping one job seeker decide which newly funded Australian
startups to approach and how. The research focus is {topic}.

<candidate>
{candidate}
</candidate>

<instructions>
You get what we know about one startup: funding news, text from its website and careers pages, and roles on
its job board. Write a detailed profile.
- Separate evidence from analysis. Facts (funding, product, people, roles) must come from the sources.
  The ai_implementation section is your expert analysis of how {topic} is or will be built into their
  product; reason from the product and evidence, and say where you are inferring.
- People: list only people named in the sources, with their role. Never invent names.
- If the website text looks like a different company, set website_consistent to false and ignore it.
- relevance_score: how good an employer this is for the candidate given their skills and experience above,
  target roles ({targets}), location (based in {home}; preferred {preferred}), work rights ({rights} Very
  early-stage startups rarely sponsor visas), and whether the company is likely to hire for those roles soon.
- Australian English. Be specific and concise; no hype.
</instructions>"""


def build_system_prompt() -> str:
    p = strip_todos(profile())
    candidate = {
        "summary": p.get("summary"),
        "skills": p.get("skills"),
        "experience": [{k: e.get(k) for k in ("title", "company", "project", "facts")} for e in p.get("experience", []) if e.get("facts")],
        "projects": [{k: e.get(k) for k in ("name", "facts")} for e in p.get("projects", [])],
    }
    prefs = preferences()
    return SYSTEM_TEMPLATE.format(
        candidate=yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True),
        topic=prefs.get("intel", {}).get("topic", "AI"),
        targets=", ".join(persona.target_roles()) or "not specified",
        home=persona.home_location() or "Australia",
        preferred=", ".join(prefs["search"]["locations"].get("preferred", [])) or "anywhere",
        rights=persona.work_rights_text(),
    )


def build_user_prompt(st) -> str:
    events = "\n".join(
        f"- {e.get('announced_date') or 'date unknown'}: {e.get('round') or 'round n/a'} {e.get('amount_text') or ''}"
        f" | investors: {', '.join(e.get('investors', [])) or 'n/a'} | {e.get('one_line', '')}"
        for e in st.events
    )
    sources = "\n".join(f"- {s.get('title')} ({s.get('source')}, {s.get('date')})" for s in st.sources[:8])
    careers = st.careers or {}
    roles = "\n".join(f"- {r}" for r in careers.get("roles", [])[:30]) or "- none found"
    return f"""<startup name="{st.name}" city="{st.hq_city or 'unknown'}" sector="{st.sector}">
<funding_events>
{events}
</funding_events>
<news_headlines>
{sources}
</news_headlines>
<article_excerpt>
{st.article_excerpt or '(none)'}
</article_excerpt>
<website url="{st.website or 'not found'}">
{st.site_excerpt or '(not found)'}
</website>
<job_board ats="{careers.get('ats', 'none')}" open_roles="{careers.get('open_roles', 0)}">
{roles}
</job_board>
</startup>

Write the profile."""


def write_profile(st, system_prompt: str) -> StartupProfile:
    result = llm.parse(
        agent="intel.profile",
        model=preferences()["models"]["research"],
        system=system_prompt,
        user=build_user_prompt(st),
        output_format=StartupProfile,
        max_tokens=4500,
        effort=preferences()["intel"].get("effort", "medium"),
    )
    result.relevance_score = max(0, min(100, result.relevance_score))
    return result
