"""Sonnet pulls structured funding events out of several articles per call."""

from typing import Literal

from pydantic import BaseModel, Field

from jobsearch import llm
from jobsearch.config import preferences
from jobsearch.intel.news import Article

BATCH_CHARS = 20_000   # article text per extraction call; round-ups can list a dozen companies each


class FundingEvent(BaseModel):
    article: int = Field(description="index of the article this came from")
    company: str
    website: str = Field(description="company website if the article states it, else empty")
    is_australian: Literal["yes", "no", "unclear"] = Field(description="headquartered or founded in Australia")
    hq_city: str = Field(description="Australian city if stated, else empty")
    ai_centricity: Literal["core", "significant", "peripheral", "none"] = Field(
        description="core: the product is AI; significant: AI is a major part; peripheral: AI is incidental")
    ai_hiring_potential: Literal["high", "medium", "low"] = Field(
        description="how likely the company is to hire AI engineers in the next 6-12 months, judged from its "
                    "product, data and what the article says the money is for")
    sector: str
    round: str = Field(description="e.g. pre-seed, seed, Series A; empty if not stated")
    amount_text: str = Field(description="as written, e.g. '$12 million'; empty if not stated")
    amount_aud_millions: float = Field(description="amount in AUD millions if stated or clearly convertible, else -1")
    investors: list[str]
    announced_date: str = Field(description="YYYY-MM-DD if stated, else empty")
    one_line: str = Field(description="what the company does, one sentence, from the article")


class Extraction(BaseModel):
    events: list[FundingEvent]


SYSTEM = """You extract startup funding announcements from news articles.

For each article, list every company that announced a new funding round (pre-seed through late stage,
grants and government funding included). Round-up articles can contain several companies; list each.
Skip acquisitions, IPOs, fund launches by VCs, and companies merely mentioned in passing.
Use only what the article states. Leave a field empty (or -1 for amounts) rather than guess.
If the amount is in USD, convert to AUD at 1 USD = 1.5 AUD for amount_aud_millions and keep amount_text as written.
ai_hiring_potential applies to every company, not only AI companies: a newly funded fintech, healthtech or
SaaS startup that plans to build AI features, automate with LLMs or exploit a large dataset is "medium" or
"high"; a company with no plausible AI work (e.g. a café chain or a hardware reseller) is "low"."""


def batches(articles: list[tuple[int, Article]]):
    batch, size = [], 0
    for idx, a in articles:
        body = a.text or a.summary
        if batch and size + len(body) > BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append((idx, a))
        size += len(body)
    if batch:
        yield batch


def extract(batch: list[tuple[int, Article]]) -> list[FundingEvent]:
    parts = []
    for idx, a in batch:
        body = a.text or a.summary or "(headline only)"
        date = a.published_at.date().isoformat() if a.published_at else "unknown"
        parts.append(f'<article index="{idx}" source="{a.source}" published="{date}">\n'
                     f"Headline: {a.title}\n{body}\n</article>")
    topic = preferences().get("intel", {}).get("topic", "AI")
    result = llm.parse(
        agent="intel.extract",
        model=preferences()["models"]["default"],
        system=SYSTEM + f"\nai_centricity measures how central {topic} is to the company's product.",
        user="\n\n".join(parts),
        output_format=Extraction,
        max_tokens=6000,
        effort="low",
    )
    return result.events


def extract_all(batch: list[tuple[int, Article]]) -> list[FundingEvent]:
    """Like extract(), but a batch whose events overflow max_tokens is split in half and retried."""
    try:
        return extract(batch)
    except llm.OutputTruncated:
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        return extract_all(batch[:mid]) + extract_all(batch[mid:])
