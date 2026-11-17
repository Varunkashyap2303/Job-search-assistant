from collections import Counter
from datetime import datetime, timezone

import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import TypeAdapter
from sqlmodel import SQLModel, create_engine, select

from jobsearch import db
from jobsearch.intel import enrich, news, pipeline, report
from jobsearch.intel.extract import Extraction, FundingEvent
from jobsearch.intel.profile import StartupProfile


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    return engine


def art(title, summary="", au_source=True, text=""):
    return news.Article(key=news.title_key(title), title=title, url="https://x", source="Test",
                        published_at=datetime(2026, 9, 20, tzinfo=timezone.utc), summary=summary,
                        text=text, au_source=au_source)


def event(**kw):
    base = dict(article=0, company="Acme AI", website="", is_australian="yes", hq_city="Melbourne",
                ai_centricity="core", ai_hiring_potential="medium", sector="HR tech", round="Seed", amount_text="$3 million",
                amount_aud_millions=3.0, investors=["Blackbird"], announced_date="", one_line="AI recruiting agents")
    base.update(kw)
    return FundingEvent(**base)


def test_candidate_filter():
    assert news.is_candidate(art("Melbourne AI startup Acme raises $3 million seed round"))
    assert news.is_candidate(art("Seven Aussie startups that raised $274.7 million this week"))  # round-up
    assert not news.is_candidate(art("Proptech RentBetter moves in on $5 million"))  # no AI angle
    assert not news.is_candidate(art("AI startup raises $50m Series B", au_source=False))  # not Australian
    assert news.is_candidate(art("AI startup raises $50m Series B", summary="The Sydney company...", au_source=False))
    assert not news.is_candidate(art("Canva launches new AI design tools"))  # no funding
    # Broad mode (the default): any AU funding story goes on to extraction, which rates AI hiring potential
    assert news.is_candidate(art("Proptech RentBetter moves in on $5 million"), topic=None)
    assert not news.is_candidate(art("Canva launches new AI design tools"), topic=None)
    assert news.topic_regex({}) is None
    assert news.topic_regex({"require_topic_mention": True}).search("an agentic platform")


def test_title_key_strips_publisher():
    assert news.title_key("Heidi raises $475 million - SmartCompany") == news.title_key("Heidi raises $475 million")


def test_keep_rules():
    assert pipeline._keep(event())
    assert not pipeline._keep(event(ai_centricity="peripheral", ai_hiring_potential="low"))
    assert pipeline._keep(event(ai_centricity="none", ai_hiring_potential="high"))  # fintech adding AI after a raise
    assert pipeline._keep(event(ai_centricity="core", ai_hiring_potential="low"))
    assert not pipeline._keep(event(is_australian="no"))
    assert pipeline._keep(event(is_australian="unclear", hq_city="Sydney"))
    assert not pipeline._keep(event(is_australian="unclear", hq_city=""))


def test_upsert_merges_events(temp_db):
    stats = Counter()
    a1 = art("Acme AI raises $3m seed")
    pipeline.upsert_startup(event(announced_date="2026-06-01"), a1, stats)
    a2 = art("Acme AI lands $15m Series A")
    pipeline.upsert_startup(event(company="Acme AI Pty Ltd", round="Series A", amount_text="$15 million",
                                  amount_aud_millions=15.0, announced_date="2026-09-20"), a2, stats)
    with db.session() as s:
        rows = s.exec(select(db.Startup)).all()
    assert len(rows) == 1 and stats["startups.new"] == 1 and stats["startups.updated"] == 1
    st = rows[0]
    assert st.latest_round == "Series A" and st.latest_amount_aud_m == 15.0
    assert len(st.events) == 2 and len(st.sources) == 2


def test_slugs_and_excerpt():
    assert enrich.slugs("Heidi Health") == ["heidihealth", "heidi-health"]
    assert "harrison" in enrich.slugs("Harrison.ai")
    text = "x" * 5000 + " Acme AI raised money " + "y" * 5000
    assert "Acme AI" in pipeline._excerpt_about(text, "Acme AI")


def test_extract_all_splits_overflowing_batches(monkeypatch):
    from jobsearch import llm
    from jobsearch.intel import extract
    calls = []

    def fake(batch):
        calls.append(len(batch))
        if len(batch) > 2:
            raise llm.OutputTruncated("Hit max_tokens")
        return [event(article=i) for i, _ in batch]

    monkeypatch.setattr(extract, "extract", fake)
    batch = [(i, art(f"Startup {i} raises $1m")) for i in range(5)]
    assert [e.article for e in extract.extract_all(batch)] == [0, 1, 2, 3, 4]
    assert calls == [5, 2, 3, 1, 2]
    monkeypatch.setattr(extract, "extract", lambda b: (_ for _ in ()).throw(llm.OutputTruncated("x")))
    with pytest.raises(llm.OutputTruncated):   # a single article that still overflows is reported, not looped
        extract.extract_all(batch[:1])


