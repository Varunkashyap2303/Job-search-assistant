"""Overview: today at a glance and what needs you next."""

from datetime import datetime, timedelta, timezone

import streamlit as st
from sqlalchemy import func
from sqlmodel import select

from jobsearch import llm, persona
from jobsearch.config import local_tz, preferences
from jobsearch.db import Application, Job, OutreachDraft, ResumeVersion, Startup, as_utc, session
from jobsearch.ui import style


def _count(s, model, *where) -> int:
    return s.exec(select(func.count()).select_from(model).where(*where)).one()


def _greeting() -> str:
    hour = datetime.now(local_tz()).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    name = persona.first_name()
    return f"Good {part}{', ' + name if name else ''}"


def _trends(tz) -> tuple[list[int], list[float]]:
    """New jobs per day and spend per day over the last 7 days, for the metric sparklines."""
    from jobsearch.db import LlmUsage

    days = [datetime.now(tz).date() - timedelta(days=i) for i in range(6, -1, -1)]
    since = datetime.combine(days[0], datetime.min.time(), tz).astimezone(timezone.utc)
    with session() as s:
        seen = [as_utc(d).astimezone(tz).date() for d in s.exec(select(Job.first_seen_at).where(
            Job.first_seen_at >= since, Job.status != "filtered_out")).all()]
        usage = s.exec(select(LlmUsage.ts, LlmUsage.cost_usd).where(LlmUsage.ts >= since)).all()
    spend: dict = {}
    for ts, cost in usage:
        day = as_utc(ts).astimezone(tz).date()
        spend[day] = spend.get(day, 0) + cost
    return [seen.count(d) for d in days], [round(spend.get(d, 0), 3) for d in days]


def _status_chips(tz, now) -> list[tuple[str, str, str]]:
    """Agent health for the status strip: last run of each agent, budget use, jobs tracked."""
    from jobsearch.db import RunLog

    chips = []
    with session() as s:
        for kind, label, stale_hours in (("scout", "Scout", 3), ("intel", "Research", 26)):
            run = s.exec(select(RunLog).where(RunLog.kind == kind).order_by(RunLog.id.desc())).first()
            if run is None:
                chips.append(("warn", label, "never run"))
                continue
            started = as_utc(run.started_at)
            state = "err" if run.error else ("warn" if now - started > timedelta(hours=stale_hours) else "ok")
            chips.append((state, label, started.astimezone(tz).strftime("%a %H:%M") + (" · error" if run.error else "")))
        tracked = _count(s, Job, Job.status != "filtered_out")
    cap = preferences().get("budget", {}).get("daily_usd", 0) or 0
    used = llm.spend_today() / cap if cap else 0
    chips.append(("warn" if used > 0.8 else "ok", "Budget", f"{used:.0%} of today"))
    chips.append(("ok", "Tracking", f"{tracked} jobs"))
    return chips


