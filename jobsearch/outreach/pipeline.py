"""Outreach run: shortlisted startups -> contacts -> Opus-drafted messages awaiting your review.
Nothing is ever sent from here; you send each message yourself from the dashboard."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from jobsearch import llm
from jobsearch.config import local_tz, preferences
from jobsearch.db import OutreachDraft, OutreachPlan, RunLog, Startup, session, utcnow
from jobsearch.outreach import people
from jobsearch.outreach.compose import build_system_prompt, check_messages, compose
from jobsearch.tailor.facts import load_catalogue

log = logging.getLogger(__name__)


def draft_for(startup: Startup, cat, system_prompt: str) -> OutreachPlan:
    started = datetime.now(timezone.utc)
    cfg = preferences()["outreach"]
    discovered = people.discover(startup)
    plan_out = compose(startup, discovered, cat, system_prompt)

    by_id = {f"p{i}": c for i, c in enumerate(discovered["people"])}
    known = {c["name"].lower() for c in discovered["people"]}
    p = startup.profile or {}
    # Numbers from the rules themselves (e.g. "15-minute chat") are expected in messages too
    allowed_text = " ".join([*cat.facts.values(), str(p), *(s.get("title", "") for s in startup.sources), system_prompt])

    with session() as s:
        plan = OutreachPlan(startup_id=startup.id, model=preferences()["models"]["outreach"],
                            strategy=plan_out.strategy, discovered=discovered)
        s.add(plan)
        s.flush()
        for c in sorted(plan_out.contacts, key=lambda x: x.priority)[: cfg.get("max_contacts_per_startup", 3)]:
            found = by_id.get(c.contact_id, {})
            name = found.get("name", c.name)
            s.add(OutreachDraft(
                plan_id=plan.id,
                startup_id=startup.id,
                priority=c.priority,
                name=name,
                role=found.get("role", c.role),
                source=found.get("source", "role to find on LinkedIn" if not name else "model"),
                email=found.get("email", ""),
                email_confidence=found.get("email_confidence", "none"),
                linkedin_search=found.get("linkedin_search") or people.linkedin_people_search(c.role, startup.name),
                why=c.why,
                messages=c.model_dump(include={"connection_note", "linkedin_message", "email_subject", "email_body", "follow_up"}),
                warnings=check_messages(c, allowed_text, cfg.get("connection_note_chars", 200), known),
            ))
        plan.cost_usd = round(llm.spend_since(started), 4)
        s.add(plan)
        s.commit()
    return plan


def drafted_today() -> int:
    start = datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with session() as s:
        return len(s.exec(select(OutreachPlan.id).where(OutreachPlan.created_at >= start)).all())


def run_outreach(startup_id: int | None = None, limit: int | None = None) -> dict:
    cfg = preferences()["outreach"]
    stats: Counter = Counter()
    with session() as s:
        if startup_id is not None:
            queue = [s.get(Startup, startup_id)]
        else:
            planned = set(s.exec(select(OutreachPlan.startup_id)).all())
            shortlisted = s.exec(select(Startup).where(Startup.status == "shortlisted")).all()
            n = limit if limit is not None else max(0, cfg["max_startups_per_day"] - drafted_today())
            queue = sorted((x for x in shortlisted if x.id not in planned),
                           key=lambda x: x.relevance_score or 0, reverse=True)[:n]
        run = RunLog(kind="outreach")
        s.add(run)
        s.commit()

    if queue:
        cat = load_catalogue()
        system_prompt = build_system_prompt(cat)
        for st in queue:
            if not st.profile:
                stats["needs_profile_first"] += 1
                continue
            try:
                plan = draft_for(st, cat, system_prompt)
                stats["planned"] += 1
                log.info("Drafted outreach for %s ($%.3f)", st.name, plan.cost_usd)
            except llm.BudgetExceeded as e:
                log.warning("Stopping outreach: %s", e)
                stats["budget_stop"] += 1
                break
            except llm.LLMError as e:
                log.warning("Outreach failed for %s: %s", st.name, e)
                stats["failed"] += 1

    run.stats = dict(stats)
    run.finished_at = utcnow()
    with session() as s:
        s.add(run)
        s.commit()
    return dict(stats)


def mark_sent(draft_id: int) -> None:
    days = preferences()["outreach"].get("follow_up_days", 7)
    with session() as s:
        d = s.get(OutreachDraft, draft_id)
        d.status = "sent"
        d.sent_at = utcnow()
        d.follow_up_due = utcnow() + timedelta(days=days)
        s.add(d)
        s.commit()


def mark_follow_up_sent(draft_id: int) -> None:
    with session() as s:
        d = s.get(OutreachDraft, draft_id)
        d.follow_up_sent = True
        d.follow_up_due = None
        s.add(d)
        s.commit()


def set_draft_status(draft_id: int, status: str) -> None:
    with session() as s:
        d = s.get(OutreachDraft, draft_id)
        d.status = status
        if status in ("replied", "skipped"):
            d.follow_up_due = None
        s.add(d)
        s.commit()
