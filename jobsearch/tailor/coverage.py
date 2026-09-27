"""How well a tailored resume covers the job's keywords, split into what's covered, what the
candidate has but the resume doesn't show, and genuine gaps (not in the profile at all)."""

import re

from jobsearch.tailor.facts import Catalogue, words
from jobsearch.tailor.tailor import TailoredResume


def resume_text(r: TailoredResume) -> str:
    parts = [r.headline, r.summary.text]
    parts += [f"{g.label} {' '.join(g.items)}" for g in r.skills]
    parts += [b.text for c in (*r.experience, *r.projects) for b in c.bullets]
    return " ".join(parts)


def _present(keyword: str, text: str, text_words: set[str]) -> bool:
    # "Evaluation/quality measurement" is covered if either alternative is
    for alt in re.split(r"\s*/\s*", keyword.lower().strip()):
        if alt and (alt in text.lower() or (bool(words(alt)) and all(w in text_words for w in words(alt)))):
            return True
    return False


def keyword_coverage(r: TailoredResume, cat: Catalogue, assessment: dict) -> list[dict]:
    keywords = list(dict.fromkeys(assessment.get("must_have_skills", []) + assessment.get("ats_keywords", [])))
    text = resume_text(r)
    text_words = set(words(text))
    profile_text = " ".join([cat.summary, *cat.facts.values(),
                             *(s for g in cat.skills.values() for s in g),
                             *(s for e in cat.entries.values() for s in e.stack)])
    profile_words = set(words(profile_text))
    out = []
    for kw in keywords:
        if _present(kw, text, text_words):
            status = "covered"
        elif _present(kw, profile_text, profile_words):
            status = "in_profile_not_used"
        else:
            status = "gap"
        out.append({"keyword": kw, "status": status})
    return out
