from collections import Counter
from datetime import datetime, timezone

import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import TypeAdapter
from sqlmodel import SQLModel, create_engine, select

from jobsearch import db
from jobsearch.intel import enrich, news, pipeline
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
                ai_centricity="core", sector="HR tech", round="Seed", amount_text="$3 million",
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


def test_title_key_strips_publisher():
    assert news.title_key("Heidi raises $475 million - SmartCompany") == news.title_key("Heidi raises $475 million")


def test_keep_rules():
    assert pipeline._keep(event())
    assert not pipeline._keep(event(ai_centricity="peripheral"))
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


def test_schemas_compatible():
    for model in (Extraction, StartupProfile):
        assert transform_schema(TypeAdapter(model).json_schema())["additionalProperties"] is False
