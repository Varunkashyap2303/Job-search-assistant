"""Review dashboard. Launch with: python -m jobsearch dashboard"""

import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def reload_changed_project_modules() -> None:
    """Streamlit only re-imports modules that change *during* a live browser session. A page refresh
    starts a new session, so edited jobsearch modules stayed stale in memory (ImportErrors after
    updates). Compare source timestamps on every run and re-import the package if anything changed."""
    package = Path(__file__).resolve().parent.parent / "jobsearch"
    stamps = {str(p): p.stat().st_mtime_ns for p in package.rglob("*.py")}
    previous = getattr(sys, "_jobsearch_source_stamps", None)
    if previous is not None and previous != stamps:
        if "jobsearch.db" in sys.modules:
            # Table classes are about to be redefined; drop the old mappings first
            from sqlmodel import SQLModel
            from sqlmodel.main import default_registry

            default_registry.dispose()
            SQLModel.metadata.clear()
        for name in [n for n in sys.modules if n == "jobsearch" or n.startswith("jobsearch.")]:
            del sys.modules[name]
    sys._jobsearch_source_stamps = stamps


reload_changed_project_modules()

from jobsearch import llm, persona  # noqa: E402
from jobsearch.config import local_tz, preferences  # noqa: E402
from jobsearch.db import (Application, Job, LlmUsage, OutreachDraft, OutreachPlan, ResumeVersion, RunLog, Startup,  # noqa: E402
                          as_utc, session, utcnow)
from jobsearch.scout.filters import FRESHNESS_TIERS, freshness_tier  # noqa: E402
from jobsearch.tailor import gaps  # noqa: E402
from jobsearch.tailor.facts import load_catalogue  # noqa: E402

st.set_page_config(page_title="Job Search Swarm", page_icon=":material/work:", layout="wide")
from jobsearch.ui import style  # noqa: E402

style.apply()
prefs = preferences()
tz = local_tz()


def md(text) -> str:
    """Escape $ so Streamlit doesn't render dollar amounts ("$475m at $1.26b") as LaTeX."""
    return str(text or "").replace("$", "\\$")


def local(dt):
    dt = as_utc(dt)
    return dt.astimezone(tz).strftime("%d %b %H:%M") if dt else ""


def set_status(job_id: int, status: str) -> None:
    with session() as s:
        job = s.get(Job, job_id)
        job.status = status
        s.add(job)
        s.commit()


def review(version_id: int, decision: str) -> None:
    """decision: approve | reject"""
    with session() as s:
        v = s.get(ResumeVersion, version_id)
        job = s.get(Job, v.job_id)
        v.status = "approved" if decision == "approve" else "rejected"
        v.reviewed_at = utcnow()
        job.status = {"approve": "approved", "reject": "dismissed"}[decision]
        s.add(v)
        s.add(job)
        s.commit()


# ---------- sidebar: budget and runs (shown under the page navigation) ----------
def sidebar() -> None:
    style.brand()
    b = prefs["budget"]
    today, month = llm.spend_today(), llm.spend_this_month()
    with st.sidebar.container(border=True, key="card-budget"):
        st.markdown("**Budget**")
        st.caption(f"Today \\${today:.2f} of \\${b['daily_usd']:.2f}")
        st.progress(min(today / b["daily_usd"], 1.0) if b["daily_usd"] else 0.0)
        st.caption(f"This month \\${month:.2f} of \\${b['monthly_usd']:.2f}")
        st.progress(min(month / b["monthly_usd"], 1.0) if b["monthly_usd"] else 0.0)

    ready = all(persona.is_set_up().values())
    if st.sidebar.button("Run scout now", icon=":material/travel_explore:", width="stretch", disabled=not ready,
                         type="primary"):
        from jobsearch.scout.pipeline import run_scout

        with st.spinner("Fetching and scoring jobs..."):
            stats = run_scout()
        st.sidebar.success(f"Scored {stats.get('scored', 0)}, shortlisted {stats.get('shortlisted', 0)}")
    if st.sidebar.button("Run startup research", icon=":material/rocket_launch:", width="stretch", disabled=not ready):
        from jobsearch.intel.pipeline import run_intel

        with st.spinner("Reading funding news, researching startups..."):
            stats = run_intel()
        st.sidebar.success(f"{stats.get('startups.new', 0)} new startups, {stats.get('profiled', 0)} profiled")
    with session() as s:
        last = s.exec(select(RunLog).order_by(RunLog.id.desc()).limit(1)).first()
    if last:
        st.sidebar.caption(f"Last run {local(last.started_at)}{' · error' if last.error else ''}")


def page_profile() -> None:
    style.header("My profile", "The only source of truth the agents may use.")
    from jobsearch.ui import profile_tab

    profile_tab.render()


def page_search() -> None:
    style.header("Job search", "What you want, where to look, your budget and API keys.")
    from jobsearch.ui import search_tab

    search_tab.render()


