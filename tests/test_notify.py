from datetime import datetime, timezone

import httpx2 as httpx
import pytest
from sqlmodel import SQLModel, create_engine

from jobsearch import db, notify
from jobsearch.intel import report


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    return engine


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class FakeClient:
        def post(self, url, data=None, files=None):
            calls.append({"url": url, "data": data, "file": files["document"][0] if files else None})
            return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(notify, "http", lambda: FakeClient())
    return calls


def test_summary_text(temp_db):
    now = datetime.now(timezone.utc)
    with db.session() as s:
        acme = db.Startup(name="Acme AI", name_norm="acme ai", hq_city="Melbourne", latest_round="Seed",
                          latest_amount_text="$3 million", latest_announced=now, relevance_score=80,
                          status="profiled", profile={"ai_engineer_potential": "high"})
        s.add_all([acme,
                   db.Startup(name="Q-CTRL", name_norm="q ctrl", latest_round="Series C", latest_announced=now,
                              ai_hiring_potential="high"),
                   db.Startup(name="Brew", name_norm="brew", latest_announced=now, ai_hiring_potential="low")])
        s.commit()
        plan = db.OutreachPlan(startup_id=acme.id)
        s.add(plan)
        s.flush()
        s.add_all([db.OutreachDraft(plan_id=plan.id, startup_id=acme.id),
                   db.OutreachDraft(plan_id=plan.id, startup_id=acme.id, status="sent")])
        s.commit()
    text = report.summary_text({"budget_stop": 1})
    assert "3 new today · 1 profiled · 2 more" in text
    assert "1. Acme AI (new) · Melbourne · Seed $3 million" in text
    assert "AI engineer potential: High · fit 80 · 1 draft(s) to review" in text
    assert "• Q-CTRL · Series C · High" in text and "Brew" not in text     # low potential left out
    assert "budget cap" in text and len(text) < notify.MAX_TEXT


def test_deliver_brief_sends_summary_then_file(temp_db, telegram, tmp_path):
    brief = tmp_path / "intel_2026-11-19.md"
    brief.write_text("# Morning startup brief", encoding="utf-8")
    assert notify.deliver_brief(str(brief), {}) == "sent"
    assert [c["url"].rsplit("/", 1)[1] for c in telegram] == ["sendMessage", "sendDocument"]
    assert all(c["data"]["chat_id"] == "42" for c in telegram)                 # only ever your own chat
    assert telegram[1]["file"] == "intel_2026-11-19.md"


def test_deliver_brief_never_raises_or_leaks_token(temp_db, monkeypatch, tmp_path):
    assert notify.deliver_brief("x.md") == "not configured"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class Down:
        def post(self, url, **kw):
            raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(notify, "http", lambda: Down())
    status = notify.deliver_brief(str(tmp_path / "missing.md"))
    assert status.startswith("failed") and "secret-token" not in status
