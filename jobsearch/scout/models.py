from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


@dataclass
class RawPosting:
    """One job as returned by a single source, before normalization and dedupe."""

    source: str                 # adzuna | jooble | greenhouse | lever | ashby | workable | smartrecruiters
    external_id: str
    company: str
    title: str
    location: str
    url: str
    posted_at: Optional[datetime]
    description: str = ""
    salary_text: str = ""
    employment_type: str = ""
    is_ats: bool = False        # direct from the employer's ATS (better apply URL than an aggregator)
    ai_native: bool = False
    # Some sources list jobs without descriptions; this fetches one lazily, only for
    # postings that survive the cheap title/location filters.
    fetch_description: Optional[Callable[[], str]] = field(default=None, repr=False)
