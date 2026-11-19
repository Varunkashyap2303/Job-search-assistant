"""MCP server for the job search, so Hermes Agent, Claude Desktop or Claude Code can read the morning brief,
startups and outreach drafts, and run the same actions as the dashboard buttons.

Run with `python -m jobsearch mcp`. It speaks MCP over stdio (newline-delimited JSON-RPC 2.0) and only
implements tools, so it needs no extra packages. There is deliberately no tool that sends anything: drafts
are shown and marked, and you send every message yourself. Paid tools go through the same budget guard as
the dashboard (your daily and monthly caps always apply)."""

import contextlib
import json
import logging
import sys
import traceback
from datetime import datetime, timezone

from rapidfuzz import fuzz, process
from sqlmodel import select

from jobsearch import llm
from jobsearch.config import DATA_DIR, preferences
from jobsearch.db import OutreachDraft, OutreachPlan, Startup, as_utc, session
from jobsearch.scout.dedupe import norm_company

log = logging.getLogger(__name__)

SERVER_INFO = {"name": "jobsearch", "version": "0.1.0"}
DEFAULT_PROTOCOL = "2025-06-18"
TOOLS: dict[str, dict] = {}


class ToolError(Exception):
    """A problem the caller can fix (unknown startup, bad status); shown to the model as a tool error."""


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None):
    def register(fn):
        TOOLS[name] = {"fn": fn, "spec": {
            "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": required or []}}}
        return fn
    return register


STARTUP_ARG = {"startup": {"type": "string", "description": "startup name (fuzzy match is fine) or numeric id"}}


def find_startup(query: str) -> Startup:
    query = str(query).strip()
    with session() as s:
        rows = s.exec(select(Startup)).all()
    if query.isdigit():
        match = next((r for r in rows if r.id == int(query)), None)
        if match:
            return match
    key = norm_company(query)
    exact = [r for r in rows if norm_company(r.name) == key]
    if len(exact) == 1:
        return exact[0]
    contains = exact or [r for r in rows if key and key in norm_company(r.name)]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        raise ToolError("Several startups match: " + ", ".join(f"{r.name} (id {r.id})" for r in contains))
    best = process.extractOne(query, {r.id: r.name for r in rows}, scorer=fuzz.WRatio, score_cutoff=85)
    if best:
        return next(r for r in rows if r.id == best[2])
    raise ToolError(f"No startup matches '{query}'. Use list_startups to see names and ids.")


def _potential(st: Startup) -> str:
    return (st.profile or {}).get("ai_engineer_potential") or st.ai_hiring_potential or "not rated"


def _draft_dict(d: OutreachDraft, startup_name: str, with_messages: bool = True) -> dict:
    out = {"draft_id": d.id, "startup": startup_name, "priority": d.priority,
           "name": d.name or f"(find on LinkedIn: {d.role})", "role": d.role, "found_in": d.source,
           "email": d.email, "email_confidence": d.email_confidence, "linkedin_search": d.linkedin_search,
           "why": d.why, "status": d.status, "warnings": d.warnings}
    if d.follow_up_due:
        out["follow_up_due"] = as_utc(d.follow_up_due).date().isoformat()
    if with_messages:
        out["messages"] = d.messages
    return out


# ---------- read-only tools (free) ----------

@tool("get_brief", "Today's morning brief on funded Australian startups likely to hire AI engineers: a short "
                   "summary by default, or the full Markdown brief with every profile and drafted message.",
      {"full": {"type": "boolean", "description": "return the whole brief instead of the summary", "default": False}})
def get_brief(full: bool = False) -> str:
    from jobsearch.intel.report import summary_text

    reports = sorted((DATA_DIR / "reports").glob("intel_*.md"))
    if full:
        if not reports:
            raise ToolError("No brief written yet; the morning run writes one at 06:00.")
        return f"{reports[-1].name}\n\n{reports[-1].read_text(encoding='utf-8')}"
    return summary_text() + (f"\n\nLatest brief file: {reports[-1].name}" if reports else "")


@tool("list_startups", "Recently funded startups being tracked, best fit first, with AI engineer potential, "
                       "fit score (0-100), status and how many outreach drafts exist.",
      {"status": {"type": "string", "enum": ["new", "profiled", "shortlisted", "dismissed"],
                  "description": "only this status (default: everything except dismissed)"},
       "limit": {"type": "integer", "default": 20}})
def list_startups(status: str | None = None, limit: int = 20) -> list[dict]:
    with session() as s:
        q = select(Startup).where(Startup.status == status) if status else select(Startup).where(Startup.status != "dismissed")
        rows = s.exec(q).all()
        drafts = s.exec(select(OutreachDraft.startup_id)).all()
    rows.sort(key=lambda r: (r.relevance_score or -1, as_utc(r.latest_announced) or as_utc(r.first_seen_at)), reverse=True)
    return [{"id": r.id, "name": r.name, "city": r.hq_city, "sector": r.sector,
             "round": " ".join(x for x in (r.latest_round, r.latest_amount_text) if x),
             "announced": as_utc(r.latest_announced).date().isoformat() if r.latest_announced else "",
             "ai_engineer_potential": _potential(r), "fit": r.relevance_score, "status": r.status,
             "profiled": bool(r.profile), "drafts": drafts.count(r.id)} for r in rows[:limit]]


