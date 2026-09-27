"""Job Scout run: fetch -> prefilter -> dedupe/merge -> store -> score newest first."""

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from jobsearch import llm
from jobsearch.config import preferences, watchlist
from jobsearch.db import Job, RunLog, as_utc, session, utcnow
from jobsearch.scout import scorer
from jobsearch.scout.dedupe import dedupe_key, is_same_job, norm_company
from jobsearch.scout.filters import prefilter, work_rights_blocker
from jobsearch.scout.models import RawPosting
from jobsearch.scout.sources import aggregators, ats

log = logging.getLogger(__name__)

SHORTLIST_WORK_RIGHTS = {"ok", "sponsorship_offered", "unclear"}


def fetch_all(prefs: dict, stats: Counter) -> list[RawPosting]:
    companies = [c for c in watchlist() if c.get("active", True)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        batches = list(ex.map(ats.fetch_company, companies))
    postings = [p for batch in batches for p in batch]
    queries, max_age = prefs["search"]["queries"], prefs["search"]["max_age_days"]
    postings += aggregators.adzuna(queries, max_age)
    postings += aggregators.jooble(queries, max_age)
    for p in postings:
        stats[f"fetched.{p.source}"] += 1
    return postings


def _find_existing(s: Session, p: RawPosting, city: str) -> Job | None:
    for job in s.exec(select(Job).where(Job.company_norm == norm_company(p.company))):
        if any(src["source"] == p.source and src["external_id"] == p.external_id for src in job.sources):
            return job
        if is_same_job(job.title, job.city, p.title, city):
            return job
    return None


def _merge(job: Job, p: RawPosting) -> bool:
    """Fold another sighting of the same job into the stored row. Returns True if changed."""
    if any(src["source"] == p.source and src["external_id"] == p.external_id for src in job.sources):
        return False
    job.sources = [*job.sources, {"source": p.source, "url": p.url, "external_id": p.external_id}]
    if p.is_ats:
        job.url = p.url
    if len(p.description) > len(job.description):
        job.description = p.description
    if p.posted_at and (job.posted_at is None or p.posted_at < as_utc(job.posted_at)):
        job.posted_at = p.posted_at
    job.salary_text = job.salary_text or p.salary_text
    return True


def ingest(postings: list[RawPosting], prefs: dict, stats: Counter) -> None:
    with session() as s:
        for p in postings:
            loc, reason = prefilter(p, prefs)
            if loc is None:
                stats[f"rejected.{reason.split(' (')[0]}"] += 1
                continue
            city, location_class = loc
            existing = _find_existing(s, p, city)
            if existing:
                if _merge(existing, p):
                    s.add(existing)
                    stats["merged_duplicate"] += 1
                continue
            if p.fetch_description and not p.description:
                p.description = p.fetch_description()
            blocker = work_rights_blocker(f"{p.title}\n{p.description}", prefs)
            s.add(Job(
                dedupe_key=dedupe_key(p.company, p.title, city),
                company=p.company,
                company_norm=norm_company(p.company),
                title=p.title,
                location_raw=p.location,
                city=city,
                location_class=location_class,
                posted_at=p.posted_at,
                url=p.url,
                sources=[{"source": p.source, "url": p.url, "external_id": p.external_id}],
                description=p.description,
                salary_text=p.salary_text,
                employment_type=p.employment_type,
                status="filtered_out" if blocker else "new",
                filter_reason=f"work rights: {blocker}" if blocker else "",
            ))
            s.flush()  # so later postings in this run can dedupe against it
            stats["filtered_out.work_rights" if blocker else "new"] += 1
        s.commit()


def score_pending(prefs: dict, stats: Counter, limit: int | None = None) -> None:
    limit = limit if limit is not None else prefs["scoring"]["max_scores_per_run"]
    threshold = prefs["scoring"]["shortlist_threshold"]
    with session() as s:
        # Newest first: jobs posted in the last 24h are always scored before older ones.
        pending = s.exec(
            select(Job).where(Job.status == "new").order_by(Job.posted_at.desc().nulls_last()).limit(limit)
        ).all()
        if not pending:
            return
        system_prompt = scorer.build_system_prompt()
        for job in pending:
            try:
                a = scorer.score(job, system_prompt)
            except llm.BudgetExceeded as e:
                log.warning("Stopping scoring: %s", e)
                stats["budget_stop"] += 1
                break
            except llm.LLMError as e:
                log.warning("Scoring failed for job %s: %s", job.id, e)
                stats["score_failed"] += 1
                continue
            job.fit_score = a.fit_score
            job.recommendation = a.recommendation
            job.work_rights = a.work_rights
            job.seniority_fit = a.seniority_fit
            job.assessment = a.model_dump()
            job.salary_text = job.salary_text or a.salary
            job.scored_at = utcnow()
            job.score_model = prefs["scoring"]["model"]
            shortlist = (
                a.fit_score >= threshold
                and a.work_rights in SHORTLIST_WORK_RIGHTS
                and a.recommendation != "skip"
            )
            job.status = "shortlisted" if shortlist else "scored"
            s.add(job)
            s.commit()
            stats["scored"] += 1
            stats["shortlisted"] += int(shortlist)


def run_scout(score: bool = True, score_limit: int | None = None) -> dict:
    prefs = preferences()
    stats: Counter = Counter()
    with session() as s:
        run = RunLog(kind="scout")
        s.add(run)
        s.commit()
    try:
        postings = fetch_all(prefs, stats)
        ingest(postings, prefs, stats)
        if score:
            score_pending(prefs, stats, score_limit)
        if score and prefs.get("tailoring", {}).get("auto_after_scout"):
            from jobsearch.tailor.pipeline import run_tailor

            for k, v in run_tailor().items():
                stats[f"tailor.{k}"] += v
    except Exception as e:
        run.error = repr(e)
        raise
    finally:
        stats["spend_today_usd"] = round(llm.spend_today(), 4)
        run.stats = dict(stats)
        run.finished_at = utcnow()
        with session() as s:
            s.add(run)
            s.commit()
    return dict(stats)
