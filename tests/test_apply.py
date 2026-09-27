from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobsearch import llm
from jobsearch.apply import answers, filler, schema
from jobsearch.apply.pipeline import blocking_fields
from jobsearch.apply.schema import FormField
from tests.fixtures import mock_forms


# ---------- schema / answers (no browser, no network) ----------

def test_detect_routes_direct_ats_and_guided():
    gh = SimpleNamespace(url="", sources=[{"source": "greenhouse", "url": "https://job-boards.greenhouse.io/acme/jobs/123"}])
    lv = SimpleNamespace(url="", sources=[{"source": "lever", "url": "https://jobs.lever.co/acme/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"}])
    az = SimpleNamespace(url="https://adzuna/x", sources=[{"source": "adzuna", "url": "https://adzuna/x"}])
    assert schema.detect(gh)[0:2] == ("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/123")
    assert schema.detect(lv)[1].endswith("/apply")
    assert schema.detect(az)[0] == "guided"


def _f(**kw):
    base = dict(key="k", label="Question", type="text", required=True, options=[], section="custom")
    base.update(kw)
    return FormField(**base)


def test_standard_answers_from_profile():
    prof = {"identity": {"name": "Alex Sample", "email": "v@example.com", "linkedin": "https://linkedin.com/in/v"},
            "application": {"first_name": "Alex", "last_name": "Sample", "phone_international": "+61 400 000 000",
                            "city": "Melbourne", "location": "Melbourne, VIC, Australia", "eeo_default": "decline"}}
    files = {"resume": "r.pdf", "cover_letter": "c.pdf"}
    sa = answers.standard_answer
    assert sa(_f(key="first_name", label="First Name"), prof, files) == ("Alex", "profile")
    assert sa(_f(key="phone", type="phone", label="Phone"), prof, files) == ("+61 400 000 000", "profile")
    assert sa(_f(key="candidate-location", type="location", label="Location (City)"), prof, files) == ("Melbourne", "profile")
    assert sa(_f(key="resume", type="file", label="Resume/CV"), prof, files) == ("r.pdf", "file")
    assert sa(_f(key="cover_letter", type="file", label="Cover Letter"), prof, files) == ("c.pdf", "file")
    assert sa(_f(key="q", label="LinkedIn Profile"), prof, files)[0] == "https://linkedin.com/in/v"
    eeo = _f(key="136", type="select", section="eeo", label="Gender", options=["Man", "Woman", "I don't wish to answer"])
    assert sa(eeo, prof, files) == ("I don't wish to answer", "default")
    assert sa(_f(key="q9", label="Why us?"), prof, files) is None  # left for the drafter


def test_drafted_choices_must_match_options(monkeypatch):
    fields = [_f(key="auth", type="select", options=["Yes", "No"], label="Authorised to work in Australia?"),
              _f(key="salary", type="text", label="Salary expectations?"),
              _f(key="why", type="textarea", label="Why this role?")]

    def fake(*, output_format, **kw):
        return output_format(answers=[
            answers.DraftedAnswer(key="auth", answer="yes", needs_you=False, basis="485 visa"),
            answers.DraftedAnswer(key="salary", answer="", needs_you=True, basis="salary_expectation is TODO"),
            answers.DraftedAnswer(key="why", answer="I cut latency by 73% at Acme.", needs_you=False, basis="cover letter"),
        ])
    monkeypatch.setattr(llm, "parse", fake)
    job = SimpleNamespace(title="AI Engineer", company="Acme", description="Build RAG.")
    out = {f.key: f for f in answers.fill_answers(fields, job, {}, [], "accuracy from 70% to 90%")}
    assert out["auth"].answer == "Yes" and not out["auth"].needs_you          # normalised to the exact option
    assert out["salary"].needs_you                                            # unknown -> you decide
    assert out["why"].needs_you and "73" in out["why"].note                   # invented number flagged
    assert [f["key"] for f in blocking_fields([f.to_dict() for f in out.values()])] == ["salary", "why"]


# ---------- the filler against local mock forms (fake data; never submits) ----------

@pytest.fixture(scope="module")
def browser():
    try:
        with sync_playwright() as p:
            from jobsearch import browser as browsers

            b = browsers.launch(p, headless=True)
            yield b
            b.close()
    except Exception as e:  # e.g. sandboxed shells where Edge can't start
        pytest.skip(f"Edge not available: {e}")


def _run(browser, html, ats, fields):
    page = browser.new_page()
    page.set_content(html)
    res = filler.FillResult()
    filler.fill_form(page, ats, fields, res)
    return page, res


