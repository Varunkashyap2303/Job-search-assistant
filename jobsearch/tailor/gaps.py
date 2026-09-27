"""Close the gaps: turn the gaps found on a tailored resume into profile additions you confirm,
then regenerate the resume with them. Everything added is your own statement of fact."""

import re
from dataclasses import dataclass

from jobsearch.config import add_to_profile
from jobsearch.tailor.facts import Catalogue

# Scorer notes about the ad itself, not about the candidate; nothing to address
_META = re.compile(r"snippet|description is|not specified|unclear|work rights|citizenship|years of experience", re.I)


@dataclass
class Target:
    label: str
    kind: str      # skill | fact
    target: str    # skills group or entry ref


def addressable_gaps(coverage: list[dict], assessment: dict) -> list[str]:
    items = [c["keyword"] for c in coverage if c["status"] == "gap"]
    items += [g for g in assessment.get("gaps", []) if not _META.search(g)]
    seen, out = set(), []
    for g in items:
        key = g.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(g)
    return out


def unused_keywords(coverage: list[dict]) -> list[str]:
    return [c["keyword"] for c in coverage if c["status"] == "in_profile_not_used"]


def targets(cat: Catalogue) -> list[Target]:
    out = [Target(f"Skill · {group.replace('_', ' ')}", "skill", group) for group in cat.skills]
    out.append(Target("Skill · other", "skill", "other"))
    out += [Target(f"{'Experience' if e.kind == 'experience' else 'Project'} · {e.heading}", "fact", ref)
            for ref, e in cat.entries.items()]
    return out


def apply_additions(choices: list[tuple[Target, str, str]]) -> list[str]:
    """choices: (target, text, gap). A skill with no text uses the gap wording itself.
    Returns human-readable descriptions of what was added."""
    added = []
    for target, text, gap in choices:
        text = text.strip() or (gap if target.kind == "skill" else "")
        if not text:
            continue
        if add_to_profile(target.kind, target.target, text):
            added.append(f"{target.label}: {text}")
    return added


def build_guidance(must_mention: list[str], added: list[str]) -> str:
    lines = []
    if must_mention:
        lines.append("Make sure the resume clearly shows: " + ", ".join(must_mention) + ".")
    if added:
        lines.append("The candidate just added these to their profile to address this job's gaps; "
                     "use them where they fit:")
        lines += [f"- {a}" for a in added]
    return "\n".join(lines)
