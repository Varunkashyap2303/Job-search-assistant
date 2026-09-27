from types import SimpleNamespace

import pytest

from jobsearch.tailor import verify
from jobsearch.tailor.coverage import keyword_coverage
from jobsearch.tailor.facts import fmt_range, load_catalogue
from jobsearch.tailor.render import _trim_one
from jobsearch.tailor.tailor import Bullet, EntryChoice, SkillGroup, TailoredResume

JOB = SimpleNamespace(title="AI Engineer", company="Acme", description="Build RAG systems with FastAPI.")


@pytest.fixture
def cat():
    return load_catalogue()


@pytest.fixture
def all_supported(monkeypatch):
    """Stand-in for the Sonnet verifier: marks every claim it receives as supported."""
    def fake_parse(*, user, output_format, **kw):
        import re
        ids = re.findall(r'<claim id="([^"]+)">', user)
        return output_format(verdicts=[verify.ClaimVerdict(claim_id=i, supported=True, issue="") for i in ids])
    monkeypatch.setattr(verify.llm, "parse", fake_parse)


def resume(cat, **overrides) -> TailoredResume:
    base = dict(
        headline="AI Engineer",
        summary=Bullet(text=cat.summary, sources=["profile.summary"]),
        skills=[SkillGroup(label="LLM", items=["RAG", "FastAPI"])],
        experience=[EntryChoice(ref="acme", bullets=[Bullet(text="Raised inventory accuracy from 70% to 90%.", sources=["acme.3"])])],
        projects=[EntryChoice(ref="atlas", bullets=[Bullet(text="Owned AI engineering in a team of 5.", sources=["atlas.2"])])],
        cover_letter=[Bullet(text="I build RAG systems.", sources=["acme.2"])],
        tailoring_notes="",
    )
    base.update(overrides)
    return TailoredResume(**base)


def test_catalogue_ids_and_incomplete_entries(cat):
    assert "acme.3" in cat.facts and "70%" in cat.facts["acme.3"]
    assert "oldjob" not in cat.entries  # no dates/facts yet, so it must not reach a resume
    assert fmt_range("2025-07 to 2025-11") == "Jul 2025 – Nov 2025"


def test_clean_resume_passes_untouched(cat, all_supported):
    fixed, report = verify.verify_and_fix(resume(cat), cat, JOB)
    assert report == []
    assert fixed.experience[0].bullets[0].text.startswith("Raised")


def test_invented_number_falls_back_to_source(cat, all_supported):
    r = resume(cat, experience=[EntryChoice(ref="acme", bullets=[
        Bullet(text="Raised accuracy from 70% to 95%.", sources=["acme.3"])])])
    fixed, report = verify.verify_and_fix(r, cat, JOB)
    assert fixed.experience[0].bullets[0].text == cat.facts["acme.3"]
    assert report[0]["action"] == "replaced" and "number" in report[0]["reason"]


def test_cross_entry_citation_is_rejected(cat, all_supported):
    r = resume(cat, experience=[EntryChoice(ref="acme", bullets=[
        Bullet(text="Won Best Project of the Semester at Acme.", sources=["atlas.5"])])])
    fixed, report = verify.verify_and_fix(r, cat, JOB)
    assert all("Best Project" not in b.text for b in fixed.experience[0].bullets)
    assert "different job" in report[0]["reason"]


def test_unknown_skill_dropped_and_missing_job_restored(cat, all_supported):
    r = resume(cat, skills=[SkillGroup(label="Infra", items=["Kubernetes", "AWS (ECS, Lambda)"])], experience=[])
    fixed, report = verify.verify_and_fix(r, cat, JOB)
    assert fixed.skills[0].items == ["AWS (ECS, Lambda)"]
    assert [c.ref for c in fixed.experience] == ["acme"]
    assert {i["action"] for i in report} == {"dropped", "restored"}


def test_llm_rejection_replaces_and_drops(cat, monkeypatch):
    def reject_all(*, user, output_format, **kw):
        import re
        ids = re.findall(r'<claim id="([^"]+)">', user)
        return output_format(verdicts=[verify.ClaimVerdict(claim_id=i, supported=False, issue="inflated") for i in ids])
    monkeypatch.setattr(verify.llm, "parse", reject_all)
    fixed, _ = verify.verify_and_fix(resume(cat), cat, JOB)
    assert fixed.summary.text == cat.summary
    assert fixed.experience[0].bullets[0].text == cat.facts["acme.3"]
    assert fixed.cover_letter == []


def test_trim_order(cat):
    r = resume(cat, projects=[
        EntryChoice(ref="atlas", bullets=[Bullet(text="a", sources=[]), Bullet(text="b", sources=[])]),
        EntryChoice(ref="ragbench", bullets=[Bullet(text="c", sources=[])]),
    ])
    assert _trim_one(r) == "trimmed a bullet from atlas"
    assert _trim_one(r) == "dropped project ragbench"


def test_coverage(cat):
    cov = keyword_coverage(resume(cat), cat, {"must_have_skills": ["RAG", "Qdrant", "Kubernetes"], "ats_keywords": []})
    assert {c["keyword"]: c["status"] for c in cov} == {"RAG": "covered", "Qdrant": "in_profile_not_used", "Kubernetes": "gap"}


def test_output_schemas_are_structured_output_compatible():
    from anthropic.lib._parse._transform import transform_schema
    from pydantic import TypeAdapter

    for model in (TailoredResume, verify.VerificationResult):
        schema = transform_schema(TypeAdapter(model).json_schema())
        assert schema["additionalProperties"] is False


def test_digits_inside_names_are_not_numbers(cat, all_supported):
    r = resume(cat, projects=[EntryChoice(ref="ragbench", bullets=[
        Bullet(text="Built a hybrid Graph + Vector RAG agent (Claude, ChromaDB, Neo4j) with cross-encoder reranking.",
               sources=["ragbench.1"])])])
    fixed, report = verify.verify_and_fix(r, cat, JOB)
    assert report == [] and "Neo4j" in fixed.projects[0].bullets[0].text


def test_verifier_sees_stack_of_cited_entry(cat):
    text = verify._source_text(["acme.1"], cat, JOB)
    assert "[acme stack]" in text and "Qdrant" in text and "PostgreSQL" in text
