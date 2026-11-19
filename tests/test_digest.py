from datetime import datetime, timedelta, timezone

import httpx2 as httpx
import pytest
from sqlmodel import SQLModel, create_engine

from jobsearch import db, digest, notify


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    return engine


def add_startup(**kw) -> int:
    with db.session() as s:
        st = db.Startup(name_norm=kw["name"].lower(), latest_announced=datetime.now(timezone.utc), **kw)
        s.add(st)
        s.commit()
        return st.id


def add_drafts(startup_id: int, *people: str) -> list[int]:
    with db.session() as s:
        plan = db.OutreachPlan(startup_id=startup_id)
        s.add(plan)
        s.flush()
        drafts = [db.OutreachDraft(plan_id=plan.id, startup_id=startup_id, priority=i, name=p, role="CTO")
                  for i, p in enumerate(people, 1)]
        s.add_all(drafts)
        s.commit()
        return [d.id for d in drafts]


def set_draft(draft_id: int, **kw) -> None:
    with db.session() as s:
        d = s.get(db.OutreachDraft, draft_id)
        for k, v in kw.items():
            setattr(d, k, v)
        s.add(d)
        s.commit()


def morning():
    """What the scheduled run does, minus Telegram: build the message and record what it showed."""
    message = digest.morning_message()
    if message:
        digest.record(*message)
    return message


def test_brief_then_checkin_then_silence(temp_db):
    acme = add_startup(name="Acme AI", status="profiled", relevance_score=80, hq_city="Melbourne",
                       profile={"ai_engineer_potential": "high"})
    add_startup(name="Queued Co", ai_hiring_potential="high")
    add_startup(name="Brew", status="profiled", profile={"ai_engineer_potential": "low"})
    jane, tom = add_drafts(acme, "Jane Doe", "Tom Lee")

    kind, text, *_ = morning()                                   # day 1: everything is new
    assert kind == "brief"
    assert "1. Acme AI · Melbourne" in text and f"#{jane} Jane Doe (CTO)" in text
    assert "• Queued Co" in text and "Brew" not in text          # low potential never shown

    kind, text, *_ = morning()                                   # day 2: nothing new -> check-in
    assert kind == "checkin" and "No new startups or drafts today." in text
    assert f"• Acme AI: #{jane} Jane Doe (CTO), #{tom} Tom Lee (CTO)" in text
    assert "Acme AI · Melbourne" not in text and "Queued Co" not in text  # no repeats
    assert "So far: 0 sent" in text and '"sent #4"' in text

    set_draft(jane, status="sent", sent_at=datetime.now(timezone.utc) - timedelta(days=8),
              follow_up_due=datetime.now(timezone.utc) - timedelta(days=1))
    kind, text, *_ = morning()                                   # day 3: follow-up due, Tom still unsent
    assert "Follow-ups due" in text and f"#{jane} Jane Doe (CTO) at Acme AI" in text
    assert f"• Acme AI: #{tom} Tom Lee (CTO)" in text and "1 sent" in text

    set_draft(jane, status="replied", follow_up_due=None)
    set_draft(tom, status="skipped")
    assert morning() is None                                     # day 4: nothing new or pending -> silent


def test_new_drafts_for_a_seen_startup_trigger_a_brief(temp_db):
    acme = add_startup(name="Acme AI", status="profiled", relevance_score=80, profile={"ai_engineer_potential": "high"})
    add_drafts(acme, "Jane Doe")
    morning()
    (new_draft,) = add_drafts(acme, "Ana Kim")
    kind, text, startup_ids, draft_ids = morning()
    assert kind == "brief" and draft_ids == [new_draft] and startup_ids == []
    assert "New drafts for startups you've seen" in text and f"• Acme AI: #{new_draft} Ana Kim" in text
    assert "1. Acme AI" not in text and "Still waiting on you: 1 earlier draft(s)" in text


def test_baseline_marks_everything_seen(temp_db):
    acme = add_startup(name="Acme AI", status="profiled", profile={"ai_engineer_potential": "high"})
    add_drafts(acme, "Jane Doe")
    assert digest.mark_all_seen() == (1, 1)
    assert digest.morning_message()[0] == "checkin"


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class FakeClient:
        fail = False

        def post(self, url, data=None, files=None):
            if FakeClient.fail:
                raise httpx.ConnectError("down")
            calls.append(url.rsplit("/", 1)[1])
            return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(notify, "http", lambda: FakeClient())
    return calls, FakeClient


def test_morning_update_sends_records_and_stays_quiet(temp_db, telegram, tmp_path):
    telegram, client = telegram
    brief = tmp_path / "intel.md"
    brief.write_text("# brief", encoding="utf-8")
    assert notify.morning_update(str(brief)) == "nothing new or pending: no message" and telegram == []

    acme = add_startup(name="Acme AI", status="profiled", profile={"ai_engineer_potential": "high"})
    add_drafts(acme, "Jane Doe")
    client.fail = True
    assert notify.morning_update(str(brief)).startswith("failed")
    assert digest.seen() == (set(), set())                      # a failed send isn't recorded as seen

    client.fail = False
    assert notify.morning_update(str(brief)) == "sent brief"
    assert telegram == ["sendMessage", "sendDocument"]
    assert notify.morning_update(str(brief)) == "sent checkin"  # next morning: text only, no file
    assert telegram[2:] == ["sendMessage"]