# ---------- jobs ----------
def page_jobs() -> None:
    style.header('Jobs', 'Every job found, freshest first. Select one for details.')
    with session() as s:
        jobs = s.exec(select(Job)).all()
    if not jobs:
        st.info("No jobs yet. Run the scout from the sidebar.")
    else:
        now = datetime.now(timezone.utc)
        rows = [{
            "id": j.id,
            "fresh": freshness_tier(as_utc(j.posted_at), now),
            "score": j.fit_score,
            "rec": j.recommendation,
            "title": j.title,
            "company": j.company,
            "city": j.city,
            "posted": local(j.posted_at),
            "status": j.status,
            "rights": j.work_rights,
            "seniority": j.seniority_fit,
            "sources": ", ".join(sorted({src["source"] for src in j.sources})),
            "url": j.url,
        } for j in jobs]
        df = pd.DataFrame(rows)

        c1, c2, c3, c4 = st.columns([2, 2, 1, 2])
        statuses = c1.multiselect("Status", sorted(df.status.unique()),
                                  default=[x for x in ("shortlisted", "scored", "new") if x in set(df.status)])
        tiers = c2.multiselect("Freshness", list(FRESHNESS_TIERS), default=list(FRESHNESS_TIERS),
                               format_func=lambda t: f"{t}: {FRESHNESS_TIERS[t]}")
        min_score = c3.number_input("Min score", 0, 100, 0, step=5)
        cities = c4.multiselect("City", sorted(df.city.unique()))

        view = df[df.status.isin(statuses) & df.fresh.isin(tiers)]
        if min_score:
            view = view[view.score.fillna(-1) >= min_score]
        if cities:
            view = view[view.city.isin(cities)]
        view = view.sort_values(["fresh", "score"], ascending=[True, False], na_position="last")

        st.caption(f"{len(view)} of {len(df)} jobs. Newest (tier A) first, then by fit score.")
        event = st.dataframe(
            view,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "id": None,
                "url": st.column_config.LinkColumn("link", display_text="open"),
                "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d"),
            },
        )

        if event.selection.rows:
            job_id = int(view.iloc[event.selection.rows[0]]["id"])
            with session() as s:
                job = s.get(Job, job_id)
            st.divider()
            st.subheader(f"{job.title} · {job.company}")
            st.caption(f"{job.city} · posted {local(job.posted_at)} · {md(job.salary_text) or 'salary not stated'} · "
                       f"status: {job.status}{' · ' + job.filter_reason if job.filter_reason else ''}")
            a = job.assessment or {}
            if a:
                st.write(md(a.get("summary", "")))
                left, right = st.columns(2)
                left.markdown("**Matched strengths**\n" + "\n".join(f"- {x}" for x in a.get("matched_strengths", [])))
                right.markdown("**Gaps**\n" + "\n".join(f"- {x}" for x in a.get("gaps", [])))
                left.markdown("**Must-have skills**\n" + "\n".join(f"- {x}" for x in a.get("must_have_skills", [])))
                right.markdown("**ATS keywords**\n" + ", ".join(a.get("ats_keywords", [])))
            b1, b2, b3, b4, b5 = st.columns(5)
            b1.link_button("Open posting", job.url, width="stretch")
            if b5.button("Tailor resume now", width="stretch", disabled=job.status in ("tailored", "approved")):
                from jobsearch.tailor.pipeline import run_tailor

                with st.spinner("Tailoring with Opus, verifying, rendering..."), llm.user_initiated():
                    res = run_tailor(job_id=job.id)
                if res.get("tailored"):
                    st.success("Done. Review it on the Approvals page.")
                else:
                    st.error(f"Tailoring didn't complete: {res}")
            if b2.button("Shortlist", width="stretch", disabled=job.status == "shortlisted"):
                set_status(job.id, "shortlisted")
                st.rerun()
            if b3.button("Dismiss", width="stretch", disabled=job.status == "dismissed"):
                set_status(job.id, "dismissed")
                st.rerun()
            if b4.button("Re-score next run", width="stretch"):
                set_status(job.id, "new")
                st.rerun()
            with st.expander("Full description"):
                st.text(job.description or "(none)")

