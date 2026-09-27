"""SQLite state store shared by all agents. Agents hand work to each other through these tables."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, text
from sqlmodel import Field, Session, SQLModel, create_engine

from jobsearch.config import DATA_DIR, DB_PATH


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    dedupe_key: str = Field(index=True)
    company: str
    company_norm: str = Field(index=True)
    title: str
    location_raw: str = ""
    city: str = ""                      # normalized AU city, or "Remote (AU)"
    location_class: str = ""            # preferred | acceptable | remote_au
    posted_at: Optional[datetime] = Field(default=None, index=True)
    first_seen_at: datetime = Field(default_factory=utcnow)
    url: str = ""                       # best apply URL (ATS preferred over aggregator)
    sources: list = Field(default_factory=list, sa_column=Column(JSON))  # [{source, url, external_id}]
    description: str = ""
    salary_text: str = ""
    employment_type: str = ""

    # pipeline state: new -> filtered_out | scored -> shortlisted | dismissed -> (phase 2+) tailored, applied
    status: str = Field(default="new", index=True)
    filter_reason: str = ""

    fit_score: Optional[int] = Field(default=None, index=True)
    recommendation: str = ""
    work_rights: str = ""
    seniority_fit: str = ""
    assessment: dict = Field(default_factory=dict, sa_column=Column(JSON))
    scored_at: Optional[datetime] = None
    score_model: str = ""


class ResumeVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    model: str = ""
    draft: dict = Field(default_factory=dict, sa_column=Column(JSON))        # raw tailor output
    content: dict = Field(default_factory=dict, sa_column=Column(JSON))      # after verification + page fit
    verification: list = Field(default_factory=list, sa_column=Column(JSON)) # what the verifier changed and why
    trims: list = Field(default_factory=list, sa_column=Column(JSON))
    coverage: list = Field(default_factory=list, sa_column=Column(JSON))     # [{keyword, status}]
    humanizer: dict = Field(default_factory=dict, sa_column=Column(JSON))    # changes, reverted lines, AI tells before/after
    resume_pdf: str = ""
    cover_letter_pdf: str = ""
    cost_usd: float = 0.0
    # pending_review -> approved | rejected
    status: str = Field(default="pending_review", index=True)
    reviewed_at: Optional[datetime] = None


class NewsItem(SQLModel, table=True):
    """Every news article seen, so each one is filtered and extracted at most once."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)   # normalized title, stable across feeds that syndicate it
    title: str
    url: str = ""
    source: str = ""
    published_at: Optional[datetime] = None
    first_seen_at: datetime = Field(default_factory=utcnow)
    relevant: bool = False                      # passed the free keyword filter
    extracted: bool = False
    events_found: int = 0


class Startup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    name_norm: str = Field(index=True, unique=True)
    website: str = ""
    hq_city: str = ""
    sector: str = ""
    ai_centricity: str = ""                     # core | significant | peripheral
    one_line: str = ""
    first_seen_at: datetime = Field(default_factory=utcnow)
    latest_round: str = ""
    latest_amount_text: str = ""
    latest_amount_aud_m: Optional[float] = None
    latest_announced: Optional[datetime] = Field(default=None, index=True)
    investors: list = Field(default_factory=list, sa_column=Column(JSON))
    events: list = Field(default_factory=list, sa_column=Column(JSON))      # every funding event seen
    sources: list = Field(default_factory=list, sa_column=Column(JSON))     # [{title, url, source, date}]
    article_excerpt: str = ""
    site_excerpt: str = ""
    careers: dict = Field(default_factory=dict, sa_column=Column(JSON))     # {ats, slug, roles: [...]}
    enriched_at: Optional[datetime] = None
    profile: dict = Field(default_factory=dict, sa_column=Column(JSON))
    profiled_at: Optional[datetime] = None
    relevance_score: Optional[int] = Field(default=None, index=True)
    # new -> profiled -> shortlisted (phase 4 outreach) | dismissed
    status: str = Field(default="new", index=True)


class OutreachPlan(SQLModel, table=True):
    """One outreach plan per startup per drafting run: who to contact, in what order, and why."""
    id: Optional[int] = Field(default=None, primary_key=True)
    startup_id: int = Field(foreign_key="startup.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    model: str = ""
    strategy: str = ""
    discovered: dict = Field(default_factory=dict, sa_column=Column(JSON))   # people/emails found and where
    cost_usd: float = 0.0


class OutreachDraft(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="outreachplan.id", index=True)
    startup_id: int = Field(foreign_key="startup.id", index=True)
    priority: int = 1                           # 1 = contact first
    name: str = ""                              # empty for a role you still need to find on LinkedIn
    role: str = ""
    source: str = ""                            # where the person was found
    email: str = ""
    email_confidence: str = ""                  # published | pattern (unverified) | none
    linkedin_search: str = ""
    why: str = ""
    messages: dict = Field(default_factory=dict, sa_column=Column(JSON))
    warnings: list = Field(default_factory=list, sa_column=Column(JSON))
    # pending_review -> approved -> sent -> replied | skipped
    status: str = Field(default="pending_review", index=True)
    sent_at: Optional[datetime] = None
    follow_up_due: Optional[datetime] = Field(default=None, index=True)
    follow_up_sent: bool = False


class Application(SQLModel, table=True):
    """One application per approved resume. The agent prepares and pre-fills; you always click Submit."""
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    resume_version_id: int = Field(foreign_key="resumeversion.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    mode: str = ""                  # prefill (Greenhouse | Lever | Ashby form filled for you) | guided (copy-paste kit)
    ats: str = ""
    apply_url: str = ""
    fields: list = Field(default_factory=list, sa_column=Column(JSON))   # questions + answers, see apply/schema.py
    # needs_input -> ready -> applied (you mark it once you've submitted)
    status: str = Field(default="needs_input", index=True)
    preview_png: str = ""
    result_note: str = ""
    submitted_at: Optional[datetime] = None
    cost_usd: float = 0.0


class LlmUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    agent: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    request_id: str = ""


class RunLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = ""


_engine = None


def engine():
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}")
        SQLModel.metadata.create_all(_engine)
        add_missing_columns(_engine)
    return _engine


def add_missing_columns(eng) -> None:
    """create_all() only creates new tables. Add columns introduced after a table already existed
    (nullable, so existing rows are untouched)."""
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table.name}')"))}
            for col in table.columns:
                if existing and col.name not in existing:
                    ddl = col.type.compile(dialect=eng.dialect)
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}"))


def session() -> Session:
    return Session(engine(), expire_on_commit=False)


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite drops tzinfo on read; every datetime we store is UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
