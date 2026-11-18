"""The morning brief: recently funded Australian startups likely to hire AI engineers, with their profile,
the people to contact and the drafted messages, as one Markdown file in data/reports/. Nothing is sent."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlmodel import select

from jobsearch.config import DATA_DIR, local_tz, preferences
from jobsearch.db import OutreachDraft, OutreachPlan, Startup, as_utc, session

REPORT_DIR = DATA_DIR / "reports"
POTENTIAL = {"high": "High", "medium": "Medium", "low": "Low"}
OPEN_DRAFT = {"pending_review", "approved"}


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _quote(text: str) -> list[str]:
    return [f"> {line}".rstrip() for line in (text or "").strip().splitlines()] or ["> (empty)"]


def _date(dt) -> str:
    return as_utc(dt).astimezone(local_tz()).date().isoformat() if dt else ""


def _round(st: Startup) -> str:
    return " ".join(x for x in (st.latest_round, st.latest_amount_text) if x) or "round n/a"


def _potential(st: Startup) -> str:
    p = (st.profile or {}).get("ai_engineer_potential") or st.ai_hiring_potential
    return POTENTIAL.get(p, "not rated")   # profiles written before the rating existed


def _latest_plans(startup_ids: list[int]) -> dict[int, tuple[OutreachPlan, list[OutreachDraft]]]:
    with session() as s:
        plans = s.exec(select(OutreachPlan).where(OutreachPlan.startup_id.in_(startup_ids))
                       .order_by(OutreachPlan.created_at)).all()
        latest = {p.startup_id: p for p in plans}
        drafts = s.exec(select(OutreachDraft).where(OutreachDraft.plan_id.in_([p.id for p in latest.values()]))
                        .order_by(OutreachDraft.priority)).all()
    by_plan: dict[int, list[OutreachDraft]] = {}
    for d in drafts:
        by_plan.setdefault(d.plan_id, []).append(d)
    return {sid: (p, by_plan.get(p.id, [])) for sid, p in latest.items()}


def _people(st: Startup, plan: tuple | None, note_days: int) -> list[str]:
    lines = ["### People to reach out to", ""]
    if plan is None:
        named = (st.profile or {}).get("people", [])
        if named:
            lines += ["No messages drafted yet. People named in the sources:", ""]
            lines += [f"- {p['name']}, {p['role']} ({p.get('source', 'news')})" for p in named]
        else:
            lines.append("No messages drafted yet, and no people are named in the sources.")
        return lines + [""]

    p, drafts = plan
    lines += [f"**Strategy.** {p.strategy}", "",
              "| # | Name | Role | Found in | Email | LinkedIn | Status |", "|-|-|-|-|-|-|-|"]
    for d in drafts:
        email = f"{d.email} ({d.email_confidence})" if d.email else "none found"
        lines.append(f"| {d.priority} | {_cell(d.name or 'find on LinkedIn')} | {_cell(d.role)} | {_cell(d.source)} | "
                     f"{_cell(email)} | [search]({d.linkedin_search}) | {d.status.replace('_', ' ')} |")
    lines.append("")

    for d in drafts:
        m = d.messages or {}
        who = f"{d.name}, {d.role}" if d.name else f"{d.role} (find the right person on LinkedIn first)"
        lines += [f"#### {d.priority}. {who}", "", d.why, ""]
        if d.status not in OPEN_DRAFT:
            lines += [f"Marked {d.status.replace('_', ' ')} on the dashboard; drafts not repeated here.", ""]
            continue
        note = m.get("connection_note", "")
        lines += [f"**LinkedIn connection note** ({len(note)} characters)", "", *_quote(note), "",
                  "**LinkedIn message**", "", *_quote(m.get("linkedin_message", "")), "",
                  f"**Email.** Subject: {m.get('email_subject', '')}", "", *_quote(m.get("email_body", "")), "",
                  f"**Follow-up** (if no reply after {note_days} days)", "", *_quote(m.get("follow_up", "")), ""]
        lines += [f"- Check: {w}" for w in d.warnings]
        if d.warnings:
            lines.append("")
    return lines


def _startup_section(i: int, st: Startup, is_new: bool, plan: tuple | None, note_days: int) -> list[str]:
    p = st.profile or {}
    careers = st.careers or {}
    lines = [f"## {i}. {st.name}{' · NEW' if is_new else ''}", "",
             f"**{st.hq_city or 'City n/a'} · {st.sector or 'sector n/a'} · {_round(st)} · "
             f"announced {_date(st.latest_announced) or 'n/a'} · fit {st.relevance_score}/100**", ""]
    links = [f"[Website]({st.website})"] if st.website else []
    if careers.get("ats"):
        links.append(f"Job board: {careers['ats']}, {careers.get('open_roles', 0)} open role(s)")
    if st.investors:
        links.append("Investors: " + ", ".join(st.investors))
    if links:
        lines += [" · ".join(links), ""]
    lines += [f"_{p.get('one_liner') or st.one_line}_", "",
              "### The company", "",
              f"**Vision.** {p.get('vision', '')}", "",
              f"**Product.** {p.get('product', '')}", "",
              f"**Customers and market.** {p.get('customers_and_market', '')}", "",
              f"**Business model.** {p.get('business_model', '')}", "",
              f"**Funding.** {p.get('funding_summary', '')}", "",
              f"### Potential for AI engineers: {_potential(st)}", ""]
    if p.get("ai_engineer_potential_reasons"):
        lines += [p["ai_engineer_potential_reasons"], ""]
    if p.get("ai_engineer_work"):
        lines += [f"**What you'd likely build.** {p['ai_engineer_work']}", ""]
    if p.get("likely_ai_roles"):
        lines += [f"**Likely roles.** {', '.join(p['likely_ai_roles'])}", ""]
    lines += [f"**How AI fits in ({p.get('ai_centricity', st.ai_centricity) or 'n/a'}).**", "",
              p.get("ai_implementation", ""), "",
              f"**Hiring signals.** {p.get('hiring_signals', '')}", "",
              f"**Why you.** {p.get('relevance_reasons', '')}", "",
              f"**Outreach angle.** {p.get('outreach_angle', '')}", ""]
    if p.get("unknowns"):
        lines += ["**Unknowns.** " + "; ".join(p["unknowns"]), ""]
    lines += _people(st, plan, note_days)
    if st.sources:
        lines += ["**Sources**", ""]
        lines += [f"- [{_cell(x.get('title'))}]({x.get('url')}) ({x.get('source')}, {x.get('date')})" for x in st.sources[:5]]
        lines.append("")
    return lines


@dataclass
class Brief:
    today: date
    days: int
    is_new: set[int]
    full: list[Startup]        # profiled and not rated low: full sections
    rest: list[Startup]        # not profiled yet, or rated low: one line each
    plans: dict[int, tuple[OutreachPlan, list[OutreachDraft]]]


def collect() -> Brief:
    days = preferences().get("intel", {}).get("lookback_days", 45)
    today = datetime.now(local_tz()).date()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session() as s:
        rows = s.exec(select(Startup).where(Startup.status != "dismissed")).all()
    rows = [r for r in rows if (as_utc(r.latest_announced) or as_utc(r.first_seen_at)) >= cutoff]
    is_new = {r.id for r in rows if as_utc(r.first_seen_at).astimezone(local_tz()).date() == today}
    full = [r for r in rows if r.profile and _potential(r) != "Low"]
    full.sort(key=lambda r: (r.relevance_score or 0, as_utc(r.latest_announced) or cutoff), reverse=True)
    full_ids = {r.id for r in full}
    rest = [r for r in rows if r.id not in full_ids]
    rest.sort(key=lambda r: as_utc(r.latest_announced) or as_utc(r.first_seen_at), reverse=True)
    return Brief(today, days, is_new, full, rest, _latest_plans([r.id for r in full]))


def _stopped_early(stats: dict) -> bool:
    return bool(stats.get("budget_stop") or stats.get("outreach.budget_stop"))


def summary_text(stats: Counter | dict | None = None, top: int = 5) -> str:
    """The short version for your phone (plain text, well under Telegram's 4096-character limit)."""
    stats = stats or {}
    b = collect()
    waiting = lambda r: sum(d.status in OPEN_DRAFT for d in b.plans[r.id][1]) if r.id in b.plans else 0
    lines = [f"Morning startup brief · {b.today.strftime('%a %d %b')}",
             f"{len(b.is_new)} new today · {len(b.full)} profiled · {len(b.rest)} more", ""]
    if b.full:
        lines.append("Top picks")
        for i, r in enumerate(b.full[:top], 1):
            lines += [f"{i}. {r.name}{' (new)' if r.id in b.is_new else ''} · {r.hq_city or 'city n/a'} · {_round(r)}",
                      f"   AI engineer potential: {_potential(r)} · fit {r.relevance_score} · {waiting(r)} draft(s) to review"]
        lines.append("")
    likely = [r for r in b.rest if _potential(r) in ("High", "Medium")]
    if likely:
        lines.append("Also funded, profile queued")
        lines += [f"• {r.name} · {_round(r)} · {_potential(r)}" for r in likely[:top]]
        if len(likely) > top:
            lines.append(f"…and {len(likely) - top} more")
        lines.append("")
    if _stopped_early(stats):
        lines += ["Stopped early at the budget cap; the rest wait for tomorrow.", ""]
    lines.append("Full brief with every draft attached. Nothing has been sent.")
    return "\n".join(lines)


def write_report(stats: Counter | dict | None = None) -> str:
    stats = stats or {}
    note_days = preferences().get("outreach", {}).get("follow_up_days", 7)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    b = collect()
    today, days, is_new, full, rest, plans = b.today, b.days, b.is_new, b.full, b.rest, b.plans

    lines = [f"# Morning startup brief: {today}", "",
             f"Australian startups funded in the last {days} days that are likely to hire AI engineers. "
             "Nothing has been sent: review the drafts, send them yourself, then mark them sent on the "
             "dashboard's Outreach page so follow-ups are tracked.", ""]
    if stats:
        lines += [f"This run: {stats.get('articles.new', 0)} new articles, {stats.get('articles.candidates', 0)} "
                  f"funding stories, {stats.get('startups.new', 0)} new startups, {stats.get('profiled', 0)} "
                  f"profiled, {stats.get('outreach.planned', 0)} outreach plans drafted"
                  + (" (stopped early at the budget cap)" if _stopped_early(stats) else "")
                  + ".", ""]
    lines += [f"{len(is_new)} new today · {len(full)} profiled · {len(rest)} more waiting or less likely to hire", ""]

    if full:
        lines += ["## At a glance", "",
                  "| # | Startup | City | Round | Announced | AI engineer potential | Fit | Contacts drafted |",
                  "|-|-|-|-|-|-|-|-|"]
        for i, r in enumerate(full, 1):
            drafted = len(plans[r.id][1]) if r.id in plans else 0
            lines.append(f"| {i} | {_cell(r.name)}{' (new)' if r.id in is_new else ''} | {_cell(r.hq_city)} | "
                         f"{_cell(_round(r))} | {_date(r.latest_announced)} | {_potential(r)} | "
                         f"{r.relevance_score} | {drafted} |")
        lines.append("")
        for i, r in enumerate(full, 1):
            lines += _startup_section(i, r, r.id in is_new, plans.get(r.id), note_days)
    else:
        lines += ["No profiled startups in this window yet.", ""]

    if rest:
        lines += ["## Other recent funding news", "",
                  "Not profiled yet (queued for a later morning) or rated unlikely to hire AI engineers.", ""]
        for r in rest:
            src = r.sources[0] if r.sources else {}
            link = f" [source]({src['url']})" if src.get("url") else ""
            state = "profiled" if r.profile else "not profiled yet"
            lines.append(f"- **{r.name}**{' (new)' if r.id in is_new else ''} ({r.hq_city or 'city n/a'}, "
                         f"{r.sector or 'sector n/a'}): {_round(r)}, {_date(r.latest_announced) or 'date n/a'}. "
                         f"{r.one_line} AI engineer potential: {_potential(r)} ({state}).{link}")
        lines.append("")

    path = REPORT_DIR / f"intel_{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