# ---------- approvals ----------
def page_approvals() -> None:
    style.header('Approvals', 'Tailored resumes waiting for you. Nothing is sent without your OK.')
    with session() as s:
        pending = s.exec(select(ResumeVersion).where(ResumeVersion.status == "pending_review")
                         .order_by(ResumeVersion.created_at.desc())).all()
        approved = s.exec(select(ResumeVersion).where(ResumeVersion.status == "approved")
                          .order_by(ResumeVersion.reviewed_at.desc()).limit(20)).all()
        jobs_by_id = {j.id: j for j in s.exec(select(Job).where(Job.id.in_([v.job_id for v in pending + approved]))).all()}

    with session() as s:
        n_outreach = len(s.exec(select(OutreachDraft.id).where(OutreachDraft.status == "pending_review")).all())
    if n_outreach:
        st.info(f"{n_outreach} outreach draft(s) are waiting for review on the Outreach page.")
    if not pending:
        st.info("Nothing waiting for review.")
    else:
        catalogue = load_catalogue()
    for v in pending:
        job = jobs_by_id[v.job_id]
        with st.expander(f"**{job.title}** · {job.company} · :primary-badge[{job.fit_score if job.fit_score is not None else '–'}/100] · posted {local(job.posted_at)}",
                         expanded=len(pending) == 1):
            left, right = st.columns([3, 2])
            preview = Path(v.resume_pdf).parent / "preview.png"
            if preview.exists():
                left.image(str(preview), width="stretch")
            content = v.content or {}
            right.markdown("**Why it's tailored this way**")
            right.write(md(content.get("tailoring_notes", "")))
            cov = pd.DataFrame(v.coverage)
            if not cov.empty:
                right.markdown("**Keyword coverage**")
                right.dataframe(cov, hide_index=True, width="stretch")
            if v.verification:
                right.markdown(f"**Fact-checker changes ({len(v.verification)})**")
                for item in v.verification:
                    was = f"  \n  _was:_ {item['original']}" if item.get("original") else ""
                    right.markdown(f"- `{item['where']}` {item['action']}: {md(item['reason'])}{md(was)}")
            else:
                right.success("Fact-checker: every claim is supported by your profile.")
            hz = v.humanizer or {}
            if hz.get("changes") is not None and not hz.get("skipped"):
                before, after = len(hz.get("tells_before", [])), len(hz.get("tells_after", []))
                right.markdown(f"**Humanizer** · AI tells {before} → {after} · {hz.get('lines_changed', 0)} line(s) rewritten")
                if hz.get("changes"):
                    right.dataframe(pd.DataFrame(hz["changes"]).rename(
                        columns={"pass_name": "pass", "what_changed": "what changed"}), hide_index=True, width="stretch")
                if hz.get("tells_after"):
                    right.caption("Still flagged: " + md(", ".join(hz["tells_after"])))
                for rv in hz.get("reverted", []):
                    right.caption(md(f"Reverted `{rv['where']}` to the fact-checked version ({rv['reason']}): "
                                     f"{rv['attempt']}"))
            else:
                why = hz.get("skipped", "made before the humanizer existed")
                right.warning(md(f"Not humanized yet ({why})."))
                if right.button("Humanize this version (~$0.05)", key=f"hz{v.id}"):
                    from jobsearch.tailor.pipeline import humanize_version

                    with st.spinner("Humanizing, re-checking facts, re-rendering..."), llm.user_initiated():
                        try:
                            humanize_version(v.id)
                            st.rerun()
                        except (llm.BudgetExceeded, llm.LLMError) as e:
                            st.error(md(str(e)))
            if v.trims:
                right.caption("Trimmed to fit one page: " + "; ".join(v.trims))
            right.caption(f"Cost ${v.cost_usd:.3f} · {v.model}")
            if content.get("cover_letter"):
                with right.popover("Cover letter"):
                    for para in content["cover_letter"]:
                        st.write(md(para["text"]))
            gap_list = gaps.addressable_gaps(v.coverage, job.assessment or {})
            unused = gaps.unused_keywords(v.coverage)
            # Expanders can't nest, so the gap form sits behind a toggle inside the card
            if st.toggle(f"Close the gaps ({len(gap_list)} gap(s), {len(unused)} unused keyword(s))", key=f"gtog{v.id}"):
                st.caption("For each gap you actually have experience with, pick where it belongs and describe it in "
                           "one truthful sentence (real numbers if you have them). It's saved to your profile "
                           "(config/profile_additions.yaml), so every future resume can use it too. "
                           "Leave anything you don't have as-is: interviewers will ask.")
                gap_targets = gaps.targets(catalogue)
                options = ["Leave it"] + [t.label for t in gap_targets]
                by_label = {t.label: t for t in gap_targets}
                with st.form(key=f"gapform{v.id}"):
                    rows = []
                    for i, g in enumerate([*gap_list, ""]):
                        if g:
                            st.markdown(f"**{md(g)}**")
                        else:
                            st.markdown("**Something else the resume should show**")
                        g1, g2 = st.columns([1, 2])
                        choice = g1.selectbox("Add to", options, key=f"gt{v.id}_{i}", label_visibility="collapsed")
                        text = g2.text_input(
                            "What you did", key=f"gx{v.id}_{i}", label_visibility="collapsed",
                            placeholder="Skill name, or one sentence of what you did (e.g. 'Cut p95 latency 40% by ...')")
                        rows.append((choice, text, g))
                    must = st.multiselect("Already in your profile: make sure the resume mentions", unused,
                                          key=f"gm{v.id}") if unused else []
                    s1, s2 = st.columns(2)
                    regen = s1.form_submit_button("Save to profile & regenerate this resume", type="primary",
                                                  width="stretch")
                    save_only = s2.form_submit_button("Save to profile only", width="stretch")
                if regen or save_only:
                    chosen = [(by_label[c], t, g) for c, t, g in rows if c != "Leave it"]
                    missing = [g or "something else" for c, t, g in rows
                               if c != "Leave it" and by_label[c].kind == "fact" and not t.strip()]
                    if missing:
                        st.error("Describe what you did for: " + ", ".join(missing))
                    else:
                        added = gaps.apply_additions(chosen)
                        if added:
                            st.success("Added to your profile: " + "; ".join(added))
                        if regen and (added or must):
                            from jobsearch.tailor.pipeline import run_tailor

                            with st.spinner("Re-tailoring with Opus, fact-checking, rendering..."), llm.user_initiated():
                                res = run_tailor(job_id=job.id, guidance=gaps.build_guidance(must, added))
                            if res.get("tailored"):
                                st.rerun()
                            st.error(f"Regeneration didn't complete: {res}")
                        elif regen:
                            st.info("Nothing to change: choose where a gap belongs, or pick keywords to mention.")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.link_button("Open posting", job.url, width="stretch", icon=":material/open_in_new:")
            with open(v.resume_pdf, "rb") as f:
                c2.download_button("Resume PDF", f.read(), file_name=Path(v.resume_pdf).name,
                                   mime="application/pdf", key=f"r{v.id}", width="stretch", icon=":material/download:")
            if v.cover_letter_pdf and Path(v.cover_letter_pdf).exists():
                with open(v.cover_letter_pdf, "rb") as f:
                    c3.download_button("Cover letter PDF", f.read(), file_name=Path(v.cover_letter_pdf).name,
                                       mime="application/pdf", key=f"c{v.id}", width="stretch", icon=":material/download:")
            if c4.button("Approve", key=f"a{v.id}", type="primary", width="stretch", icon=":material/check_circle:"):
                review(v.id, "approve")
                from jobsearch.apply.pipeline import prepare

                with st.spinner("Reading the application form and drafting answers..."), llm.user_initiated():
                    try:
                        prepare(v.id)
                    except Exception as e:  # the resume stays approved; prepare again from the list below
                        st.error(md(f"Couldn't prepare the application: {e}"))
                st.rerun()
            if c5.button("Regenerate", key=f"g{v.id}", width="stretch", icon=":material/refresh:", help="Re-tailor now (~$0.15)"):
                # Regenerate now rather than queueing for the next scheduled run. The new version replaces
                # this one only once it succeeds, so a failure (e.g. budget) leaves this card in place.
                from jobsearch.tailor.pipeline import run_tailor

                with st.spinner("Re-tailoring with Opus, fact-checking, humanizing, rendering..."), llm.user_initiated():
                    res = run_tailor(job_id=job.id)
                if res.get("tailored"):
                    st.rerun()
                st.error(md(f"Regeneration didn't complete: {res}. This version is unchanged."))
            if st.button("Reject and skip this job", key=f"x{v.id}", icon=":material/close:", type="tertiary"):
                review(v.id, "reject")
                st.rerun()

    with session() as s:
        queued = s.exec(select(Job).where(Job.status == "shortlisted")
                        .order_by(Job.posted_at.desc().nulls_last())).all()
    if queued:
        st.subheader(f"Waiting to be tailored ({len(queued)})")
        st.caption("Shortlisted jobs without a resume yet. Scheduled runs tailor them newest first within the "
                   "daily budget, or tailor one now.")
        for qj in queued:
            q1, q2 = st.columns([5, 1])
            q1.markdown(md(f"**{qj.title}** · {qj.company} · score {qj.fit_score} · posted {local(qj.posted_at)}"))
            if q2.button("Tailor now", key=f"tq{qj.id}", width="stretch"):
                from jobsearch.tailor.pipeline import run_tailor

                with st.spinner("Tailoring with Opus, fact-checking, humanizing, rendering..."), llm.user_initiated():
                    res = run_tailor(job_id=qj.id)
                if res.get("tailored"):
                    st.rerun()
                st.error(md(f"Tailoring didn't complete: {res}"))

    if approved:
        st.subheader("Approved, ready to apply")
        for v in approved:
            job = jobs_by_id[v.job_id]
            c1, c2 = st.columns([5, 1])
            c1.markdown(md(f"**{job.title}** · {job.company} · approved {local(v.reviewed_at)} · status: {job.status}"))
            with session() as s:
                has_app = s.exec(select(Application.id).where(Application.resume_version_id == v.id)).first()
            if has_app:
                c2.caption("See Applications")
            elif c2.button("Prepare application", key=f"pa{v.id}", width="stretch"):
                from jobsearch.apply.pipeline import prepare

                with st.spinner("Reading the application form and drafting answers..."), llm.user_initiated():
                    prepare(v.id)
                st.rerun()


