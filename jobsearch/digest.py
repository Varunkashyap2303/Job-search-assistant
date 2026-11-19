"""The morning Telegram message, without repeats. Every message records which startups and drafts it showed.

- Something new (a newly profiled startup or new outreach drafts): a brief of only the new items, plus one
  line on what's still waiting. Newly found but unprofiled startups ride along as one line each.
- Nothing new: a check-in listing follow-ups due and every draft you haven't acted on, with your progress,
  so you can reply ("sent #4", "skip Morgan") and Hermes records it.
- Nothing new and nothing waiting: no message."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from jobsearch.config import local_tz, preferences
from jobsearch.db import OutreachDraft, Startup, TelegramMessage, as_utc, session

OPEN = ("pending_review", "approved")
LIKELY = ("high", "medium")
MAX_LINES = 12                  # per list, keeps a message well under Telegram's 4096 characters
REPLY_HINT = 'Reply here to record what you did, e.g. "sent #4", "replied #1" or "skip Morgan".'


@dataclass
class News:
    startups: list[Startup] = field(default_factory=list)     # newly profiled, worth a full entry
    drafts: list[OutreachDraft] = field(default_factory=list)  # open drafts not shown before
    queued: list[Startup] = field(default_factory=list)       # newly found, profile still queued

    def __bool__(self) -> bool:
        return bool(self.startups or self.drafts)


def seen() -> tuple[set[int], set[int]]:
    with session() as s:
        rows = s.exec(select(TelegramMessage)).all()
    return ({i for r in rows for i in r.startup_ids}, {i for r in rows for i in r.draft_ids})


def _potential(st: Startup) -> str:
    return ((st.profile or {}).get("ai_engineer_potential") or st.ai_hiring_potential or "").lower()


def _recent(st: Startup) -> bool:
    days = preferences().get("intel", {}).get("lookback_days", 45)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return (as_utc(st.latest_announced) or as_utc(st.first_seen_at)) >= cutoff


def _open_drafts(s) -> list[OutreachDraft]:
    dismissed = set(s.exec(select(Startup.id).where(Startup.status == "dismissed")).all())
    drafts = s.exec(select(OutreachDraft).where(OutreachDraft.status.in_(OPEN))
                    .order_by(OutreachDraft.startup_id, OutreachDraft.priority)).all()
    return [d for d in drafts if d.startup_id not in dismissed]


def whats_new() -> News:
    seen_startups, seen_drafts = seen()
    with session() as s:
        startups = [x for x in s.exec(select(Startup).where(Startup.status != "dismissed")).all() if _recent(x)]
        drafts = _open_drafts(s)
    new = News()
    new.drafts = [d for d in drafts if d.id not in seen_drafts]
    for st in sorted(startups, key=lambda x: x.relevance_score or 0, reverse=True):
        if st.id in seen_startups:
            continue
        if st.profile and _potential(st) != "low":       # includes profiles written before the rating existed
            new.startups.append(st)
        elif not st.profile and _potential(st) in LIKELY:
            new.queued.append(st)
    return new


def progress() -> dict:
    """Follow-ups due, drafts waiting for you, and what you've done so far."""
    now = datetime.now(timezone.utc)
    with session() as s:
        drafts = s.exec(select(OutreachDraft)).all()
        waiting = _open_drafts(s)
        names = {x.id: x.name for x in s.exec(select(Startup)).all()}
    due = [d for d in drafts if d.status == "sent" and not d.follow_up_sent
           and d.follow_up_due and as_utc(d.follow_up_due) <= now]
    return {
        "follow_ups_due": due,
        "waiting": waiting,
        "names": names,
        "sent": sum(d.status in ("sent", "replied") for d in drafts),
        "replied": sum(d.status == "replied" for d in drafts),
        "awaiting_reply": sum(d.status == "sent" for d in drafts),
        "skipped": sum(d.status == "skipped" for d in drafts),
        "follow_ups_sent": sum(d.follow_up_sent for d in drafts),
    }


def _who(d: OutreachDraft) -> str:
    return f"#{d.id} {d.name or d.role}" + (f" ({d.role})" if d.name and d.role else "")


def _round(st: Startup) -> str:
    return " ".join(x for x in (st.latest_round, st.latest_amount_text) if x) or "round n/a"


