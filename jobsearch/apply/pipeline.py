"""Application agent: prepare an application for an approved resume, let you review and edit every
answer, then preview it or open it pre-filled for you to submit. This module never submits anything."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from jobsearch import llm
from jobsearch.apply import answers, filler, schema
from jobsearch.db import Application, Job, ResumeVersion, session, utcnow
from jobsearch.tailor.facts import load_catalogue

log = logging.getLogger(__name__)


def blocking_fields(fields: list[dict]) -> list[dict]:
    """Questions to resolve before the application is ready for you to send."""
    return [f for f in fields if f.get("needs_you") or (f.get("required") and f.get("answer") in ("", None))]


def _status(fields: list[dict]) -> str:
    return "needs_input" if blocking_fields(fields) else "ready"


def prepare(resume_version_id: int) -> Application:
    started = datetime.now(timezone.utc)
    with session() as s:
        v = s.get(ResumeVersion, resume_version_id)
        job = s.get(Job, v.job_id)
    ats, url, form = schema.read_form(job)
    files = {"resume": v.resume_pdf, "cover_letter": v.cover_letter_pdf}
    cover = [p["text"] for p in (v.content or {}).get("cover_letter", [])]
    facts_text = " ".join(load_catalogue().facts.values())
    filled = [f.to_dict() for f in answers.fill_answers(form, job, files, cover, facts_text)]
    app = Application(job_id=job.id, resume_version_id=v.id, mode="guided" if ats == "guided" else "prefill",
                      ats=ats, apply_url=url, fields=filled, status=_status(filled),
                      cost_usd=round(llm.spend_since(started), 4))
    with session() as s:
        # A fresh preparation replaces an earlier unsent one for the same job
        for old in s.exec(select(Application).where(Application.job_id == job.id,
                                                    Application.status.in_(["needs_input", "ready"]))).all():
            s.delete(old)
        s.add(app)
        s.commit()
    return app


def save_answers(app_id: int, edits: dict[str, str | list]) -> Application:
    with session() as s:
        app = s.get(Application, app_id)
        fields = [dict(f) for f in app.fields]
        for f in fields:
            if f["key"] in edits:
                new = edits[f["key"]]
                new = " | ".join(new) if isinstance(new, list) else new
                if new != f["answer"] or f.get("needs_you"):
                    f["answer"], f["source"], f["needs_you"] = new, "you", False
        app.fields = fields
        if app.status in ("needs_input", "ready"):
            app.status = _status(fields)
        s.add(app)
        s.commit()
        return app


def _screenshot_path(app: Application) -> Path:
    with session() as s:
        v = s.get(ResumeVersion, app.resume_version_id)
    return Path(v.resume_pdf).parent / f"application_{app.id}_preview.png"


def run_preview(app_id: int) -> filler.FillResult:
    """Headless fill + screenshot. Never submits."""
    with session() as s:
        app = s.get(Application, app_id)
    res = filler.preview(app.apply_url, app.ats, app.fields, _screenshot_path(app))
    with session() as s:
        app = s.get(Application, app_id)
        app.preview_png = res.screenshot
        app.result_note = "; ".join(res.problems) or f"Preview filled {len(res.filled)} answers with no problems."
        s.add(app)
        s.commit()
    return res


def run_prefill(app_id: int) -> filler.FillResult:
    """Visible window filled in for you; you review and click Submit yourself."""
    with session() as s:
        app = s.get(Application, app_id)
    return filler.prefill(app.apply_url, app.ats, app.fields)


def mark_applied(app_id: int) -> None:
    with session() as s:
        app = s.get(Application, app_id)
        app.status, app.submitted_at = "applied", utcnow()
        job = s.get(Job, app.job_id)
        job.status = "applied"
        s.add(app)
        s.add(job)
        s.commit()