# ---------- applications ----------
ATS_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}


def answer_widget(f: dict, key: str):
    label = ("⚠️ " if f.get("needs_you") else "") + f["label"][:180] + (" *" if f.get("required") else "")
    help_text = md(f.get("note") or "") or None
    t, ans = f["type"], f.get("answer") or ""
    if t == "file":
        st.caption(f"{f['label']}: {Path(ans).name if ans else 'not attached'}")
        return None
    if t in ("select", "radio", "boolean"):
        opts = [""] + f.get("options", [])
        return st.selectbox(label, opts, index=opts.index(ans) if ans in opts else 0, key=key, help=help_text)
    if t in ("multiselect", "checkbox"):
        current = [a.strip() for a in ans.split("|") if a.strip() in f.get("options", [])] if ans else []
        return st.multiselect(label, f.get("options", []), default=current, key=key, help=help_text)
    if t == "textarea":
        return st.text_area(label, ans, key=key, help=help_text, height=140)
    return st.text_input(label, ans, key=key, help=help_text)


def page_applications() -> None:
    style.header('Applications', 'Drafted answers and pre-filled forms. You always click Submit.')
    from jobsearch.apply import pipeline as apply_pipeline

    with session() as s:
        apps = s.exec(select(Application).order_by(Application.created_at.desc())).all()
        app_jobs = {j.id: j for j in s.exec(select(Job).where(Job.id.in_([a.job_id for a in apps]))).all()}
        app_versions = {v.id: v for v in s.exec(select(ResumeVersion).where(
            ResumeVersion.id.in_([a.resume_version_id for a in apps]))).all()}

    st.caption("The agent reads each application form, drafts every answer from your profile, and fills the form "
               "for you. You always click Submit yourself. Approving a resume prepares its application here.")
    if not apps:
        st.info("No applications yet. Approve a tailored resume on the Approvals page to prepare one.")
    show_done = st.toggle("Show applied", value=False, key="apps_show_done")
    for a in apps:
        if a.status == "applied" and not show_done:
            continue
        job, ver = app_jobs[a.job_id], app_versions.get(a.resume_version_id)
        blockers = apply_pipeline.blocking_fields(a.fields)
        with style.card(f"app-{a.id}"):
            how = f"pre-fill via {ATS_NAMES.get(a.ats, a.ats)}" if a.mode == "prefill" else "guided (you apply on their site)"
            st.markdown(md(f"#### {job.title} · {job.company}"))
            st.markdown(f"{style.badge(a.status)} :gray-badge[{how}]")
            if a.status == "applied":
                st.success(f"Applied {local(a.submitted_at)}.")
                continue
            if blockers:
                st.warning(f"{len(blockers)} question(s) need you: " + md(", ".join(f["label"][:50] for f in blockers)))
            groups = [("Needs you", [f for f in a.fields if f in blockers]),
                      ("Questions", [f for f in a.fields if f not in blockers and f["section"] == "custom"]),
                      ("Your details", [f for f in a.fields if f not in blockers and f["section"] == "standard"]),
                      ("Demographic questions (declined by default)", [f for f in a.fields if f not in blockers and f["section"] == "eeo"])]
            with st.form(key=f"appform{a.id}"):
                edits = {}
                for title, group in groups:
                    if not group:
                        continue
                    if title.startswith("Demographic"):
                        with st.expander(f"{title} ({len(group)})"):
                            for f in group:
                                edits[f["key"]] = answer_widget(f, f"af{a.id}_{f['key']}")
                        continue
                    st.markdown(f"**{title}**")
                    for f in group:
                        edits[f["key"]] = answer_widget(f, f"af{a.id}_{f['key']}")
                if st.form_submit_button("Save answers", type="primary"):
                    apply_pipeline.save_answers(a.id, {k: v for k, v in edits.items() if v is not None})
                    st.rerun()

            b1, b2, b3, b4 = st.columns(4)
            b1.link_button("Application page", a.apply_url or job.url, width="stretch", icon=":material/open_in_new:")
            if ver:
                with open(ver.resume_pdf, "rb") as fh:
                    b2.download_button("Resume PDF", fh.read(), file_name=Path(ver.resume_pdf).name,
                                       mime="application/pdf", key=f"ar{a.id}", width="stretch")
            if a.mode == "prefill":
                if b3.button("Preview fill", key=f"ap{a.id}", width="stretch", icon=":material/preview:", help="Fills the form in a hidden browser and screenshots it. Never submits."):
                    with st.spinner("Filling the form in a hidden browser and taking a screenshot..."):
                        res = apply_pipeline.run_preview(a.id)
                    st.rerun()
                if b4.button("Open pre-filled", key=f"ao{a.id}", width="stretch", type="primary", icon=":material/open_in_browser:",
                             disabled=bool(blockers), help="Fills the real form in a visible window. You check it "
                             "and click Submit yourself, then close the window."):
                    with st.spinner("A browser window is open with the form filled in. Review it, click Submit there, then close the window."):
                        res = apply_pipeline.run_prefill(a.id)
                    if res.problems:
                        st.warning(md("Couldn't fill: " + "; ".join(res.problems)))
                    st.info(res.note)
            else:
                b3.caption("Copy the answers below into the employer's form.")
            if st.button("Mark applied", key=f"am{a.id}", icon=":material/done_all:"):
                apply_pipeline.mark_applied(a.id)
                st.rerun()

            if a.mode == "guided":
                with st.expander("Copy-paste answers"):
                    for f in a.fields:
                        st.markdown(md(f"**{f['label']}**"))
                        st.code(f.get("answer") or "(needs you)", language=None, wrap_lines=True)
            if a.preview_png and Path(a.preview_png).exists():
                with st.expander("Preview screenshot (form filled, not submitted)"):
                    st.caption(md(a.result_note))
                    st.image(a.preview_png)


