"""Checks every tailored claim against the facts it cites, then repairs what fails.
Deterministic checks run first (free); a Sonnet pass then judges each claim's wording.
Repairs never add content: a failed bullet falls back to its source fact verbatim."""

import re
from typing import Literal

from pydantic import BaseModel

from jobsearch import llm
from jobsearch.config import preferences
from jobsearch.tailor.facts import Catalogue, words
from jobsearch.tailor.tailor import Bullet, EntryChoice, TailoredResume

# Standalone numbers only: the digits in names like "Neo4j", "S3" or "p95" are not claims
_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")


class ClaimVerdict(BaseModel):
    claim_id: str
    supported: bool
    issue: str


class VerificationResult(BaseModel):
    verdicts: list[ClaimVerdict]


VERIFIER_SYSTEM = """You fact-check a tailored resume against the candidate's own records.

For each claim you get the claim text and the source facts it cites. A claim is supported only if every
factual assertion in it (technologies, actions, scope, team size, ownership, outcomes, numbers) is stated in
or directly implied by the cited sources. Rewording, reordering, summarising and swapping in a near-synonym
the source supports (e.g. "semantic search" for vector retrieval) are fine. Anything added or inflated is not:
extra technologies, bigger scope, "led" when the source says "built", invented outcomes, or a different number.

Return a verdict for every claim_id. For unsupported claims, name the exact unsupported words in `issue`;
otherwise leave `issue` empty."""


def _source_text(sources: list[str], cat: Catalogue, job) -> str:
    facts = cat.facts
    parts = [f"[{s}] {facts[s]}" for s in sources if s in facts]
    # The stack of each cited job/project is part of its record (e.g. a job's databases and frameworks)
    for ref in dict.fromkeys(s.split(".")[0] for s in sources if s in facts and s != "profile.summary"):
        entry = cat.entries.get(ref)
        if entry and entry.stack:
            parts.append(f"[{ref} stack] {', '.join(entry.stack)}")
    if "job" in sources:
        parts.append(f"[job] {job.title} at {job.company}. {job.description[:6000]}")
    return "\n".join(parts)


def _numbers_ok(text: str, source_text: str) -> bool:
    src = set(_NUM.findall(source_text))
    return all(n in src for n in _NUM.findall(text))


class Report:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, where: str, action: Literal["replaced", "dropped", "restored", "kept"], reason: str, original: str = ""):
        self.items.append({"where": where, "action": action, "reason": reason, "original": original})


def _structural_fixes(r: TailoredResume, cat: Catalogue, report: Report) -> None:
    vocab = cat.vocabulary()
    for group in r.skills:
        kept = []
        for item in group.items:
            if all(w in vocab for w in words(item)):
                kept.append(item)
            else:
                report.add(f"skills/{group.label}", "dropped", "skill not in profile", item)
        group.items = kept
    r.skills = [g for g in r.skills if g.items]

    for attr, kind in (("experience", "experience"), ("projects", "project")):
        choices = []
        for choice in getattr(r, attr):
            entry = cat.entries.get(choice.ref)
            if entry is None or entry.kind != kind:
                report.add(f"{attr}/{choice.ref}", "dropped", "unknown entry ref")
                continue
            choices.append(choice)
        setattr(r, attr, choices)

    # Every real job must appear, even if the model skipped it.
    present = {c.ref for c in r.experience}
    for ref, entry in cat.entries.items():
        if entry.kind == "experience" and ref not in present:
            r.experience.append(EntryChoice(ref=ref, bullets=[
                Bullet(text=t, sources=[fid]) for fid, t in list(entry.facts.items())[:3]
            ]))
            report.add(f"experience/{ref}", "restored", "tailor omitted a real job; restored from profile")


def _claims(r: TailoredResume):
    """Yield (claim_id, bullet, owning entry ref or None)."""
    yield "summary", r.summary, None
    for attr in ("experience", "projects"):
        for c in getattr(r, attr):
            for i, b in enumerate(c.bullets):
                yield f"{attr}/{c.ref}/{i}", b, c.ref
    for i, b in enumerate(r.cover_letter):
        yield f"cover_letter/{i}", b, None


