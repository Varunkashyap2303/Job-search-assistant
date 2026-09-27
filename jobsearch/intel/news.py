"""Collects startup-funding news from free RSS feeds and filters it with keywords (no tokens)."""

import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from bs4 import BeautifulSoup

from jobsearch.scout.sources.base import html_to_text, http

log = logging.getLogger(__name__)

_FUNDING = re.compile(
    r"\brais(e|es|ed|ing)\b|\bfunding\b|\bseed\b|pre-seed|series [a-e]\b|\binvest(ment|ors?)\b|\bbacked\b|"
    r"\bsecures?\b|\bcloses?\b.*\bround\b|\bround\b|\$\s?\d+(\.\d+)?\s?(m|million|bn|billion)\b|cheque",
    re.I,
)
_AI = re.compile(
    r"\bai\b|artificial intelligence|machine learning|\bml\b|\bllms?\b|gen(erative)? ?ai|agentic|\bagents?\b|"
    r"deep learning|computer vision|\bnlp\b|foundation model|copilot",
    re.I,
)
_AU = re.compile(
    r"australia|aussie|\bsydney\b|\bmelbourne\b|\bbrisbane\b|\bperth\b|\badelaide\b|\bcanberra\b|\bhobart\b|\banz\b",
    re.I,
)
# Weekly round-ups cover several startups; AI is judged per startup during extraction
_ROUNDUP = re.compile(r"startups? (that )?raised|funding (round-?up|wrap)|cheque-in|biggest funding rounds|deals of the week", re.I)


@dataclass
class Article:
    key: str
    title: str
    url: str
    source: str
    published_at: Optional[datetime]
    summary: str
    text: str = ""          # full text when the feed carries it or we fetch it
    au_source: bool = True  # AU-focused publication (no need to check for an Australian mention)
    fetchable: bool = True  # False for Google News redirect links


def title_key(title: str) -> str:
    title = re.sub(r"\s+-\s+[^-]+$", "", title)  # Google News appends " - Publisher"
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _tag(item: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
    if not m:
        return ""
    value = m.group(1).strip()
    if value.startswith("<![CDATA["):
        value = value[9:-3]
    return value


def fetch_feed(url: str, source: str, au_source: bool, fetchable: bool = True) -> list[Article]:
    try:
        r = http().get(url, headers={"User-Agent": "Mozilla/5.0 (personal job search)"})
    except Exception as e:
        log.warning("Feed %s failed: %s", source, e)
        return []
    if r.status_code != 200:
        log.warning("Feed %s returned HTTP %s", source, r.status_code)
        return []
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        title = html_to_text(_tag(item, "title"))
        if not title:
            continue
        publisher = html_to_text(_tag(item, "source")) or source
        out.append(Article(
            key=title_key(title),
            title=title,
            url=_tag(item, "link"),
            source=publisher,
            published_at=_parse_date(_tag(item, "pubDate")),
            summary=html_to_text(_tag(item, "description"))[:1000],
            text=html_to_text(_tag(item, "content:encoded")),
            au_source=au_source,
            fetchable=fetchable,
        ))
    return out


def google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-AU&gl=AU&ceid=AU:en"


def collect(cfg: dict) -> list[Article]:
    articles: list[Article] = []
    for feed in cfg["feeds"]:
        articles += fetch_feed(feed["url"], feed["name"], au_source=feed.get("au_source", True))
    for q in cfg["google_news_queries"]:
        # Google News links are encoded redirects, so these items are judged on the headline alone
        articles += fetch_feed(google_news_url(q), "Google News", au_source=False, fetchable=False)
    seen, unique = set(), []
    for a in articles:
        if a.key and a.key not in seen:
            seen.add(a.key)
            unique.append(a)
    return unique


def topic_regex(cfg: dict) -> re.Pattern:
    """The research focus (default: AI) as a regex; set intel.topic_keywords in preferences to change it."""
    words = cfg.get("topic_keywords")
    return re.compile("|".join(words), re.I) if words else _AI


def is_candidate(a: Article, topic: re.Pattern = _AI) -> bool:
    """Free filter: funding news about (probably) Australian companies in the focus topic, or a round-up."""
    text = f"{a.title} {a.summary} {a.text[:3000]}"
    if not _FUNDING.search(text):
        return False
    if not a.au_source and not _AU.search(text):
        return False
    return bool(_ROUNDUP.search(a.title) or topic.search(text))


def fetch_article_text(url: str, limit: int = 9000) -> str:
    try:
        r = http().get(url, headers={"User-Agent": "Mozilla/5.0 (personal job search)"})
        if r.status_code != 200:
            return ""
    except Exception as e:
        log.warning("Article fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    paras = soup.select("article p, .entry-content p, .article-body p, .post-content p, main p")
    text = "\n".join(p.get_text(" ", strip=True) for p in paras)
    text = re.sub(r"If you like this article, share it with your friends\.?", "", text)
    return text if len(text) <= limit else text[:limit] + "\n[article truncated]"
