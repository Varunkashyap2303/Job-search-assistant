"""Startup Intel run: news -> free filter -> Sonnet extraction -> free enrichment -> Opus profiles -> report."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from jobsearch import llm
from jobsearch.config import DATA_DIR, local_tz, preferences
from jobsearch.db import NewsItem, RunLog, Startup, as_utc, session, utcnow
from jobsearch.intel import enrich, extract, news
from jobsearch.intel.profile import build_system_prompt, write_profile
from jobsearch.scout.dedupe import norm_company
from jobsearch.scout.sources.base import parse_iso

log = logging.getLogger(__name__)

REPORT_DIR = DATA_DIR / "reports"
KEEP_AI = {"core", "significant"}


def _keep(ev: extract.FundingEvent) -> bool:
    return ev.ai_centricity in KEEP_AI and (ev.is_australian == "yes" or (ev.is_australian == "unclear" and ev.hq_city))


def _event_date(ev: extract.FundingEvent, article: news.Article) -> datetime | None:
    return parse_iso(ev.announced_date) if ev.announced_date else article.published_at


def upsert_startup(ev: extract.FundingEvent, article: news.Article, stats: Counter) -> None:
    key = norm_company(ev.company)
    if not key:
        return
    when = _event_date(ev, article)
    event = {**ev.model_dump(exclude={"article"}), "date": when.date().isoformat() if when else ""}
    source = {"title": article.title, "url": article.url, "source": article.source,
              "date": article.published_at.date().isoformat() if article.published_at else ""}
    with session() as s:
        st = s.exec(select(Startup).where(Startup.name_norm == key)).first()
        if st is None:
            st = Startup(name=ev.company, name_norm=key)
            stats["startups.new"] += 1
        else:
            stats["startups.updated"] += 1
        if not any(e.get("round") == ev.round and e.get("amount_text") == ev.amount_text for e in st.events):
            st.events = [*st.events, event]
        if not any(x.get("title") == source["title"] for x in st.sources):
            st.sources = [*st.sources, source]
        if when and (st.latest_announced is None or when >= as_utc(st.latest_announced)):
            st.latest_announced = when
            st.latest_round = ev.round or st.latest_round
            st.latest_amount_text = ev.amount_text or st.latest_amount_text
            st.latest_amount_aud_m = ev.amount_aud_millions if ev.amount_aud_millions >= 0 else st.latest_amount_aud_m
            st.investors = ev.investors or st.investors
        st.website = st.website or ev.website
        st.hq_city = st.hq_city or ev.hq_city
        st.sector = st.sector or ev.sector
        st.one_line = st.one_line or ev.one_line
        rank = {"core": 2, "significant": 1}
        if rank.get(ev.ai_centricity, 0) > rank.get(st.ai_centricity, 0):
            st.ai_centricity = ev.ai_centricity
        if article.text and len(st.article_excerpt) < 500:
            st.article_excerpt = _excerpt_about(article.text, ev.company)
        s.add(st)
        s.commit()


def _excerpt_about(text: str, company: str, limit: int = 3500) -> str:
    """For round-up articles, keep the part that talks about this company."""
    i = text.lower().find(company.lower())
    if i < 0:
        return text[:limit]
    start = max(0, i - 300)
    return text[start:start + limit]


def discover(cfg: dict, stats: Counter) -> None:
    articles = news.collect(cfg)
    stats["articles.fetched"] = len(articles)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["lookback_days"])
    with session() as s:
        seen = set(s.exec(select(NewsItem.key)).all())
    fresh = [a for a in articles if a.key not in seen and (a.published_at is None or a.published_at >= cutoff)]
    topic = news.topic_regex(cfg)
    candidates = [a for a in fresh if news.is_candidate(a, topic)]
    stats["articles.new"] = len(fresh)
    stats["articles.candidates"] = len(candidates)

    # Irrelevant articles are recorded now so they're never looked at again
    with session() as s:
        for a in fresh:
            if a not in candidates:
                s.add(NewsItem(key=a.key, title=a.title, url=a.url, source=a.source, published_at=a.published_at))
        s.commit()

    for a in candidates:
        if a.fetchable and len(a.text) < 1000:
            a.text = news.fetch_article_text(a.url) or a.text

    indexed = list(enumerate(candidates))
    for batch in extract.batches(indexed):
        try:
            events = extract.extract(batch)
        except llm.BudgetExceeded as e:
            # Unprocessed candidates aren't recorded, so the next run retries them if still in the feeds
            log.warning("Stopping extraction: %s", e)
            stats["budget_stop"] += 1
            return
        except llm.LLMError as e:
            log.warning("Extraction failed: %s", e)
            stats["extract_failed"] += 1
            continue
        by_article = Counter(ev.article for ev in events)
        for ev in events:
            if not 0 <= ev.article < len(candidates):
                continue
            stats["events.found"] += 1
            if _keep(ev):
                stats["events.kept"] += 1
                upsert_startup(ev, candidates[ev.article], stats)
        with session() as s:
            for idx, a in batch:
                s.add(NewsItem(key=a.key, title=a.title, url=a.url, source=a.source, published_at=a.published_at,
                               relevant=True, extracted=True, events_found=by_article.get(idx, 0)))
            s.commit()


def enrich_new(stats: Counter) -> None:
    with session() as s:
        pending = s.exec(select(Startup).where(Startup.enriched_at.is_(None))).all()
    for st in pending:
        website, site_text = enrich.find_website(st.name, st.website)
        careers = enrich.detect_ats(st.name)
        if website:
            extra = enrich.careers_text(website)
            site_text = f"{site_text}\n\n{extra}" if extra else site_text
        with session() as s:
            st = s.get(Startup, st.id)
            st.website = website or st.website
            st.site_excerpt = site_text[:6000]
            st.careers = careers
            st.enriched_at = utcnow()
            s.add(st)
            s.commit()
        stats["enriched"] += 1
        if enrich.add_to_watchlist(st.name, careers, ai_native=st.ai_centricity == "core"):
            stats["watchlist.added"] += 1


def _priority(st: Startup, preferred: list[str]) -> tuple:
    return (
        st.ai_centricity == "core",
        any(c.lower() in (st.hq_city or "").lower() for c in preferred),
        bool(st.careers.get("open_roles")),
        as_utc(st.latest_announced) or datetime.min.replace(tzinfo=timezone.utc),
        st.latest_amount_aud_m or 0,
    )


def profiled_today() -> int:
    start = datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with session() as s:
        return len(s.exec(select(Startup.id).where(Startup.profiled_at >= start)).all())


def profile_top(cfg: dict, stats: Counter, limit: int | None = None, startup_id: int | None = None) -> None:
    with session() as s:
        if startup_id is not None:
            queue = [s.get(Startup, startup_id)]
        else:
            n = limit if limit is not None else max(0, cfg["max_profiles_per_day"] - profiled_today())
            pending = s.exec(select(Startup).where(Startup.status == "new", Startup.enriched_at.is_not(None))).all()
            preferred = preferences()["search"]["locations"]["preferred"]
            queue = sorted(pending, key=lambda x: _priority(x, preferred), reverse=True)[:n]
    if not queue:
        return
    system_prompt = build_system_prompt()
    for st in queue:
        try:
            p = write_profile(st, system_prompt)
        except llm.BudgetExceeded as e:
            log.warning("Stopping profiles: %s", e)
            stats["budget_stop"] += 1
            break
        except llm.LLMError as e:
            log.warning("Profile failed for %s: %s", st.name, e)
            stats["profile_failed"] += 1
            continue
        with session() as s:
            st = s.get(Startup, st.id)
            st.profile = p.model_dump()
            st.profiled_at = utcnow()
            st.relevance_score = p.relevance_score
            st.ai_centricity = p.ai_centricity
            if not p.website_consistent:
                st.website = ""
            if st.status == "new":
                st.status = "profiled"
            s.add(st)
            s.commit()
        stats["profiled"] += 1


def write_report(stats: Counter) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(local_tz()).date()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    with session() as s:
        rows = s.exec(select(Startup).where(Startup.status != "dismissed")).all()
    rows = [r for r in rows if (as_utc(r.latest_announced) or as_utc(r.first_seen_at)) >= cutoff]
    rows.sort(key=lambda r: (r.relevance_score or -1, as_utc(r.latest_announced) or cutoff), reverse=True)
    new_today = [r for r in rows if as_utc(r.first_seen_at).astimezone(local_tz()).date() == today]

    lines = [f"# Funded AU AI startups: {today}", "",
             f"{len(new_today)} new today · {len(rows)} tracked (last 90 days)", ""]
    for r in rows:
        p = r.profile or {}
        tag = " **NEW**" if r in new_today else ""
        lines.append(f"## {r.name}{tag}")
        lines.append(f"{r.hq_city or 'city n/a'} · {r.sector} · {r.latest_round or 'round n/a'} "
                     f"{r.latest_amount_text} · {r.latest_announced.date() if r.latest_announced else ''} · "
                     f"AI: {r.ai_centricity} · relevance: {r.relevance_score if r.relevance_score is not None else 'not profiled'}")
        if r.website:
            lines.append(f"Website: {r.website}")
        if p:
            lines += ["", f"**Vision.** {p['vision']}", "", f"**Product.** {p['product']}", "",
                      f"**Funding.** {p['funding_summary']}", "", "**How AI fits in.**", p["ai_implementation"], "",
                      f"**Hiring.** {p['hiring_signals']}", "", f"**Why you.** {p['relevance_reasons']}",
                      f"**Outreach angle.** {p['outreach_angle']}"]
        else:
            lines.append(r.one_line)
        lines.append("")
    path = REPORT_DIR / f"intel_{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def run_intel(profile_limit: int | None = None, startup_id: int | None = None) -> dict:
    cfg = preferences()["intel"]
    stats: Counter = Counter()
    with session() as s:
        run = RunLog(kind="intel")
        s.add(run)
        s.commit()
    try:
        if startup_id is None:
            discover(cfg, stats)
            enrich_new(stats)
        profile_top(cfg, stats, profile_limit, startup_id)
        stats["report"] = write_report(stats)
    except Exception as e:
        run.error = repr(e)
        raise
    finally:
        run.stats = dict(stats)
        run.finished_at = utcnow()
        with session() as s:
            s.add(run)
            s.commit()
    return dict(stats)
