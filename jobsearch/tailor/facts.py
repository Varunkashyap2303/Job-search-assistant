"""Turns profile.yaml into a catalogue of citable facts. Tailored bullets must cite fact ids;
the verifier checks every claim against the facts it cites."""

import re
from dataclasses import dataclass, field

from jobsearch.config import profile, strip_todos

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Entry:
    ref: str
    kind: str                   # experience | project
    heading: str                # "Data Analyst | Acme" or project name
    dates: str                  # display form, e.g. "Jul 2026 – Present"
    location: str
    subtitle: str               # italic line: project name and/or stack
    badge: str                  # e.g. award shown next to a project name
    facts: dict[str, str] = field(default_factory=dict)   # fact id -> text
    stack: list[str] = field(default_factory=list)


@dataclass
class Catalogue:
    identity: dict
    work_rights: dict
    summary: str
    skills: dict[str, list[str]]
    entries: dict[str, Entry]
    education: list[dict]

    @property
    def facts(self) -> dict[str, str]:
        out = {"profile.summary": self.summary}
        for e in self.entries.values():
            out.update(e.facts)
        return out

    def vocabulary(self) -> set[str]:
        """Every word appearing in a listed skill or stack, for checking tailored skill lists."""
        items = [s for group in self.skills.values() for s in group]
        items += [s for e in self.entries.values() for s in e.stack]
        return {w for item in items for w in words(item)}


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9.+#-]*", text.lower()) if len(w) > 1]


def fmt_month(value) -> str:
    value = str(value).strip()
    if value.lower() == "present":
        return "Present"
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}" if m else value


def fmt_range(text: str) -> str:
    parts = [p.strip() for p in re.split(r"\s+to\s+", str(text))]
    return " – ".join(fmt_month(p) for p in parts)


def load_catalogue() -> Catalogue:
    p = strip_todos(profile())
    entries: dict[str, Entry] = {}
    for x in p.get("experience", []):
        facts = x.get("facts") or []
        if not facts or "start" not in x:
            continue  # incomplete entries (e.g. dates still TODO) can't go on a resume
        ref = x["id"]
        entries[ref] = Entry(
            ref=ref,
            kind="experience",
            heading=f"{x['title']}  |  {x['company']}",
            dates=f"{fmt_month(x['start'])} – {fmt_month(x.get('end', 'present'))}",
            location=x.get("location", ""),
            subtitle=" — ".join(filter(None, [x.get("project", ""), ", ".join(x.get("stack", []))])),
            badge="",
            facts={f"{ref}.{i + 1}": f for i, f in enumerate(facts)},
            stack=x.get("stack", []),
        )
    for x in p.get("projects", []):
        facts = x.get("facts") or []
        if not facts:
            continue
        ref = x["id"]
        entries[ref] = Entry(
            ref=ref,
            kind="project",
            heading=x["name"].replace(" - ", " — ", 1),
            dates=fmt_range(x.get("dates", "")),
            location="",
            subtitle=", ".join(x.get("stack", [])),
            badge=x.get("award", ""),
            facts={f"{ref}.{i + 1}": f for i, f in enumerate(facts)},
            stack=x.get("stack", []),
        )
    return Catalogue(
        identity=p.get("identity", {}),
        work_rights=p.get("work_rights", {}),
        summary=" ".join(str(p.get("summary", "")).split()),
        skills=p.get("skills", {}),
        entries=entries,
        education=p.get("education", []),
    )


def render_for_prompt(cat: Catalogue) -> str:
    lines = ["[profile.summary] " + cat.summary, "", "SKILLS (the only skills that may be listed):"]
    for group, items in cat.skills.items():
        lines.append(f"- {group}: {', '.join(items)}")
    for e in cat.entries.values():
        lines += ["", f"{e.kind.upper()} ref={e.ref}: {e.heading} ({e.dates})"]
        if e.subtitle:
            lines.append(f"  context/stack: {e.subtitle}")
        if e.badge:
            lines.append(f"  award: {e.badge}")
        lines += [f"  [{fid}] {text}" for fid, text in e.facts.items()]
    lines += ["", "EDUCATION:"]
    for ed in cat.education:
        lines.append(f"- {ed.get('degree')}, {ed.get('institution')} ({ed.get('dates')})"
                     + (f"; {ed['notes']}" if ed.get("notes") else ""))
    return "\n".join(lines)
