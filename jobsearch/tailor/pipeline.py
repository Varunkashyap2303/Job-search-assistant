"""Resume Tailor run: shortlisted jobs -> tailored, verified, one-page PDFs awaiting approval."""

import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import select

from jobsearch import llm
from jobsearch.config import DATA_DIR, local_tz, preferences
from jobsearch.db import Job, ResumeVersion, RunLog, session, utcnow
from jobsearch.tailor import render
from jobsearch.tailor.coverage import keyword_coverage
from jobsearch.tailor.facts import Catalogue, load_catalogue
from jobsearch.tailor.humanize import humanize
from jobsearch.tailor.tailor import TailoredResume, build_system_prompt, tailor
from jobsearch.tailor.verify import verify_and_fix

log = logging.getLogger(__name__)

RESUME_DIR = DATA_DIR / "resumes"


def humanize_step(verified: TailoredResume, cat: Catalogue, job: Job) -> tuple[TailoredResume, dict]:
    """Humanizer pass. On failure the verified version is kept and the reason recorded, so a paid
    tailoring run is never thrown away."""
    if not preferences()["tailoring"].get("humanize", True):
        return verified, {"skipped": "disabled in preferences"}
    try:
        return humanize(verified, cat, job)
    except (llm.BudgetExceeded, llm.LLMError) as e:
        log.warning("Humanizer skipped for job %s: %s", job.id, e)
        return verified, {"skipped": str(e)}


def _render(content: TailoredResume, cat: Catalogue, job: Job) -> dict:
    who = render.safe_name(cat.identity.get("name", "Resume"))
    company = render.safe_name(job.company)
    folder = RESUME_DIR / f"{job.id}_{company}_{render.safe_name(job.title)}"
    resume_pdf = folder / f"{who}_Resume_{company}.pdf"
    fitted, trims = render.render_resume(content, cat, resume_pdf)
    render.preview_png(resume_pdf, folder / "preview.png")
    cover_pdf = folder / f"{who}_Cover_Letter_{company}.pdf"
    if fitted.cover_letter:
        render.render_cover_letter(fitted, cat, job, cover_pdf)
    return {
        "content": fitted.model_dump(),
        "trims": trims,
        "coverage": keyword_coverage(fitted, cat, job.assessment or {}),
        "resume_pdf": str(resume_pdf),
        "cover_letter_pdf": str(cover_pdf) if fitted.cover_letter else "",
    }


def tailor_job(job: Job, cat: Catalogue, system_prompt: str, guidance: str = "") -> ResumeVersion:
    started = datetime.now(timezone.utc)
    draft = tailor(job, cat, system_prompt, guidance)
    verified, report = verify_and_fix(draft, cat, job)
    final, human = humanize_step(verified, cat, job)

    version = ResumeVersion(
        job_id=job.id,
        model=preferences()["models"]["tailoring"],
        draft=draft.model_dump(),
        verification=report,
        humanizer=human,
        cost_usd=round(llm.spend_since(started), 4),
        **_render(final, cat, job),
    )
    with session() as s:
        # A new version replaces any earlier one still waiting for review
        for old in s.exec(select(ResumeVersion).where(ResumeVersion.job_id == job.id,
                                                      ResumeVersion.status == "pending_review")).all():
            old.status = "superseded"
            s.add(old)
        s.add(version)
        job.status = "tailored"
        s.add(job)
        s.commit()
    return version


def humanize_version(version_id: int) -> ResumeVersion:
    """Run the humanizer on an existing version (e.g. one made before the humanizer existed) and re-render it."""
    started = datetime.now(timezone.utc)
    cat = load_catalogue()
    with session() as s:
        v = s.get(ResumeVersion, version_id)
        job = s.get(Job, v.job_id)
    final, human = humanize(TailoredResume.model_validate(v.content), cat, job)
    rendered = _render(final, cat, job)
    with session() as s:
        v = s.get(ResumeVersion, version_id)
        for k, val in rendered.items():
            setattr(v, k, val)
        v.humanizer = human
        v.cost_usd = round((v.cost_usd or 0) + llm.spend_since(started), 4)
        s.add(v)
        s.commit()
    return v


def tailored_today() -> int:
    start = datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with session() as s:
        return s.exec(select(func.count()).select_from(ResumeVersion).where(ResumeVersion.created_at >= start)).one()


def run_tailor(limit: int | None = None, job_id: int | None = None, guidance: str = "") -> dict:
    cfg = preferences()["tailoring"]
    stats: Counter = Counter()
    with session() as s:
        if job_id is not None:
            jobs = [s.get(Job, job_id)]
        else:
            room = max(0, cfg["max_per_day"] - tailored_today())
            n = min(limit if limit is not None else cfg["max_per_run"], room)
            if n == 0:
                stats["daily_limit_reached"] += 1
            # Newest first, so jobs from the last 24h get their resumes before older ones
            jobs = s.exec(
                select(Job).where(Job.status == "shortlisted")
                .order_by(Job.posted_at.desc().nulls_last(), Job.fit_score.desc()).limit(n)
            ).all() if n else []
        run = RunLog(kind="tailor")
        s.add(run)
        s.commit()

    if jobs:
        cat = load_catalogue()
        system_prompt = build_system_prompt(cat)
        for job in jobs:
            if job is None:
                continue
            try:
                v = tailor_job(job, cat, system_prompt, guidance)
                stats["tailored"] += 1
                stats["verifier_changes"] += len(v.verification)
                log.info("Tailored job %s (%s at %s) for $%.3f", job.id, job.title, job.company, v.cost_usd)
            except llm.BudgetExceeded as e:
                log.warning("Stopping tailoring: %s", e)
                stats["budget_stop"] += 1
                break
            except Exception:
                log.exception("Tailoring failed for job %s", job.id)
                stats["failed"] += 1

    run.stats = dict(stats)
    run.finished_at = utcnow()
    with session() as s:
        s.add(run)
        s.commit()
    return dict(stats)
