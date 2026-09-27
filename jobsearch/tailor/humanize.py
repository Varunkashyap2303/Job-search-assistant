"""Humanizer pass for tailored resumes and cover letters, following the humanize-writing skill's
editing passes, adapted to job applications. Runs after the fact checker; every line it changes is
fact-checked again and reverted to the verified version if it drifted from the candidate's facts."""

import re

from pydantic import BaseModel, Field

from jobsearch import llm
from jobsearch.config import preferences
from jobsearch.tailor.facts import Catalogue
from jobsearch.tailor.tailor import Bullet, TailoredResume
from jobsearch.tailor.verify import _claims, _source_text, check_claims

# From the skill: tier 1 words are red flags on their own; tier 2 are tells in clusters.
TIER1 = ["delve", "landscape", "tapestry", "paradigm shift", "leverage", "leveraging", "leveraged", "harness",
         "navigate", "navigating", "realm", "embark", "journey", "myriad", "plethora", "multifaceted",
         "groundbreaking", "revolutionise", "revolutionize", "synergy", "resonate", "streamline", "streamlined"]
TIER2 = ["robust", "seamless", "seamlessly", "cutting-edge", "innovative", "comprehensive", "pivotal", "nuanced",
         "compelling", "transformative", "bolster", "underscore", "underscores", "evolving", "fostering", "imperative",
         "intricate", "overarching", "unprecedented", "spearheaded", "passionate", "dynamic", "meticulous"]
PHRASES = ["testament to", "serves as", "stands as", "plays a crucial role", "plays a pivotal role", "vital role",
           "i am writing to", "i'd like to be considered", "i am excited", "i'm excited", "thrilled to", "it's worth noting",
           "it is important to note", "in order to", "the ability to", "moreover", "furthermore", "additionally",
           "that said", "in conclusion", "not only", "commitment to", "showcasing", "highlighting", "underscoring"]


def find_tells(text: str) -> list[str]:
    low = text.lower()
    hits = [w for w in TIER1 + TIER2 if re.search(rf"\b{re.escape(w)}\b", low)]
    hits += [p for p in PHRASES if p in low]
    dashes = text.count("—")
    if dashes:
        hits.append(f"em dash x{dashes}")
    return hits


def resume_tells(r: TailoredResume) -> list[str]:
    texts = [r.summary.text, *(b.text for c in (*r.experience, *r.projects) for b in c.bullets),
             *(b.text for b in r.cover_letter)]
    return [hit for t in texts for hit in find_tells(t)]


class HumanizedItem(BaseModel):
    id: str
    text: str


class ChangeRow(BaseModel):
    pass_name: str = Field(description="Structure, Inflation, Vocabulary, Grammar, Rhythm/Style, Hedging/Filler, Transitions or Voice")
    what_changed: str = Field(description="one short phrase")
    example: str = Field(description="a short before -> after, or a quoted addition")


class HumanizeResult(BaseModel):
    items: list[HumanizedItem]
    changes: list[ChangeRow] = Field(description="at most 8 rows, only passes where something changed")