# ---------- startups ----------
def section(title: str, body: str) -> str:
    return f"**{title}**" + chr(10) * 2 + md(body)


def bullets(title: str, items: list[str]) -> str:
    return f"**{title}**" + chr(10) + chr(10).join(f"- {md(i)}" for i in items)


def set_startup_status(startup_id: int, status: str) -> None:
    with session() as s:
        row = s.get(Startup, startup_id)
        row.status = status
        s.add(row)
        s.commit()


def page_startups() -> None:
    style.header('Startups', 'Newly funded startups in your field, researched for you.')
    with session() as s:
        startups = s.exec(select(Startup)).all()
    if not startups:
        st.info("No startups yet. Click Run startup research in the sidebar (it also runs daily at 06:00).")
    else:
        today_local = datetime.now(tz).date()
        srows = [{
            "id": x.id,
            "new": "●" if as_utc(x.first_seen_at).astimezone(tz).date() == today_local else "",
            "relevance": x.relevance_score,
            "startup": x.name,
            "city": x.hq_city,
            "sector": x.sector,
            "round": x.latest_round,
            "amount": x.latest_amount_text,
            "announced": as_utc(x.latest_announced).astimezone(tz).date() if x.latest_announced else None,
            "AI": x.ai_centricity,
            "open roles": (x.careers or {}).get("open_roles", 0),
            "status": x.status,
        } for x in startups]
        sdf = pd.DataFrame(srows)
        c1, c2 = st.columns([3, 2])
        sstatus = c1.multiselect("Status", sorted(sdf.status.unique()),
                                 default=[x for x in ("new", "profiled", "shortlisted") if x in set(sdf.status)],
                                 key="startup_status")
        days = c2.slider("Announced within (days)", 7, 180, 90)
        cutoff_day = today_local - timedelta(days=days)
        sview = sdf[sdf.status.isin(sstatus) & sdf.announced.apply(lambda d: d is None or d >= cutoff_day)]
        sview = sview.sort_values(["relevance", "announced"], ascending=[False, False], na_position="last")
        st.caption(f"{len(sview)} startups · {int((sdf.new == '●').sum())} new today · profiled ones sorted by relevance to you")
        sev = st.dataframe(
            sview, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
            column_config={"id": None,
                           "relevance": st.column_config.ProgressColumn("relevance", min_value=0, max_value=100, format="%d")},
        )
        if sev.selection.rows:
            sid = int(sview.iloc[sev.selection.rows[0]]["id"])
            with session() as s:
                x = s.get(Startup, sid)
            st.divider()
            st.subheader(x.name)
            st.caption(md(" · ".join(filter(None, [x.hq_city, x.sector, f"{x.latest_round} {x.latest_amount_text}".strip(),
                                                f"investors: {', '.join(x.investors)}" if x.investors else "", x.website]))))
            p = x.profile or {}
            if p:
                st.markdown(f"**{md(p['one_liner'])}**")
                a, b = st.columns(2)
                a.markdown(section("Vision", p["vision"]))
                a.markdown(section("Product", p["product"]))
                a.markdown(section("Customers & market", p["customers_and_market"]))
                a.markdown(section("Business model", p["business_model"]))
                a.markdown(section("Funding", p["funding_summary"]))
                b.markdown(section(f"How AI fits in ({p['ai_centricity']})", p["ai_implementation"]))
                b.markdown(bullets("Evidence", p.get("ai_evidence", [])))
                b.markdown(bullets("Likely AI roles", p.get("likely_ai_roles", [])))
                b.markdown(section("Hiring signals", p["hiring_signals"]))
                st.markdown(f"**Relevance to you: {p['relevance_score']}/100.** {md(p['relevance_reasons'])}")
                st.markdown(f"**Outreach angle.** {md(p['outreach_angle'])}")
                if p.get("people"):
                    st.markdown(bullets("People named in sources",
                                        [f"{pp['name']}, {pp['role']} ({pp['source']})" for pp in p["people"]]))
                if p.get("unknowns"):
                    st.caption("Unknowns: " + "; ".join(p["unknowns"]))
            else:
                st.write(md(x.one_line or "Not profiled yet."))
            with st.expander(f"Sources ({len(x.sources)}) and job board"):
                for src in x.sources:
                    st.markdown(f"- [{md(src['title'])}]({src['url']}) · {src['source']} · {src['date']}")
                roles = (x.careers or {}).get("roles", [])
                if roles:
                    st.markdown(bullets(f"Open roles on {x.careers['ats']}", roles))
            d1, d2, d3, d4, d5 = st.columns(5)
            if x.website:
                d1.link_button("Website", x.website, width="stretch")
            if d5.button("Draft outreach (Opus)", width="stretch", disabled=not p):
                from jobsearch.outreach.pipeline import run_outreach

                if x.status != "shortlisted":
                    set_startup_status(x.id, "shortlisted")
                with st.spinner("Finding people and drafting messages..."), llm.user_initiated():
                    res = run_outreach(startup_id=x.id)
                st.success("Drafted. See the Outreach page.") if res.get("planned") else st.error(f"Didn't complete: {res}")
            if d2.button("Shortlist for outreach", width="stretch", disabled=x.status == "shortlisted"):
                set_startup_status(x.id, "shortlisted")
                st.rerun()
            if d3.button("Dismiss", key="dismiss_startup", width="stretch", disabled=x.status == "dismissed"):
                set_startup_status(x.id, "dismissed")
                st.rerun()
            if d4.button("Re-profile" if p else "Profile now (Opus)", width="stretch"):
                from jobsearch.intel.pipeline import run_intel

                with st.spinner("Profiling with Opus..."), llm.user_initiated():
                    res = run_intel(startup_id=x.id)
                st.success("Done.") if res.get("profiled") else st.error(f"Didn't complete: {res}")
                st.rerun()

