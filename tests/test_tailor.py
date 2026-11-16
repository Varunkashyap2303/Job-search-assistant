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
    assert fmt_range("2025-07 to 2025-11") == "Jul 2025 - Nov 2025"


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


# --- ATS rules -------------------------------------------------------------------------------

def _ats_job(**kw):
    base = dict(title="Senior AI Engineer (Hybrid) - Melbourne", company="Acme",
                description="We need RAG, FastAPI and semantic search experience. Kubernetes is a plus.",
                assessment={"must_have_skills": ["RAG", "FastAPI"], "nice_to_have_skills": ["Kubernetes"],
                            "ats_keywords": ["semantic search"]})
    base.update(kw)
    return SimpleNamespace(**base)


def test_ats_exact_title_goes_in_header_and_icons_are_removed(cat):
    from jobsearch.tailor import ats

    assert ats.clean_title("Senior AI Engineer (Hybrid) - Melbourne") == "Senior AI Engineer"
    assert ats.clean_title("AI Engineer - AI Services | 12 month contract") == "AI Engineer - AI Services"
    assert ats.clean_title("Data Engineer - Geelong", city="Geelong") == "Data Engineer"
    r = resume(cat, headline="AI Engineer | LLM Applications | Agents | Extra",
               summary=Bullet(text="\u260e Builds RAG systems \U0001F680", sources=["profile.summary"]))
    fixed, fixes = ats.enforce(r, _ats_job())
    assert fixed.headline == "Senior AI Engineer | LLM Applications | Agents"
    assert fixed.summary.text == "Builds RAG systems"
    assert len(fixes) == 2
    # The role actually held is untouched: it comes from the profile, not the ad
    assert cat.entries["acme"].heading.startswith("AI Engineer")
    # Already compliant: nothing changes
    again, fixes = ats.enforce(fixed, _ats_job())
    assert again == fixed and fixes == []


def test_ats_keywords_count_only_literal_matches_from_the_ad(cat):
    from jobsearch.tailor import ats

    r = resume(cat, ad_terms=["RAG", "semantic search", "Kubernetes", "LangChain"])
    kw = ats.keyword_report(r, _ats_job())
    # Kubernetes is in the ad but not the resume; LangChain is in neither the ad nor the resume
    assert kw["terms"] == ["RAG", "FastAPI"] and kw["status"] == "below"

    many = [f"term{i}" for i in range(40)]
    job = _ats_job(description=" ".join(many))
    assert ats.keyword_report(resume(cat, ad_terms=many[:30], skills=[SkillGroup(label="X", items=many[:30])]), job)["status"] == "within"
    assert ats.keyword_report(resume(cat, ad_terms=many, skills=[SkillGroup(label="X", items=many)]), job)["status"] == "above"


def test_dates_use_one_format(cat):
    from jobsearch.tailor import ats
    from jobsearch.tailor.facts import fmt_month

    assert [fmt_month(x) for x in ("2019-01", "January 2019", "Jan '19", "01/2019", "present")] == \
        ["Jan 2019", "Jan 2019", "Jan 2019", "Jan 2019", "Present"]
    assert cat.entries["acme"].dates == "Mar 2025 - Present"
    issues = ats.date_issues(resume(cat, projects=[EntryChoice(ref="atlas", bullets=[]), EntryChoice(ref="ragbench", bullets=[])]), cat)
    assert len(issues) == 1 and "2025" in issues[0]   # a year with no month is flagged, never guessed


def test_resume_files_are_ats_readable(cat, tmp_path):
    """One column of real text, standard headings, contact details in the body, plus a .docx twin."""
    import pypdfium2 as pdfium
    from docx import Document

    from jobsearch.tailor import ats, render

    fitted, _ = render.render_resume(resume(cat), cat, tmp_path / "resume.pdf")
    pdf = pdfium.PdfDocument(str(tmp_path / "resume.pdf"))
    try:
        text = pdf[0].get_textpage().get_text_range()
    finally:
        pdf.close()
    ident = cat.identity
    positions = [text.upper().find(h.upper()) for h in ats.SECTION_HEADERS]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    assert ident["email"] in text and text.find(ident["email"]) < positions[0]
    assert "Mar 2025 - Present" in text

    doc = Document(str(tmp_path / "resume.docx"))
    body = [p.text for p in doc.paragraphs]
    assert [p.text for p in doc.paragraphs if p.style.name == "Heading 1"] == [h.upper() for h in ats.SECTION_HEADERS]
    assert any(ident["email"] in t for t in body[:4])
    assert doc.tables == [] and len(doc.sections) == 1
    sec = doc.sections[0]
    assert not any(p.text.strip() for p in (*sec.header.paragraphs, *sec.footer.paragraphs))
    assert "w:cols" not in sec._sectPr.xml or 'w:num="2"' not in sec._sectPr.xml
