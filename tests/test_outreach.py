import pytest
from anthropic import transform_schema
from pydantic import TypeAdapter
from sqlmodel import SQLModel, create_engine, select

from jobsearch import db
from jobsearch.outreach import compose, people, pipeline
from jobsearch.tailor.facts import load_catalogue


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    return engine


def test_email_helpers():
    emails = people.published_emails(["Contact hello@acme.ai or jane.doe@acme.ai, logo@2x.png, bob@other.com"], "acme.ai")
    assert emails == ["hello@acme.ai", "jane.doe@acme.ai"]
    folks = [{"name": "Dr Jane Doe"}, {"name": "Tom Kelly"}]
    assert people.email_pattern(emails, folks) == "first.last"
    assert people.apply_pattern("first.last", "Tom Kelly", "acme.ai") == "tom.kelly@acme.ai"
    assert people.email_pattern(["hello@acme.ai"], folks) == ""  # no evidence, no guess
    assert people.name_parts("Dr Tom Kelly") == ["tom", "kelly"]
    assert "Tom%20Kelly%20Heidi" in people.linkedin_people_search("Tom Kelly", "Heidi")


def msg(**kw):
    base = dict(contact_id="p0", name="Tom Kelly", role="Co-founder", priority=1, why="Leads product.",
                connection_note="Hi Tom, congrats on the Series C.", linkedin_message="Hi Tom...",
                email_subject="Intent routing for clinical facts", email_body="Hi Tom, lifted accuracy from 70% to 90%.",
                follow_up="Hi Tom, following up.")
    base.update(kw)
    return compose.ContactMessages(**base)


def test_check_messages():
    allowed = "accuracy from 70% to 90% Series C $140 million"
    assert compose.check_messages(msg(), allowed, 200, {"tom kelly"}) == []
    w = compose.check_messages(msg(connection_note="x" * 250, email_body="grew revenue 300%"), allowed, 200, {"tom kelly"})
    assert any("250 chars" in x for x in w) and any("300" in x for x in w)
    assert any("wasn't in the discovered" in x for x in compose.check_messages(msg(name="Jane Smith"), allowed, 200, {"tom kelly"}))


def test_draft_for_stores_plan(temp_db, monkeypatch):
    with db.session() as s:
        st = db.Startup(name="Heidi", name_norm="heidi", status="shortlisted",
                        profile={"people": [{"name": "Tom Kelly", "role": "Co-founder", "source": "article"}]})
        s.add(st)
        s.commit()
    discovered = {"people": [{"name": "Tom Kelly", "role": "Co-founder", "source": "article", "email": "",
                              "email_confidence": "none", "linkedin_search": "https://linkedin/x"}],
                  "generic_emails": [], "email_pattern": "", "pages_checked": [], "website": ""}
    monkeypatch.setattr(pipeline.people, "discover", lambda startup: discovered)
    plan_out = compose.OutreachPlanOut(strategy="Start with Tom.", contacts=[
        msg(), msg(contact_id="role:Head of Engineering", name="", role="Head of Engineering", priority=2,
                   connection_note="Hi {first_name}, congrats on the raise.")])
    monkeypatch.setattr(pipeline, "compose", lambda *a, **k: plan_out)
    plan = pipeline.draft_for(st, load_catalogue(), "")
    with db.session() as s:
        drafts = s.exec(select(db.OutreachDraft).where(db.OutreachDraft.plan_id == plan.id)).all()
    assert [d.name for d in drafts] == ["Tom Kelly", ""]
    assert drafts[1].source == "role to find on LinkedIn" and "Head%20of%20Engineering" in drafts[1].linkedin_search
    pipeline.mark_sent(drafts[0].id)
    with db.session() as s:
        d = s.get(db.OutreachDraft, drafts[0].id)
    assert d.status == "sent" and d.follow_up_due is not None


def test_schema_compatible():
    assert transform_schema(TypeAdapter(compose.OutreachPlanOut).json_schema())["additionalProperties"] is False


def test_llm_unescape_fixes_double_escaped_output():
    from jobsearch.llm import _unescape
    double_escaped = r"Hi Yu \u2014 hello\n\nBye"   # literal backslashes, as the model sometimes emits
    assert "\\" in double_escaped
    assert _unescape({"a": [double_escaped], "b": 3}) == {"a": ["Hi Yu \u2014 hello\n\nBye"], "b": 3}
    assert _unescape("plain text") == "plain text"
