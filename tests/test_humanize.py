import re
from types import SimpleNamespace

import pytest

from jobsearch import llm
from jobsearch.tailor import humanize, pipeline, verify
from jobsearch.tailor.facts import load_catalogue
from jobsearch.tailor.tailor import Bullet, EntryChoice, SkillGroup, TailoredResume

JOB = SimpleNamespace(id=1, title="AI Engineer", company="Acme", description="Build RAG systems.",
                      assessment={"ats_keywords": ["RAG"], "must_have_skills": ["FastAPI"]})


@pytest.fixture
def cat():
    return load_catalogue()


def resume(cat) -> TailoredResume:
    return TailoredResume(
        headline="AI Engineer",
        summary=Bullet(text="AI Engineer who leverages cutting-edge RAG — delivering robust, seamless systems.",
                       sources=["profile.summary"]),
        skills=[SkillGroup(label="LLM", items=["RAG"])],
        experience=[EntryChoice(ref="acme", bullets=[
            Bullet(text="Spearheaded an intent-routing layer, enabling accuracy to rise from 70% to 90%.", sources=["acme.2", "acme.3"]),
            Bullet(text="Ships through a branch-based release process.", sources=["acme.5"])])],
        projects=[],
        cover_letter=[Bullet(text="I am writing to express my passionate interest in Acme.", sources=["job"])],
        tailoring_notes="",
    )


def test_find_tells():
    hits = humanize.find_tells("We leverage robust tools — moreover, it serves as a testament to synergy.")
    assert {"leverage", "robust", "synergy", "serves as", "testament to", "moreover", "em dash x1"} <= set(hits)
    assert humanize.find_tells("Built a FastAPI service that cut latency from 4s to 1s.") == []


def test_humanize_keeps_good_rewrites_and_reverts_drift(cat, monkeypatch):
    # humanize and verify share one llm module, so a single fake answers both kinds of call
    def fake_parse(*, user, output_format, **kw):
        if output_format is verify.VerificationResult:
            ids = re.findall(r'<claim id="([^"]+)">', user)
            return output_format(verdicts=[verify.ClaimVerdict(claim_id=i, supported=True, issue="") for i in ids])
        assert "Job keywords to keep: RAG, FastAPI" in user
        return output_format(items=[
            humanize.HumanizedItem(id="summary", text="AI Engineer building RAG systems end to end."),
            # invents a number: must be reverted by the re-check
            humanize.HumanizedItem(id="experience/acme/0", text="Built an intent-routing layer that lifted accuracy from 70% to 95%."),
            humanize.HumanizedItem(id="experience/acme/1", text="Ships through a branch-based release process."),
            humanize.HumanizedItem(id="cover_letter/0", text="Acme's RAG work is why I'm applying."),
        ], changes=[humanize.ChangeRow(pass_name="Vocabulary", what_changed="cut AI words", example="leverages -> building")])
    monkeypatch.setattr(llm, "parse", fake_parse)

    out, report = humanize.humanize(resume(cat), cat, JOB)
    assert out.summary.text == "AI Engineer building RAG systems end to end."
    assert out.experience[0].bullets[0].text.startswith("Spearheaded")  # reverted to the verified text
    assert out.cover_letter[0].text == "Acme's RAG work is why I'm applying."
    assert report["lines_changed"] == 2 and report["reverted"][0]["where"] == "experience/acme/0"
    assert len(report["tells_after"]) < len(report["tells_before"])
    assert out.experience[0].bullets[0].sources == ["acme.2", "acme.3"]  # citations preserved


def test_humanize_step_keeps_verified_version_on_failure(cat, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("refused")
    monkeypatch.setattr(humanize.llm, "parse", boom)
    r = resume(cat)
    out, report = pipeline.humanize_step(r, cat, JOB)
    assert out == r and "refused" in report["skipped"]