@tool("get_startup", "Everything known about one startup: funding events, website, job board, the full profile "
                     "(vision, product, AI engineer potential, hiring signals, outreach angle) and sources.",
      STARTUP_ARG, ["startup"])
def get_startup(startup: str) -> dict:
    st = find_startup(startup)
    return {"id": st.id, "name": st.name, "status": st.status, "city": st.hq_city, "sector": st.sector,
            "website": st.website, "one_line": st.one_line, "ai_engineer_potential": _potential(st),
            "fit": st.relevance_score, "funding_events": st.events, "investors": st.investors,
            "job_board": st.careers, "profile": st.profile or "not profiled yet (use profile_startup)",
            "sources": st.sources}


@tool("get_drafts", "Outreach drafts (connection note, LinkedIn message, email, follow-up) waiting for you to "
                    "review and send yourself. Nothing is ever sent by this server.",
      {**{k: {**v, "description": "only this startup (optional)"} for k, v in STARTUP_ARG.items()},
       "status": {"type": "string", "enum": ["open", "sent", "all"], "default": "open",
                  "description": "open = pending review or approved"}})
def get_drafts(startup: str | None = None, status: str = "open") -> list[dict]:
    with session() as s:
        q = select(OutreachDraft).order_by(OutreachDraft.startup_id, OutreachDraft.priority)
        if startup:
            q = q.where(OutreachDraft.startup_id == find_startup(startup).id)
        if status == "open":
            q = q.where(OutreachDraft.status.in_(["pending_review", "approved"]))
        elif status == "sent":
            q = q.where(OutreachDraft.status == "sent")
        drafts = s.exec(q).all()
        names = {x.id: x.name for x in s.exec(select(Startup)).all()}
    return [_draft_dict(d, names.get(d.startup_id, "?")) for d in drafts]


@tool("follow_ups_due", "Messages you marked sent whose follow-up is now due, with the drafted follow-up text.")
def follow_ups_due() -> list[dict]:
    now = datetime.now(timezone.utc)
    with session() as s:
        drafts = s.exec(select(OutreachDraft).where(OutreachDraft.status == "sent",
                                                    OutreachDraft.follow_up_sent == False)).all()  # noqa: E712
        names = {x.id: x.name for x in s.exec(select(Startup)).all()}
    due = [d for d in drafts if d.follow_up_due and as_utc(d.follow_up_due) <= now]
    return [{**_draft_dict(d, names.get(d.startup_id, "?"), with_messages=False),
             "follow_up": d.messages.get("follow_up", "")} for d in due]


@tool("progress", "What the user has done so far and what's waiting: follow-ups due, drafts not yet sent "
                  "(grouped by startup, with their #draft numbers) and counts of sent, replied and skipped. The "
                  "morning check-in on Telegram shows the same thing.")
def progress() -> dict:
    from jobsearch import digest

    p = digest.progress()
    names = p["names"]
    return {
        "follow_ups_due": [{**_draft_dict(d, names.get(d.startup_id, "?"), with_messages=False),
                            "follow_up": d.messages.get("follow_up", "")} for d in p["follow_ups_due"]],
        "drafts_not_sent": [{"draft_id": d.id, "startup": names.get(d.startup_id, "?"),
                             "name": d.name or f"(find on LinkedIn: {d.role})", "role": d.role, "status": d.status}
                            for d in p["waiting"]],
        "totals": {k: p[k] for k in ("sent", "replied", "awaiting_reply", "skipped", "follow_ups_sent")},
    }


@tool("budget_status", "AI spend today and this month against your caps, and each agent's share of the daily cap.")
def budget_status() -> dict:
    b = preferences().get("budget", {})
    shares = b.get("per_agent_daily", {})
    return {"today_usd": round(llm.spend_today(), 3), "daily_cap_usd": b.get("daily_usd"),
            "month_usd": round(llm.spend_this_month(), 3), "monthly_cap_usd": b.get("monthly_usd"),
            "by_agent_today": {g: {"spent": round(llm.spend_today(g), 3), "share": cap} for g, cap in shares.items()}}


# ---------- free actions ----------

@tool("set_startup_status", "Shortlist a startup (it gets outreach drafted at the next morning run), dismiss it "
                            "(hidden from the brief), or move it back to profiled.",
      {**STARTUP_ARG, "status": {"type": "string", "enum": ["shortlisted", "dismissed", "profiled"]}},
      ["startup", "status"])
