"""Drafts answers for every question on an application form. Standard fields come straight from the
profile; demographic questions default to 'decline'; the rest is one Sonnet call grounded in the profile,
the job and the tailored cover letter. Anything unknown is marked needs_you instead of guessed."""

import re

import yaml
from pydantic import BaseModel, Field

from jobsearch import llm, persona
from jobsearch.config import preferences, profile, strip_todos
from jobsearch.apply.schema import FormField

DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (say|answer|disclose)|rather not|choose not", re.I)
CHOICE_TYPES = {"select", "radio", "boolean", "multiselect", "checkbox"}
_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")


def _pick_option(options: list[str], pattern: re.Pattern) -> str:
    return next((o for o in options if pattern.search(o)), "")


PERSONAL = re.compile(r"know anyone|referr|relative|related to|family member|spouse|partner who|conflict of interest|"
                      r"previously (worked|applied)|worked (here|for us) before", re.I)


def standard_answer(f: FormField, prof: dict, files: dict) -> tuple[str, str] | None:
    """Return (answer, source) for fields answerable straight from the profile, else None."""
    ident, app = prof.get("identity", {}), prof.get("application", {})
    key, label = f.key.lower(), f.label.lower()
    if f.type == "file":
        if "cover" in key or "cover" in label:
            return files.get("cover_letter", ""), "file"
        if "resume" in key or "cv" in label or "resume" in label:
            return files.get("resume", ""), "file"
        return None
    if f.section == "eeo":
        if app.get("eeo_default", "decline") == "decline":
            if f.options:
                opt = _pick_option(f.options, DECLINE)
                return (opt, "default") if opt else None
            return ("", "default") if not f.required else None
        return None
    table = [
        (key == "first_name" or label.startswith("first name"), app.get("first_name")),
        (key == "last_name" or label.startswith("last name"), app.get("last_name")),
        (key == "preferred_name" or "preferred first name" in label, app.get("first_name")),
        (key in ("name", "_systemfield_name") or label in ("name", "full name"), ident.get("name")),
        (f.type == "email" or key in ("email", "_systemfield_email"), ident.get("email")),
        (f.type == "phone" or key == "phone" or label.startswith("phone"), app.get("phone_international") or ident.get("phone")),
        (f.type == "location" or key in ("location", "candidate-location") or label.startswith(("current location", "location (city)")),
         app.get("city") if f.type == "location" else app.get("location")),
        ("linkedin" in key or "linkedin" in label, ident.get("linkedin")),
        ("github" in key or "github" in label, ident.get("github")),
        ("portfolio" in key or "portfolio" in label or "website" in label, ident.get("portfolio")),
        (key == "org" or "current company" in label or "current employer" in label, app.get("current_company")),
        ("pronoun" in label, app.get("pronouns")),
        ("hear about" in label or "how did you hear" in label or "source" == key, app.get("how_did_you_hear")),
    ]
    for matched, value in table:
        if matched:
            if value and f.options and f.type in CHOICE_TYPES:
                opt = _pick_option(f.options, re.compile(re.escape(str(value)), re.I)) or \
                      _pick_option(f.options, re.compile(r"career|website|company site", re.I))
                return (opt, "profile") if opt else None
            if value:
                return str(value), "profile"
            if not f.required:
                return "", "profile"  # optional and we have nothing: leave blank
            return None
    return None


class DraftedAnswer(BaseModel):
    key: str
    answer: str = Field(description="for choice questions, exactly one of the options; for multiselect, options joined with ' | '")
    needs_you: bool = Field(description="true if the facts don't answer this and the candidate must decide")
    basis: str = Field(description="which fact or default the answer rests on, or what's missing")


class DraftedAnswers(BaseModel):
    answers: list[DraftedAnswer]


SYSTEM = """You fill in job application questions for one candidate, truthfully.

Rules:
- Answer only from the candidate facts and application defaults given. Never invent experience, numbers,
  referrals, salary figures, dates or availability. If the facts don't settle it, set needs_you = true and
  leave answer empty (or give your best suggestion in basis).
- Choice questions: answer with exactly one of the listed options, copied verbatim (multiselect: options joined
  with " | "). If none fits truthfully, needs_you = true.
- Work rights: answer strictly from the candidate's work-rights statement (given with the facts). Distinguish
  sponsorship needed now from sponsorship needed in the future. If a question is ambiguous about now vs
  future, set needs_you = true and explain in basis.
- Free-text answers: specific, plain first person, Australian English, 60-150 words unless the question asks
  otherwise. Describe only what the facts state. If a question asks for an experience the facts don't contain
  (e.g. how you personally use a tool, a conflict you resolved), set needs_you = true and put a suggested
  outline built from real facts in basis, rather than inventing a story. Draw on the tailored cover letter
  and facts; no clichés, no AI vocabulary (leverage, robust, passionate, cutting-edge, delve), no em dashes.
- Questions about knowing someone at the company, relatives working there, or conflicts of interest: answer
  "No" / "None" only if the defaults say so; otherwise needs_you = true."""