SYSTEM = """You are an editor who removes AI writing patterns from job applications: resume lines and a cover
letter. Rewrite each item so it reads like a capable engineer wrote it themselves, first try.

Hard constraints. Never break these:
- Change how things are said, never what is claimed. Keep every fact, number, name, technology and outcome
  exactly as the item's source facts support it. Add nothing: no new metrics, tools, scope, results, or
  judgements about impact. If a sentence can't be improved without changing a claim, leave it.
- Keep the job keywords listed with the items wherever they already appear, spelled the same.
- Return every item with its id, including ones you leave unchanged.
- Resume items stay resume items: no "I", start with a concrete verb (summary may be a short noun-phrase
  opener), one or two lines each. Keep roughly the same length; don't pad.
- Australian English spelling.

The passes (from the humanize-writing guide):
1. Vocabulary. Cut AI words: delve, landscape, tapestry, leverage, harness, navigate (metaphorical), realm,
   journey, myriad, plethora, multifaceted, groundbreaking, revolutionise, synergy, ecosystem (non-technical),
   resonate, streamline. Watch clusters of: robust, seamless, cutting-edge, innovative, comprehensive, pivotal,
   nuanced, compelling, transformative, bolster, underscore, fostering, intricate, unprecedented, spearheaded,
   passionate, dynamic. Often the fix is restructuring, not a synonym.
2. Inflation. Delete significance and promotional puffery (testament to, pivotal/crucial role, showcasing,
   commitment to, excited/thrilled to). Replace with the specific fact or just stop.
3. Grammar tics. "serves as / stands as / functions as" -> "is"; "boasts / features" -> "has". Cut superficial
   -ing tails (", enabling...", ", ensuring...", ", highlighting...") or turn them into a concrete clause.
   Cut rule-of-three padding and synonym cycling; one clear term, repeated, is fine.
4. Rhythm and style. Resume bullets shouldn't all open with the same verb or share one shape; vary them.
   Break up any sentence over about 35 words. In the cover letter, mix in a few short sentences (under 10
   words) among longer ones; a metronome of medium sentences is the biggest tell.
   Remove em dashes: use commas, full stops, or restructure. No bold, no emojis.
5. Hedging and filler. "in order to" -> "to", "the ability to" -> "can", drop "it's worth noting". Say it plainly.
6. Transitions. Drop Moreover / Furthermore / Additionally / That said; use a plain connector or none.
7. Voice (cover letter only). Plain first person, direct and warm, like a confident engineer writing to a
   peer. Don't open with "I am writing to...", "I'd like to be considered for..." or "I'm excited to apply":
   open with the specific reason this role fits. No generic flourish at the end: finish on the concrete ask.
   At most one plain-spoken aside if it fits naturally. No slang, no jokes.

Don't dumb it down or make it chatty. If an item already reads naturally, return it unchanged.
Then list the changes: at most 8 rows, only for passes where you changed something."""


def _items(r: TailoredResume) -> list[tuple[str, Bullet, str | None]]:
    return list(_claims(r))


def humanize(r: TailoredResume, cat: Catalogue, job) -> tuple[TailoredResume, dict]:
    """Returns the humanized resume and a report: {changes, reverted, tells_before, tells_after}."""
    cfg = preferences()
    items = _items(r)
    keywords = list(dict.fromkeys((job.assessment or {}).get("ats_keywords", []) +
                                  (job.assessment or {}).get("must_have_skills", [])))
    blocks = []
    for cid, b, owner in items:
        heading = cat.entries[owner].heading if owner in cat.entries else ("Cover letter" if cid.startswith("cover") else "Summary")
        facts = _source_text([s for s in b.sources if s != "job"], cat, job)
        if "job" in b.sources:  # the re-check sees the full ad; the editor only needs to leave employer claims alone
            facts += "\n[job] statements about the employer come from their ad: keep their substance unchanged"
        blocks.append(f'<item id="{cid}" section="{heading}">\n{b.text}\n'
                      f"<source_facts>\n{facts}\n</source_facts>\n</item>")
    tells_before = resume_tells(r)
    result = llm.parse(
        agent="tailor.humanizer",
        model=cfg["models"].get("humanizer", "claude-sonnet-5"),
        system=SYSTEM,
        user=(f"Role: {job.title} at {job.company}\n"
              f"Job keywords to keep: {', '.join(keywords) or '(none)'}\n\n" + "\n\n".join(blocks)),
        output_format=HumanizeResult,
        max_tokens=5000,
        effort=cfg.get("tailoring", {}).get("humanizer_effort", "medium"),
    )
    rewritten = {i.id: i.text.strip() for i in result.items if i.text.strip()}

    # Only lines that actually changed need re-checking
    changed = [(cid, Bullet(text=rewritten[cid], sources=b.sources), owner)
               for cid, b, owner in items if cid in rewritten and rewritten[cid] != b.text]
    failures = check_claims(changed, cat, job) if changed else {}

    out = r.model_copy(deep=True)
    reverted = []
    for cid, new, _ in changed:
        if cid in failures:
            reverted.append({"where": cid, "reason": failures[cid], "attempt": new.text})
            continue
        _set_text(out, cid, new.text)
    report = {
        "model": cfg["models"].get("humanizer", "claude-sonnet-5"),
        "changes": [c.model_dump() for c in result.changes[:8]],
        "lines_changed": len(changed) - len(reverted),
        "reverted": reverted,
        "tells_before": tells_before,
        "tells_after": resume_tells(out),
    }
    return out, report


def _set_text(r: TailoredResume, cid: str, text: str) -> None:
    if cid == "summary":
        r.summary.text = text
        return
    parts = cid.split("/")
    if parts[0] == "cover_letter":
        r.cover_letter[int(parts[1])].text = text
        return
    attr, ref, idx = parts
    for c in getattr(r, attr):
        if c.ref == ref:
            c.bullets[int(idx)].text = text