def render(pages: dict) -> None:
    tz = local_tz()
    now = datetime.now(timezone.utc)
    style.header(_greeting(), datetime.now(tz).strftime("%A %d %B %Y"), kicker="Overview")
    style.status_strip(_status_chips(tz, now))

    setup = persona.is_set_up()
    if not all(setup.values()):
        with style.card("setup"):
            st.markdown("### :material/rocket_launch: Let's get you set up")
            steps = [
                ("api_key", "Add your Anthropic API key", "Job search → API keys", "search"),
                ("profile", "Upload your resume and check your profile", "My profile", "profile"),
                ("preferences", "Describe the jobs you want", "Job search", "search"),
            ]
            for key, label, where, page in steps:
                c1, c2 = st.columns([5, 1])
                done = setup[key]
                c1.markdown(f"{':material/check_circle:' if done else ':material/radio_button_unchecked:'} "
                            f"{'~~' + label + '~~' if done else '**' + label + '**'} · {where}")
                if not done and c2.button(":material/arrow_forward:", key=f"setup_{key}", width="stretch", help="Open"):
                    st.switch_page(pages[page])
            st.caption("Then click **Run scout now** in the sidebar.")

    with session() as s:
        new_24h = _count(s, Job, Job.first_seen_at >= now - timedelta(hours=24), Job.status != "filtered_out")
        shortlisted = _count(s, Job, Job.status == "shortlisted")
        to_review = _count(s, ResumeVersion, ResumeVersion.status == "pending_review")
        apps_open = _count(s, Application, Application.status.in_(["needs_input", "ready"]))
        needs_you = _count(s, Application, Application.status == "needs_input")
        drafts = _count(s, OutreachDraft, OutreachDraft.status == "pending_review")
        followups = [d for d in s.exec(select(OutreachDraft).where(OutreachDraft.status == "sent",
                                                                   OutreachDraft.follow_up_sent.is_(False))).all()
                     if d.follow_up_due and as_utc(d.follow_up_due) <= now]
        applied = _count(s, Job, Job.status == "applied")
        top_jobs = s.exec(select(Job).where(Job.fit_score.is_not(None), Job.status.in_(["scored", "shortlisted", "tailored"]),
                                            Job.first_seen_at >= now - timedelta(days=3))
                          .order_by(Job.fit_score.desc()).limit(5)).all()
        new_startups = s.exec(select(Startup).where(Startup.status.in_(["new", "profiled"]))
                              .order_by(Startup.first_seen_at.desc()).limit(4)).all()

    budget = preferences().get("budget", {})
    today = llm.spend_today()
    jobs_trend, spend_trend = _trends(tz)
    c = st.columns(5)
    c[0].metric("New jobs", new_24h, help="Found in the last 24 hours; chart shows the last 7 days", border=True, height="stretch",
                chart_data=jobs_trend, chart_type="bar")
    c[1].metric("To review", to_review, help="Tailored resumes waiting for you", border=True, height="stretch")
    c[2].metric("Applications", apps_open, f"{needs_you} need you" if needs_you else None, delta_color="off",
                delta_arrow="off", help="Prepared and not yet sent", border=True, height="stretch")
    c[3].metric("Applied", applied, border=True, height="stretch")
    c[4].metric("Spent today", f"\\${today:.2f}", f"of \\${budget.get('daily_usd', 0):.2f} cap", delta_color="off",
                delta_arrow="off", border=True, chart_data=spend_trend, chart_type="area")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("### Next up")
        actions = [
            (to_review, ":material/fact_check:", f"{to_review} tailored resume(s) to review", "approvals"),
            (needs_you, ":material/help:", f"{needs_you} application(s) have questions only you can answer", "applications"),
            (apps_open - needs_you, ":material/send:", f"{apps_open - needs_you} application(s) ready for you to submit", "applications"),
            (len(followups), ":material/schedule:", f"{len(followups)} outreach follow-up(s) due", "outreach"),
            (drafts, ":material/forum:", f"{drafts} outreach draft(s) to review", "outreach"),
            (shortlisted, ":material/bookmark:", f"{shortlisted} shortlisted job(s) waiting for a resume", "approvals"),
        ]
        shown = 0
        for n, icon, text, page in actions:
            if n <= 0:
                continue
            shown += 1
            with style.card(f"next-{shown}"):
                a, b = st.columns([5, 1], vertical_alignment="center")
                a.markdown(f"{icon} {text}")
                if b.button(":material/arrow_forward:", key=f"next_{page}_{shown}", width="stretch", help="Open"):
                    st.switch_page(pages[page])
        if not shown:
            st.success("You're all caught up. New matches will appear after the next scout run.", icon=":material/done_all:")

    with right:
        st.markdown("### Best new matches")
        if not top_jobs:
            st.caption("Nothing scored in the last 3 days yet.")
        for j in top_jobs:
            with style.card(f"match-{j.id}"):
                st.markdown(f"**{j.title}**  \n{j.company} · {j.city}")
                st.markdown(f":primary-badge[{j.fit_score}/100] {style.badge(j.status)}")
        if top_jobs and st.button("All jobs", icon=":material/arrow_forward:", key="ov_jobs"):
            st.switch_page(pages["jobs"])

        st.markdown("### Newly funded startups")
        if not new_startups:
            st.caption("The daily startup research hasn't found any yet.")
        for x in new_startups:
            amount = " ".join(filter(None, [x.latest_round, x.latest_amount_text])).replace("$", "\\$")
            st.markdown(f":material/rocket_launch: **{x.name}** · {x.hq_city or 'AU'}"
                        + (f" · {amount}" if amount else "")
                        + (f" · :primary-badge[{x.relevance_score}/100]" if x.relevance_score is not None else ""))
        if new_startups and st.button("All startups", icon=":material/arrow_forward:", key="ov_startups"):
            st.switch_page(pages["startups"])