def _waiting_lines(waiting: list[OutreachDraft], names: dict[int, str]) -> list[str]:
    by_startup: dict[int, list[OutreachDraft]] = {}
    for d in waiting:
        by_startup.setdefault(d.startup_id, []).append(d)
    lines = [f"• {names.get(sid, '?')}: " + ", ".join(_who(d) for d in ds) for sid, ds in by_startup.items()]
    return lines[:MAX_LINES] + ([f"…and {len(lines) - MAX_LINES} more startups"] if len(lines) > MAX_LINES else [])


def _tally(p: dict) -> str:
    return (f"So far: {p['sent']} sent · {p['replied']} replied · {p['awaiting_reply']} awaiting reply · "
            f"{p['skipped']} skipped")


def _today() -> str:
    return datetime.now(local_tz()).strftime("%a %d %b")


def brief_text(new: News, p: dict | None = None) -> str:
    p = p or progress()
    names = p["names"]
    new_ids = {st.id for st in new.startups}
    drafts_by_startup: dict[int, list[OutreachDraft]] = {}
    for d in new.drafts:
        drafts_by_startup.setdefault(d.startup_id, []).append(d)

    lines = [f"Morning startup brief · {_today()}", "New since your last message", ""]
    for i, st in enumerate(new.startups[:MAX_LINES], 1):
        ds = drafts_by_startup.get(st.id, [])
        lines += [f"{i}. {st.name} · {st.hq_city or 'city n/a'} · {_round(st)}",
                  f"   AI engineer potential: {_potential(st).title() or 'n/a'} · fit {st.relevance_score}"
                  + (f" · drafts {', '.join(_who(d) for d in ds)}" if ds else " · no drafts yet")]
    older = [sid for sid in drafts_by_startup if sid not in new_ids]
    if older:
        lines += ["", "New drafts for startups you've seen"]
        lines += [f"• {names.get(sid, '?')}: " + ", ".join(_who(d) for d in drafts_by_startup[sid]) for sid in older]
    if new.queued:
        lines += ["", "Also funded, profile queued"]
        lines += [f"• {st.name} · {_round(st)} · {_potential(st).title()}" for st in new.queued[:MAX_LINES]]
    still = len([d for d in p["waiting"] if d.id not in {x.id for x in new.drafts}])
    due = len(p["follow_ups_due"])
    if still or due:
        lines += ["", f"Still waiting on you: {still} earlier draft(s) to send · {due} follow-up(s) due"]
    lines += ["", "Full brief with every draft attached. " + REPLY_HINT]
    return "\n".join(lines)


def checkin_text(p: dict | None = None) -> str | None:
    """None when there's nothing to follow up on (the morning stays silent)."""
    p = p or progress()
    if not (p["follow_ups_due"] or p["waiting"]):
        return None
    names = p["names"]
    lines = [f"Morning check-in · {_today()}", "No new startups or drafts today.", ""]
    if p["follow_ups_due"]:
        lines.append("Follow-ups due")
        for d in p["follow_ups_due"][:MAX_LINES]:
            sent = as_utc(d.sent_at).astimezone(local_tz()).strftime("%d %b") if d.sent_at else "earlier"
            lines.append(f"• {_who(d)} at {names.get(d.startup_id, '?')}: sent {sent}, no reply recorded")
        lines.append("")
    if p["waiting"]:
        lines += ["Drafts you haven't sent yet", *_waiting_lines(p["waiting"], names), ""]
    lines += [_tally(p), "", REPLY_HINT]
    return "\n".join(lines)


def morning_message() -> tuple[str, str, list[int], list[int]] | None:
    """(kind, text, startup_ids shown, draft_ids shown), or None for a silent morning."""
    new = whats_new()
    p = progress()
    if new:
        return ("brief", brief_text(new, p), [st.id for st in new.startups + new.queued], [d.id for d in new.drafts])
    text = checkin_text(p)
    return ("checkin", text, [], []) if text else None


def record(kind: str, text: str = "", startup_ids: list[int] | None = None, draft_ids: list[int] | None = None) -> None:
    with session() as s:
        s.add(TelegramMessage(kind=kind, text=text, startup_ids=startup_ids or [], draft_ids=draft_ids or []))
        s.commit()


def mark_all_seen() -> tuple[int, int]:
    """Treat everything that exists now as already shown (e.g. after the first full brief)."""
    new = whats_new()
    startup_ids = [st.id for st in new.startups + new.queued]
    draft_ids = [d.id for d in new.drafts]
    record("baseline", "", startup_ids, draft_ids)
    return len(startup_ids), len(draft_ids)