def test_fill_greenhouse_mock(browser, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    fields = [
        {"key": "first_name", "label": "First Name", "type": "text", "answer": "Test"},
        {"key": "email", "label": "Email", "type": "email", "answer": "test@example.com"},
        {"key": "phone", "label": "Phone", "type": "phone", "answer": "+61 400 000 000"},
        {"key": "candidate-location", "label": "City", "type": "location", "answer": "Melbourne"},
        {"key": "resume", "label": "Resume", "type": "file", "answer": str(resume)},
        {"key": "question_2", "label": "Sponsorship", "type": "select", "answer": "No"},
        {"key": "136", "label": "Gender", "type": "select", "answer": "I don't wish to answer"},
    ]
    page, res = _run(browser, mock_forms.GREENHOUSE, "greenhouse", fields)
    assert res.problems == []
    assert page.input_value("#first_name") == "Test"
    assert page.input_value('[id="candidate-location"]') == "Melbourne VIC, Australia"
    assert page.input_value('[id="136"]') == "I don't wish to answer"
    assert page.eval_on_selector("#resume", "e => e.files.length") == 1
    assert page.evaluate("window.__submitted") is False


def test_fill_lever_mock(browser):
    fields = [
        {"key": "name", "label": "Full name", "type": "text", "answer": "Test Person"},
        {"key": "cards[a][field0]", "label": "Authorised", "type": "select", "answer": "Yes"},
        {"key": "cards[a][field1]", "label": "Notice", "type": "radio", "answer": "3-4 weeks"},
        {"key": "surveysResponses[x]", "label": "Ethnicity", "type": "checkbox", "answer": "Prefer not to say"},
        {"key": "cards[a][field2]", "label": "Anything else", "type": "textarea", "answer": "Line one.\nLine two."},
    ]
    page, res = _run(browser, mock_forms.LEVER, "lever", fields)
    assert res.problems == []
    assert page.input_value('[name="cards[a][field0]"]') == "Yes"
    assert page.is_checked('[name="cards[a][field1]"][value="3-4 weeks"]')
    assert page.is_checked('[name="surveysResponses[x]"][value="Prefer not to say"]')
    assert page.evaluate("window.__submitted") is False


def test_fill_ashby_mock_and_report_problems(browser):
    fields = [
        {"key": "_systemfield_name", "label": "Name", "type": "text", "answer": "Test Person"},
        {"key": "visa-q", "label": "Visa", "type": "boolean", "answer": "No"},
        {"key": "why", "label": "AI at work", "type": "textarea", "answer": "I use it daily."},
        {"key": "_systemfield_resume", "label": "Resume", "type": "file", "answer": "missing.pdf"},
    ]
    page, res = _run(browser, mock_forms.ASHBY, "ashby", fields)
    assert page.eval_on_selector('[name="visa-q"]', "e => e.parentElement.dataset.v") == "No"
    assert page.input_value("#why") == "I use it daily."
    assert len(res.problems) == 1 and "Resume" in res.problems[0]   # missing file reported, not ignored
    assert page.evaluate("window.__submitted") is False


def test_invented_free_text_goes_to_you_and_personal_questions_too(monkeypatch):
    from jobsearch.tailor.verify import ClaimVerdict, VerificationResult
    fields = [_f(key="ai", type="textarea", label="Tell us how you use AI tools at work"),
              _f(key="rel", type="select", options=["Yes", "No"], label="Do you know anyone at Acme?")]
    calls = []

    def fake(*, output_format, user, **kw):
        calls.append(output_format.__name__)
        if output_format is VerificationResult:
            return output_format(verdicts=[ClaimVerdict(claim_id="ai", supported=False, issue="prompting story not in record")])
        assert "Do you know anyone" not in user  # personal questions never reach the drafter
        return output_format(answers=[answers.DraftedAnswer(
            key="ai", answer="I prompted Claude with sample queries to design my routing logic every day.",
            needs_you=False, basis="Acme")])
    monkeypatch.setattr(llm, "parse", fake)
    job = SimpleNamespace(title="AI Engineer", company="Acme", description="")
    out = {f.key: f for f in answers.fill_answers(fields, job, {}, [], "intent-routing layer at Acme")}
    assert out["ai"].needs_you and "not in record" in out["ai"].note and out["ai"].answer  # draft kept as suggestion
    assert out["rel"].needs_you and out["rel"].answer == ""
    assert calls == ["DraftedAnswers", "VerificationResult"]