def _deterministic_issue(bullet: Bullet, owner: str | None, cat: Catalogue, job) -> str:
    facts = cat.facts
    unknown = [s for s in bullet.sources if s != "job" and s not in facts]
    if unknown:
        return f"cites unknown facts {unknown}"
    if not bullet.sources:
        return "cites no sources"
    if owner and any(s != "job" and not s.startswith(owner + ".") for s in bullet.sources):
        return "cites facts from a different job/project"
    if not _numbers_ok(bullet.text, _source_text(bullet.sources, cat, job)):
        return "contains a number not in its sources"
    return ""


def _fallback(bullet: Bullet, owner: str | None, cat: Catalogue) -> Bullet | None:
    for s in bullet.sources:
        if s in cat.facts and s != "profile.summary" and (owner is None or s.startswith(owner + ".")):
            return Bullet(text=cat.facts[s], sources=[s])
    return None


def verify_and_fix(r: TailoredResume, cat: Catalogue, job) -> tuple[TailoredResume, list[dict]]:
    r = r.model_copy(deep=True)
    report = Report()
    _structural_fixes(r, cat, report)
    failures = check_claims(list(_claims(r)), cat, job)
    _apply_fixes(r, cat, failures, report)
    return r, report.items


def check_claims(claims: list[tuple[str, Bullet, str | None]], cat: Catalogue, job) -> dict[str, str]:
    """Deterministic checks first, then one Sonnet call for the rest. Returns {claim_id: issue} for failures."""
    failures: dict[str, str] = {}
    to_llm = []
    for cid, bullet, owner in claims:
        issue = _deterministic_issue(bullet, owner, cat, job)
        if issue:
            failures[cid] = issue
        else:
            to_llm.append((cid, bullet))

    if to_llm:
        claims_text = "\n\n".join(
            f"<claim id=\"{cid}\">\n{b.text}\n<sources>\n{_source_text(b.sources, cat, job)}\n</sources>\n</claim>"
            for cid, b in to_llm
        )
        result = llm.parse(
            agent="tailor.verifier",
            model=preferences()["models"].get("verification", "claude-sonnet-5"),
            system=VERIFIER_SYSTEM,
            user=claims_text,
            output_format=VerificationResult,
            max_tokens=3000,  # verdicts are short; the budget guard reserves this at full price
            effort="low",
        )
        judged = {v.claim_id: v for v in result.verdicts}
        for cid, _ in to_llm:
            v = judged.get(cid)
            if v is None:
                failures[cid] = "verifier returned no verdict"
            elif not v.supported:
                failures[cid] = v.issue or "unsupported"
    return failures


def _apply_fixes(r: TailoredResume, cat: Catalogue, failures: dict[str, str], report: Report) -> None:
    if "summary" in failures:
        report.add("summary", "replaced", failures["summary"], r.summary.text)
        r.summary = Bullet(text=cat.summary, sources=["profile.summary"])

    for attr in ("experience", "projects"):
        for c in getattr(r, attr):
            fixed = []
            for i, b in enumerate(c.bullets):
                cid = f"{attr}/{c.ref}/{i}"
                if cid not in failures:
                    fixed.append(b)
                    continue
                fb = _fallback(b, c.ref, cat)
                if fb and all(fb.text != x.text for x in fixed):
                    fixed.append(fb)
                    report.add(cid, "replaced", failures[cid], b.text)
                else:
                    report.add(cid, "dropped", failures[cid], b.text)
            c.bullets = fixed

    kept = []
    for i, b in enumerate(r.cover_letter):
        cid = f"cover_letter/{i}"
        if cid in failures:
            report.add(cid, "dropped", failures[cid], b.text)
        else:
            kept.append(b)
    r.cover_letter = kept