def test_schemas_compatible():
    for model in (Extraction, StartupProfile):
        assert transform_schema(TypeAdapter(model).json_schema())["additionalProperties"] is False


def _profile(**kw):
    base = dict(one_liner="AI recruiting agents", vision="v", product="p", customers_and_market="c",
                business_model="b", funding_summary="Seed $3m led by Blackbird", ai_centricity="core",
                ai_implementation="LLM agents screen candidates.", ai_engineer_potential="high",
                ai_engineer_potential_reasons="Raising to build the agent platform.",
                ai_engineer_work="Agent evaluation harness.", likely_ai_roles=["AI Engineer"],
                hiring_signals="2 open roles", relevance_score=80, relevance_reasons="Agents match your work.",
                outreach_angle="Lead with the agent project.", unknowns=["team size"],
                people=[{"name": "Jane Doe", "role": "CEO", "source": "article"}])
    base.update(kw)
    return base


def test_morning_report(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORT_DIR", tmp_path / "reports")
    recent = datetime.now(timezone.utc)
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db.session() as s:
        acme = db.Startup(name="Acme AI", name_norm="acme ai", hq_city="Melbourne", sector="HR tech",
                          latest_round="Seed", latest_amount_text="$3 million", latest_announced=recent,
                          relevance_score=80, status="profiled", profile=_profile(),
                          sources=[{"title": "Acme raises", "url": "https://news/acme", "source": "SD", "date": ""}])
        s.add_all([
            acme,
            db.Startup(name="PayCo", name_norm="payco", hq_city="Sydney", sector="Fintech", latest_round="Series A",
                       latest_announced=recent, relevance_score=60, status="profiled",
                       profile=_profile(ai_centricity="none", ai_engineer_potential="medium")),
            db.Startup(name="Brew", name_norm="brew", latest_announced=recent, relevance_score=20, status="profiled",
                       profile=_profile(ai_engineer_potential="low"), one_line="Coffee chain."),
            db.Startup(name="Queued", name_norm="queued", latest_announced=recent, ai_hiring_potential="high",
                       one_line="Not profiled yet."),
            db.Startup(name="Gone", name_norm="gone", latest_announced=recent, status="dismissed"),
            db.Startup(name="Stale", name_norm="stale", latest_announced=old, first_seen_at=old,
                       status="profiled", profile=_profile()),
        ])
        s.commit()
        plan = db.OutreachPlan(startup_id=acme.id, strategy="Start with Jane.")
        s.add(plan)
        s.flush()
        s.add_all([
            db.OutreachDraft(plan_id=plan.id, startup_id=acme.id, priority=1, name="Jane Doe", role="CEO",
                             source="article", email="jane@acme.ai", email_confidence="published",
                             linkedin_search="https://linkedin/jane", why="Decides on hires.",
                             messages={"connection_note": "Hi Jane, congrats on the seed.",
                                       "linkedin_message": "Hi Jane,\nmore", "email_subject": "Agents at Acme",
                                       "email_body": "Hi Jane", "follow_up": "Hi again"},
                             warnings=["number 40% not in profile"]),
            db.OutreachDraft(plan_id=plan.id, startup_id=acme.id, priority=2, role="Head of Engineering",
                             linkedin_search="https://linkedin/hoe", status="sent", messages={"connection_note": "secret"}),
        ])
        s.commit()

    from pathlib import Path
    text = Path(report.write_report({"profiled": 2, "outreach.planned": 1})).read_text(encoding="utf-8")
    assert "# Morning startup brief" in text and "2 profiled, 1 outreach plans drafted" in text
    assert text.index("## 1. Acme AI") < text.index("## 2. PayCo")              # sorted by fit
    assert "### Potential for AI engineers: High" in text and "Agent evaluation harness." in text
    assert "| 1 | Jane Doe | CEO | article | jane@acme.ai (published) | [search](https://linkedin/jane) |" in text
    assert "> Hi Jane, congrats on the seed." in text and "> more" in text and "Check: number 40%" in text
    assert "secret" not in text and "Marked sent" in text                        # sent drafts aren't repeated
    assert "No messages drafted yet. People named in the sources:" in text        # PayCo has no plan
    other = text[text.index("## Other recent funding news"):]
    assert "**Brew**" in other and "**Queued**" in other and "not profiled yet" in other
    assert "Gone" not in text and "Stale" not in text
