import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from jobsearch import db, mcp_server


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    now = datetime.now(timezone.utc)
    with db.session() as s:
        qctrl = db.Startup(name="Q-CTRL", name_norm="qctrl", hq_city="Sydney", latest_round="Series C",
                           latest_announced=now, relevance_score=70, status="profiled",
                           profile={"ai_engineer_potential": "medium", "vision": "v"})
        s.add_all([qctrl,
                   db.Startup(name="Cuttable", name_norm="cuttable", latest_announced=now, ai_hiring_potential="high"),
                   db.Startup(name="Morgan", name_norm="morgan", latest_announced=now),
                   db.Startup(name="Morgan AI", name_norm="morganai", latest_announced=now)])
        s.commit()
        plan = db.OutreachPlan(startup_id=qctrl.id, strategy="Start with the CTO.")
        s.add(plan)
        s.flush()
        s.add_all([
            db.OutreachDraft(plan_id=plan.id, startup_id=qctrl.id, name="Jane Doe", role="CTO",
                             messages={"connection_note": "Hi Jane", "follow_up": "Hi again"}),
            db.OutreachDraft(plan_id=plan.id, startup_id=qctrl.id, name="Tom Lee", role="CEO", status="sent",
                             follow_up_due=now - timedelta(days=1), messages={"follow_up": "Following up"}),
        ])
        s.commit()
    return engine


def rpc(*messages) -> list[dict]:
    out = io.BytesIO()
    mcp_server.serve(io.BytesIO("".join(json.dumps(m) + "\n" for m in messages).encode()), out)
    return [json.loads(line) for line in out.getvalue().decode().splitlines()]


def call(name, **arguments) -> tuple[bool, str]:
    reply = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})[0]
    return reply["result"]["isError"], reply["result"]["content"][0]["text"]


def test_handshake_and_tool_list():
    replies = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  {"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert len(replies) == 3                                                   # notifications get no reply
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"get_brief", "list_startups", "get_drafts", "draft_outreach", "update_draft"} <= names
    assert not any("send" in n for n in names)                                 # nothing can be sent from here
    assert all(t["inputSchema"]["type"] == "object" for t in replies[1]["result"]["tools"])
    assert replies[2]["error"]["code"] == -32601


def test_find_startup(temp_db):
    assert mcp_server.find_startup("qctrl").name == "Q-CTRL"
    assert mcp_server.find_startup("Q CTRL").name == "Q-CTRL"
    assert mcp_server.find_startup("Cutable").name == "Cuttable"              # typo
    assert mcp_server.find_startup("morgan ai").name == "Morgan AI"
    with pytest.raises(mcp_server.ToolError, match="No startup"):
        mcp_server.find_startup("Canva")


def test_read_tools(temp_db):
    err, text = call("list_startups")
    rows = json.loads(text)
    assert not err and rows[0]["name"] == "Q-CTRL" and rows[0]["drafts"] == 2
    err, text = call("get_drafts", startup="Q-CTRL")
    assert not err and [d["name"] for d in json.loads(text)] == ["Jane Doe"]  # open drafts only by default
    err, text = call("follow_ups_due")
    assert not err and json.loads(text)[0]["follow_up"] == "Following up"
    err, text = call("get_startup", startup="nonexistent")
    assert err and "No startup matches" in text


def test_free_actions(temp_db):
    assert call("set_startup_status", startup="Cuttable", status="shortlisted") == (False, "Cuttable is now shortlisted.")
    assert call("set_startup_status", startup="Cuttable", status="deleted")[0]
    err, text = call("get_drafts", startup="qctrl")
    draft_id = json.loads(text)[0]["draft_id"]
    assert not call("update_draft", draft_id=draft_id, status="sent")[0]
    with db.session() as s:
        d = s.get(db.OutreachDraft, draft_id)
    assert d.status == "sent" and d.follow_up_due is not None


def test_paid_actions_respect_profile_and_budget(temp_db, monkeypatch):
    err, text = call("draft_outreach", startup="Cuttable")
    assert err and "isn't profiled" in text
    from jobsearch import llm
    from jobsearch.intel import pipeline

    def over_budget(**kw):
        raise llm.BudgetExceeded("Daily cap $1.5 reached")

    monkeypatch.setattr(pipeline, "run_intel", over_budget)
    err, text = call("profile_startup", startup="Cuttable")
    assert err and "Daily cap" in text