# ---------- outreach ----------
def gmail_compose_url(to: str, subject: str, body: str) -> str:
    """Opens a pre-filled Gmail draft in your browser. Nothing is sent until you press Send there."""
    q = urllib.parse.urlencode({"view": "cm", "fs": "1", "to": to, "su": subject, "body": body},
                               quote_via=urllib.parse.quote)
    return f"https://mail.google.com/mail/?{q}"


def page_outreach() -> None:
    style.header('Outreach', 'Messages drafted for you to send yourself.')
    from jobsearch.outreach.pipeline import mark_follow_up_sent, mark_sent, set_draft_status

    with session() as s:
        drafts = s.exec(select(OutreachDraft).order_by(OutreachDraft.startup_id, OutreachDraft.priority)).all()
        plans = {p.id: p for p in s.exec(select(OutreachPlan)).all()}
        names = {x.id: x.name for x in s.exec(select(Startup)).all()}

    st.caption("Drafts only: nothing is sent from here. Copy a message, or open a pre-filled Gmail draft, "
               "send it yourself, then click Mark sent so follow-ups are tracked.")
    now_utc = datetime.now(timezone.utc)
    due = [d for d in drafts if d.status == "sent" and not d.follow_up_sent
           and d.follow_up_due and as_utc(d.follow_up_due) <= now_utc]
    if due:
        st.warning(f"{len(due)} follow-up(s) due")
        for d in due:
            with style.card(f"followup-{d.id}"):
                st.markdown(f"**{d.name or d.role}** at {names.get(d.startup_id)} · sent {local(d.sent_at)}")
                st.code(d.messages.get("follow_up", ""), language=None, wrap_lines=True)
                if st.button("Follow-up sent", key=f"fu{d.id}"):
                    mark_follow_up_sent(d.id)
                    st.rerun()

    if not drafts:
        st.info("No outreach drafts yet. Shortlist a startup on the Startups page, then click "
                "'Draft outreach' there (shortlisted startups are also drafted after each daily intel run).")
    else:
        statuses = ["pending_review", "approved", "sent", "replied", "skipped"]
        show = st.multiselect("Status", statuses, default=["pending_review", "approved", "sent"], key="outreach_status")
        by_plan: dict[int, list] = {}
        for d in drafts:
            if d.status in show:
                by_plan.setdefault(d.plan_id, []).append(d)
        for plan_id in sorted(by_plan, reverse=True):
            plan = plans[plan_id]
            with st.expander(f"**{names.get(plan.startup_id, '?')}** · {len(by_plan[plan_id])} contact(s) · "
                             f"drafted {local(plan.created_at)} · ${plan.cost_usd:.3f}", expanded=True):
                st.markdown(f"**Strategy.** {md(plan.strategy)}")
                for d in by_plan[plan_id]:
                    with style.card(f"draft-{d.id}"):
                        who = d.name or f"{d.role} (find on LinkedIn)"
                        st.markdown(f"**#{d.priority} · {who}** · {d.role if d.name else ''} · _{d.source}_ {style.badge(d.status)}")
                        st.caption(md(d.why))
                        if d.email:
                            st.caption(f"Email: {d.email} ({d.email_confidence})")
                        for w in d.warnings:
                            st.warning(w)
                        m = d.messages
                        t1, t2, t3, t4 = st.tabs(["Connection note", "LinkedIn message", "Email", "Follow-up"])
                        t1.code(m.get("connection_note", ""), language=None, wrap_lines=True)
                        t1.caption(f"{len(m.get('connection_note', ''))} characters")
                        t2.code(m.get("linkedin_message", ""), language=None, wrap_lines=True)
                        t3.code(f"Subject: {m.get('email_subject', '')}\n\n{m.get('email_body', '')}", language=None, wrap_lines=True)
                        t4.code(m.get("follow_up", ""), language=None, wrap_lines=True)
                        c1, c2, c3, c4, c5 = st.columns(5)
                        c1.link_button("Find on LinkedIn", d.linkedin_search, width="stretch", icon=":material/person_search:")
                        if d.email:
                            c2.link_button("Gmail draft", icon=":material/mail:", url=gmail_compose_url(d.email, m.get("email_subject", ""),
                                                                                 m.get("email_body", "")), width="stretch")
                        if d.status == "pending_review" and c3.button("Approve", key=f"oa{d.id}", type="primary", width="stretch"):
                            set_draft_status(d.id, "approved")
                            st.rerun()
                        if d.status in ("pending_review", "approved") and c4.button("Mark sent", key=f"os{d.id}", width="stretch"):
                            mark_sent(d.id)
                            st.rerun()
                        if d.status == "sent" and c4.button("Mark replied", key=f"or{d.id}", width="stretch"):
                            set_draft_status(d.id, "replied")
                            st.rerun()
                        if d.status not in ("replied", "skipped") and c5.button("Skip", key=f"ox{d.id}", width="stretch"):
                            set_draft_status(d.id, "skipped")
                            st.rerun()