def set_startup_status(startup: str, status: str) -> str:
    if status not in ("shortlisted", "dismissed", "profiled"):
        raise ToolError("status must be shortlisted, dismissed or profiled")
    st = find_startup(startup)
    with session() as s:
        row = s.get(Startup, st.id)
        row.status = status
        s.add(row)
        s.commit()
    return f"{st.name} is now {status}."


@tool("update_draft", "Record what you did with a draft after sending it yourself: sent (starts the follow-up "
                      "timer), replied, skipped, approved, or follow_up_sent. This never sends anything. "
                      "\"sent #4\" in a reply to the morning message means draft_id 4.",
      {"draft_id": {"type": "integer", "description": "the #number shown in the morning brief or check-in"},
       "status": {"type": "string", "enum": ["approved", "sent", "replied", "skipped", "follow_up_sent"]}},
      ["draft_id", "status"])
def update_draft(draft_id: int, status: str) -> str:
    from jobsearch.outreach.pipeline import mark_follow_up_sent, mark_sent, set_draft_status

    with session() as s:
        d = s.get(OutreachDraft, int(draft_id))
    if d is None:
        raise ToolError(f"No draft with id {draft_id}. Use get_drafts to see ids.")
    if status == "sent":
        mark_sent(d.id)
    elif status == "follow_up_sent":
        mark_follow_up_sent(d.id)
    elif status in ("approved", "replied", "skipped"):
        set_draft_status(d.id, status)
    else:
        raise ToolError("status must be approved, sent, replied, skipped or follow_up_sent")
    return f"Draft {d.id} ({d.name or d.role}) marked {status}."


# ---------- paid actions (Opus; your daily and monthly caps apply) ----------

@tool("profile_startup", "Research one startup now with Opus (about $0.10): vision, product, AI engineer potential, "
                         "hiring signals, people named in the sources, fit and outreach angle. Takes about a minute.",
      STARTUP_ARG, ["startup"])
def profile_startup(startup: str) -> dict:
    from jobsearch.intel.pipeline import run_intel

    st = find_startup(startup)
    with llm.user_initiated():
        stats = run_intel(startup_id=st.id)
    if stats.get("budget_stop"):
        raise ToolError("Stopped by the budget cap; see budget_status.")
    if not stats.get("profiled"):
        raise ToolError(f"Profiling {st.name} failed; check data/logs/jobsearch.log.")
    return get_startup(str(st.id))


@tool("draft_outreach", "Find people at a profiled startup and draft messages to them with Opus (about $0.11): who "
                        "to contact first and why, plus a connection note, LinkedIn message, email and follow-up for "
                        "each. Drafts only: you review and send them yourself. Takes 1-2 minutes.",
      STARTUP_ARG, ["startup"])
def draft_outreach(startup: str) -> dict:
    from jobsearch.outreach.pipeline import run_outreach

    st = find_startup(startup)
    if not st.profile:
        raise ToolError(f"{st.name} isn't profiled yet; call profile_startup first.")
    with llm.user_initiated():
        stats = run_outreach(startup_id=st.id)
    if stats.get("budget_stop"):
        raise ToolError("Stopped by the budget cap; see budget_status.")
    if not stats.get("planned"):
        raise ToolError(f"Drafting for {st.name} failed; check data/logs/jobsearch.log.")
    with session() as s:
        plan = s.exec(select(OutreachPlan).where(OutreachPlan.startup_id == st.id)
                       .order_by(OutreachPlan.created_at.desc())).first()
        drafts = s.exec(select(OutreachDraft).where(OutreachDraft.plan_id == plan.id)
                        .order_by(OutreachDraft.priority)).all()
    return {"strategy": plan.strategy, "cost_usd": plan.cost_usd, "drafts": [_draft_dict(d, st.name) for d in drafts]}


# ---------- JSON-RPC over stdio ----------

def call_tool(name: str, arguments: dict) -> dict:
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
    try:
        # Anything a pipeline prints must not reach stdout, which carries the protocol
        with contextlib.redirect_stdout(sys.stderr):
            result = TOOLS[name]["fn"](**(arguments or {}))
    except (ToolError, llm.BudgetExceeded, llm.LLMError, TypeError) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:  # report, don't kill the server
        log.error("Tool %s failed: %s", name, traceback.format_exc())
        return {"content": [{"type": "text", "text": f"{name} failed: {e!r}"}], "isError": True}
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if msg_id is None:          # notifications/initialized, notifications/cancelled, ...
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                  "instructions": "Job search tools. Nothing is ever sent from here: show drafts to the user and "
                                  "let them send. profile_startup and draft_outreach cost money; confirm first. "
                                  "The user gets a morning brief or check-in that numbers drafts as #id; when they "
                                  "reply \"sent #4\", \"replied #1\" or \"skip Morgan\", record it with update_draft "
                                  "(for a startup name, update each of its open drafts from get_drafts) and confirm "
                                  "what you recorded. Use progress to answer \"what have I done so far?\"."}
    elif method == "tools/list":
        result = {"tools": [t["spec"] for t in TOOLS.values()]}
    elif method == "tools/call":
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()
