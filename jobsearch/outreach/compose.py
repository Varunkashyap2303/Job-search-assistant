"""Opus writes a contact plan and tailored messages for one startup, grounded in the candidate's
fact catalogue and the startup's profile."""

import re

from pydantic import BaseModel, Field

from jobsearch import llm, persona
from jobsearch.config import preferences
from jobsearch.tailor.facts import Catalogue, render_for_prompt


class ContactMessages(BaseModel):
    contact_id: str = Field(description="id from the contacts list, or 'role:<role>' for a role to find on LinkedIn")
    name: str = Field(description="the person's name exactly as given, or empty for a role to find")
    role: str
    priority: int = Field(description="1 = contact first; later numbers only if earlier contacts don't reply")
    why: str = Field(description="why this person, in one or two sentences")
    connection_note: str = Field(description="LinkedIn connection request note")
    linkedin_message: str = Field(description="message to send once connected")
    email_subject: str
    email_body: str
    follow_up: str = Field(description="short follow-up to send after about a week with no reply")


class OutreachPlanOut(BaseModel):
    strategy: str = Field(description="who to contact first and why, timing, and what to avoid with this company")
    contacts: list[ContactMessages]


SYSTEM_TEMPLATE = """You write cold outreach for one job seeker ({name}) contacting people at newly funded Australian startups.

<candidate_facts>
{catalogue}
</candidate_facts>

<rules>
Who:
- Only contact people from the provided contacts list, using their names exactly. Never invent a person.
- You may add up to {max_roles} role targets the candidate should look up on LinkedIn (e.g. Head of Engineering,
  CTO, Talent Lead) when no suitable named person exists. Leave name empty and use contact_id "role:<role>".
- At most {max_contacts} contacts in total. Rank them: priority 1 is who to contact first. At startups,
  a technical co-founder or engineering/AI leader usually beats the CEO; at scale-ups, the AI or engineering
  leader or the talent team. Don't plan to message several co-founders at once.

What:
- Every message must be specific to this company: reference their recent funding or product, and connect one
  concrete challenge from their AI roadmap to one concrete piece of the candidate's work.
- Use only the candidate facts above for claims about the candidate. Copy numbers exactly.
- One clear, low-effort ask (a 15-minute chat, or whether they're hiring {role_family}). No attachments,
  no "I'd love to pick your brain", no flattery, no clichés, no mention of visas.
- Vary the angle between contacts at the same company; never send near-identical text.
- For role targets with no name, open with "Hi {{first_name}}," so the candidate fills it in.
- Lengths: connection_note at most {note_chars} characters. linkedin_message at most 700 characters.
  email_subject at most 60 characters. email_body 90-150 words, signed "{first_name}". follow_up at most 350 characters.
- Plain Australian English, warm and direct, written as the candidate in first person.
</rules>"""


def build_system_prompt(cat: Catalogue) -> str:
    cfg = preferences()["outreach"]
    return SYSTEM_TEMPLATE.format(
        name=persona.name() or "the candidate",
        first_name=persona.first_name() or "me",
        role_family=persona.role_family(),
        catalogue=render_for_prompt(cat),
        max_roles=cfg.get("max_role_targets", 2),
        max_contacts=cfg.get("max_contacts_per_startup", 3),
        note_chars=cfg.get("connection_note_chars", 200),
    )


def build_user_prompt(startup, discovered: dict) -> str:
    p = startup.profile or {}
    contacts = "\n".join(
        f'- id=p{i}: {c["name"]}, {c["role"]} (found in: {c["source"]}; email: {c.get("email") or "none"})'
        for i, c in enumerate(discovered["people"])
    ) or "- (no named people found)"
    sources = "\n".join(f"- {s.get('title')} ({s.get('source')}, {s.get('date')})" for s in startup.sources[:6])
    return f"""<startup name="{startup.name}" city="{startup.hq_city}" sector="{startup.sector}">
One-liner: {p.get("one_liner", startup.one_line)}
Vision: {p.get("vision", "")}
Product: {p.get("product", "")}
Funding: {p.get("funding_summary", "")}
How AI fits in: {p.get("ai_implementation", "")}
Likely AI roles: {", ".join(p.get("likely_ai_roles", []))}
Hiring signals: {p.get("hiring_signals", "")}
Suggested angle for this candidate: {p.get("outreach_angle", "")}
Recent news:
{sources}
</startup>

<contacts>
{contacts}
</contacts>

Write the outreach plan."""


def compose(startup, discovered: dict, cat: Catalogue, system_prompt: str) -> OutreachPlanOut:
    return llm.parse(
        agent="outreach.compose",
        model=preferences()["models"]["outreach"],
        system=system_prompt,
        user=build_user_prompt(startup, discovered),
        output_format=OutreachPlanOut,
        max_tokens=5000,  # measured plans are ~3k tokens; the budget guard reserves this at full price
        effort=preferences()["outreach"].get("effort", "medium"),
    )


# Standalone numbers only: the digits in names like "Neo4j", "S3" or "p95" are not claims
_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")


def check_messages(c: ContactMessages, allowed_text: str, note_chars: int, known_names: set[str]) -> list[str]:
    """Deterministic checks. Messages are drafts you review, so problems are flagged, not auto-fixed."""
    warnings = []
    if len(c.connection_note) > note_chars:
        warnings.append(f"Connection note is {len(c.connection_note)} chars (LinkedIn limit {note_chars}); shorten it.")
    if len(c.linkedin_message) > 700:
        warnings.append(f"LinkedIn message is {len(c.linkedin_message)} chars; consider trimming.")
    allowed_nums = set(_NUM.findall(allowed_text))
    for field in ("connection_note", "linkedin_message", "email_subject", "email_body", "follow_up"):
        bad = [n for n in _NUM.findall(getattr(c, field)) if n not in allowed_nums]
        if bad:
            warnings.append(f"{field} has numbers not found in your profile or the startup's sources: {', '.join(bad)}")
    if c.name and c.name.lower() not in known_names:
        warnings.append(f"'{c.name}' wasn't in the discovered contacts; check this person exists.")
    return warnings