# ---------- spend ----------
def page_spend() -> None:
    style.header('Spend', 'What each agent has cost over the last 30 days.')
    since = datetime.now(timezone.utc) - timedelta(days=30)
    with session() as s:
        usage = s.exec(select(LlmUsage).where(LlmUsage.ts >= since)).all()
    if not usage:
        st.info("No LLM spend in the last 30 days.")
    else:
        u = pd.DataFrame([x.model_dump() for x in usage])
        u["day"] = pd.to_datetime(u.ts, utc=True).dt.tz_convert(str(tz)).dt.date
        st.bar_chart(u.pivot_table(index="day", columns="agent", values="cost_usd", aggfunc="sum").fillna(0))
        st.dataframe(
            u.groupby(["agent", "model"]).agg(
                calls=("id", "count"), cost_usd=("cost_usd", "sum"),
                input=("input_tokens", "sum"), cache_read=("cache_read_tokens", "sum"), output=("output_tokens", "sum"),
            ).reset_index(),
            hide_index=True, width="stretch",
        )

# ---------- runs ----------
def page_runs() -> None:
    style.header('Run history', 'Every scheduled and manual run, with its results.')
    with session() as s:
        runs = s.exec(select(RunLog).order_by(RunLog.id.desc()).limit(30)).all()
    for r in runs:
        label = f"{r.kind} · {local(r.started_at)} · {'ERROR' if r.error else 'ok'}"
        with st.expander(label):
            if r.error:
                st.error(r.error)
            st.json(r.stats)