def draft(fields: list[FormField], job, cover_letter: list[str]) -> dict[str, DraftedAnswer]:
    if not fields:
        return {}
    prof = profile()
    known = strip_todos(prof)
    missing = [k for k, v in (prof.get("application") or {}).items() if v == "TODO"]
    qs = "\n".join(
        f'- key={f.key} | type={f.type} | required={f.required} | question: {f.label}'
        + (f" | options: {' ;; '.join(f.options)}" if f.options else "")
        for f in fields
    )
    user = f"""<candidate_facts>
{yaml.safe_dump({k: known.get(k) for k in ("identity", "work_rights", "summary", "skills", "experience", "projects", "education", "application")}, sort_keys=False, allow_unicode=True)}
Unknown application defaults (never guess these): {", ".join(missing) or "none"}
Work-rights statement: {persona.work_rights_text()}
</candidate_facts>

<job title="{job.title}" company="{job.company}">
{(job.description or "")[:6000]}
</job>

<tailored_cover_letter>
{chr(10).join(cover_letter)}
</tailored_cover_letter>

<questions>
{qs}
</questions>

Answer every question by key."""
    result = llm.parse(
        agent="apply.answers",
        model=preferences()["models"]["default"],
        system=SYSTEM,
        user=user,
        output_format=DraftedAnswers,
        max_tokens=5000,
        effort="low",
    )
    return {a.key: a for a in result.answers}


def fill_answers(fields: list[FormField], job, files: dict, cover_letter: list[str], facts_text: str) -> list[FormField]:
    prof = profile()
    rest = []
    for f in fields:
        std = standard_answer(f, prof, files)
        if std is not None:
            f.answer, f.source = std
        else:
            rest.append(f)
    personal = [f for f in rest if PERSONAL.search(f.label)]
    for f in personal:  # only you know who you know
        f.needs_you, f.source, f.note = True, "you", "only you can answer this"
    rest = [f for f in rest if f not in personal]
    drafted = draft(rest, job, cover_letter)
    known = strip_todos(prof)
    record = f"{facts_text} {yaml.safe_dump(known.get('work_rights'))} {yaml.safe_dump(known.get('application'))}"
    allowed_nums = set(_NUM.findall(record + " " + (job.description or "")))
    for f in rest:
        d = drafted.get(f.key)
        if d is None:
            f.needs_you, f.note = True, "no draft returned"
            continue
        f.answer, f.source, f.note = d.answer, "drafted", d.basis
        f.needs_you = d.needs_you
        if f.type in CHOICE_TYPES and f.options and d.answer:
            chosen = [a.strip() for a in d.answer.split("|")] if f.type in ("multiselect", "checkbox") else [d.answer.strip()]
            exact = [next((o for o in f.options if o.lower() == c.lower()), None) for c in chosen]
            if None in exact:
                f.needs_you, f.note = True, f"'{d.answer}' isn't one of the options"
            else:
                f.answer = " | ".join(exact) if len(exact) > 1 else exact[0]
        if f.type in ("text", "textarea") and not f.needs_you:
            bad = [n for n in _NUM.findall(f.answer) if n not in allowed_nums]
            if bad:
                f.needs_you, f.note = True, f"check these numbers, not found in your profile or the ad: {', '.join(bad)}"
        if f.needs_you and not f.required and not f.answer:
            f.needs_you = False  # optional and unknown: leave it blank
    _ground_free_text([f for f in rest if f.type in ("text", "textarea") and not f.needs_you and len(f.answer) > 40],
                      record, job)
    return fields


def _ground_free_text(fields: list[FormField], record: str, job) -> None:
    """Fact-check drafted prose against the whole profile. Unsupported answers go to you, draft kept."""
    if not fields:
        return
    from jobsearch.tailor.verify import VERIFIER_SYSTEM, VerificationResult

    claims = "\n\n".join(
        f'<claim id="{f.key}">\n{f.answer}\n<sources>\n[candidate record] {record}\n'
        f"[job] {job.title} at {job.company}. {(job.description or '')[:3000]}\n</sources>\n</claim>"
        for f in fields
    )
    result = llm.parse(agent="apply.verify", model=preferences()["models"].get("verification", "claude-sonnet-5"),
                       system=VERIFIER_SYSTEM, user=claims, output_format=VerificationResult,
                       max_tokens=2000, effort="low")
    verdicts = {v.claim_id: v for v in result.verdicts}
    for f in fields:
        v = verdicts.get(f.key)
        if v is None or not v.supported:
            f.needs_you = True
            f.note = f"fact check: {v.issue if v else 'no verdict'}. The draft is a suggestion; edit it before using."
