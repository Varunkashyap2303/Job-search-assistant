"""Resume Tailor: one Opus call that selects and rewrites profile facts for a specific job.
It decides content only; names, titles, dates and layout always come from the profile."""

from pydantic import BaseModel, Field

from jobsearch import llm
from jobsearch.config import preferences
from jobsearch.tailor.facts import Catalogue, render_for_prompt

MAX_AD_CHARS = 20_000


class Bullet(BaseModel):
    text: str
    sources: list[str] = Field(description="fact ids this text is derived from, e.g. ['acme.2']; 'job' for claims about the employer")


class EntryChoice(BaseModel):
    ref: str = Field(description="entry ref from the catalogue, e.g. 'acme' or 'atlas'")
    bullets: list[Bullet] = Field(description="most important first; the renderer trims from the end if the page overflows")


class SkillGroup(BaseModel):
    label: str
    items: list[str]


class TailoredResume(BaseModel):
    headline: str = Field(description="line under the name, e.g. 'AI Engineer | LLM Applications | Agentic Systems'")
    summary: Bullet
    skills: list[SkillGroup]
    experience: list[EntryChoice]
    projects: list[EntryChoice] = Field(description="most relevant first")
    cover_letter: list[Bullet] = Field(description="3-4 paragraphs, no greeting or sign-off")
    tailoring_notes: str = Field(description="for the candidate: what was emphasised for this role and why, and any gaps to prepare for")


SYSTEM_TEMPLATE = """You tailor one candidate's resume and cover letter to a specific job ad.

The candidate's facts are listed below, each with an id in square brackets. They are the ONLY source of truth.

<fact_catalogue>
{catalogue}
</fact_catalogue>

<rules>
Truthfulness (the most important rule):
- Every bullet, the summary and every cover-letter paragraph cites the fact ids it is derived from. Use 'job' only for statements about the employer or role taken from the ad.
- Rephrase facts to use the ad's terminology where the fact genuinely supports it (e.g. "semantic search" for vector retrieval). Never add a technology, responsibility, scope, team size or outcome that the cited facts do not state.
- Copy every number exactly as it appears in the cited facts. Never invent or round metrics.
- Skills: list only skills from the SKILLS section or an entry's stack. You may regroup, relabel and reorder them so the job's must-haves come first. Omit irrelevant ones.

Selection and emphasis:
- Lead with what this role cares about most. Put the most relevant bullets first within each entry.
- Include every experience entry. Choose and order the projects by relevance; drop a project only if it adds nothing for this role.
- Bullets are one or two lines each: strong verb first, concrete, no filler. Bullets may combine two facts from the same entry.
- The resume must fit one A4 page: at most about 14 bullets in total across experience and projects.
- A project's award is already shown next to its name, so don't spend a bullet repeating it.
- Summary: 2-3 sentences positioning the candidate for this role.
- Headline: short positioning line for this role type, true to the candidate's actual experience.

Cover letter:
- 3-4 short paragraphs in plain Australian-English business tone, without greeting or sign-off.
- Paragraph 1: the role and a specific, genuine reason it fits (cite 'job' plus facts). Middle: 1-2 concrete examples. Last: brief close.
- No clichés ("I am writing to express", "passionate", "fast-paced"). No claims the facts don't support.

Style (write like a person, not a model):
- No AI vocabulary: delve, landscape, leverage, harness, navigate, journey, robust, seamless, cutting-edge,
  innovative, pivotal, transformative, spearheaded, passionate, synergy, streamline, testament.
- No em dashes. No "serves as", no "-ing" tails that add fake depth (", enabling...", ", ensuring...").
- Vary how bullets open and how long they are; don't start several with the same verb.
- Plain, specific claims beat adjectives.

Spelling: Australian English (optimise, organisation, containerised).
</rules>"""


def build_system_prompt(cat: Catalogue) -> str:
    return SYSTEM_TEMPLATE.format(catalogue=render_for_prompt(cat))


def build_user_prompt(job, guidance: str = "") -> str:
    a = job.assessment or {}

    def items(key):
        return "\n".join(f"- {x}" for x in a.get(key, [])) or "- (none extracted)"

    ad = job.description
    if len(ad) > MAX_AD_CHARS:
        ad = ad[:MAX_AD_CHARS] + "\n[ad truncated for length]"

    return f"""<job>
Title: {job.title}
Company: {job.company}
Location: {job.city}

Must-have skills:
{items("must_have_skills")}

Nice-to-have skills:
{items("nice_to_have_skills")}

Key responsibilities:
{items("key_responsibilities")}

ATS keywords (use the exact phrasing where a fact supports it):
{items("ats_keywords")}

Full ad:
{ad}
</job>

{guidance_block(guidance)}Tailor the resume and cover letter for this job."""


def guidance_block(guidance: str) -> str:
    if not guidance:
        return ""
    return ("<candidate_requests>\n"
            f"{guidance}\n"
            "Follow these where your facts support them; the truthfulness rules still apply.\n"
            "</candidate_requests>\n\n")


def tailor(job, cat: Catalogue, system_prompt: str, guidance: str = "") -> TailoredResume:
    cfg = preferences()["tailoring"]
    return llm.parse(
        agent="tailor",
        model=preferences()["models"]["tailoring"],
        system=system_prompt,
        user=build_user_prompt(job, guidance),
        output_format=TailoredResume,
        max_tokens=10000,  # budget guard reserves max_tokens at full price, so keep it realistic
        effort=cfg.get("effort", "medium"),
    )