# ---------- navigation ----------
def page_overview() -> None:
    from jobsearch.ui import overview

    overview.render(PAGES)


PAGES = {
    "overview": st.Page(page_overview, title="Overview", icon=":material/space_dashboard:", default=True),
    "jobs": st.Page(page_jobs, url_path="jobs", title="Jobs", icon=":material/work:"),
    "approvals": st.Page(page_approvals, url_path="approvals", title="Approvals", icon=":material/fact_check:"),
    "applications": st.Page(page_applications, url_path="applications", title="Applications", icon=":material/send:"),
    "startups": st.Page(page_startups, url_path="startups", title="Startups", icon=":material/rocket_launch:"),
    "outreach": st.Page(page_outreach, url_path="outreach", title="Outreach", icon=":material/forum:"),
    "profile": st.Page(page_profile, url_path="profile", title="My profile", icon=":material/person:"),
    "search": st.Page(page_search, url_path="search", title="Job search", icon=":material/tune:"),
    "spend": st.Page(page_spend, url_path="spend", title="Spend", icon=":material/payments:"),
    "runs": st.Page(page_runs, url_path="runs", title="Run history", icon=":material/history:"),
}
nav = st.navigation({
    "": [PAGES["overview"]],
    "Pipeline": [PAGES["jobs"], PAGES["approvals"], PAGES["applications"]],
    "Startups": [PAGES["startups"], PAGES["outreach"]],
    "Settings": [PAGES["profile"], PAGES["search"], PAGES["spend"], PAGES["runs"]],
})
sidebar()
nav.run()
