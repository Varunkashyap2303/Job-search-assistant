"""Renders tailored content to PDF with a headless Chromium browser (Edge, Chrome or Playwright's own).
Layout mirrors the candidate's own resume. Trims the lowest-priority bullets until it fits one A4 page."""

import html
import re
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from playwright.sync_api import sync_playwright

from jobsearch import browser
from jobsearch.tailor.facts import Catalogue, fmt_range
from jobsearch.tailor.tailor import TailoredResume

CSS = """
@page { size: A4; margin: 12mm 15mm 11mm 15mm; }
body { font-family: Calibri, Carlito, 'Liberation Sans', 'Helvetica Neue', Helvetica, 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #1a1a1a;
       line-height: 1.24; margin: 0; }
a { color: inherit; text-decoration: none; }
h1 { color: #1F3864; font-size: 20pt; text-align: center; margin: 0 0 2px; letter-spacing: .3px; }
.center { text-align: center; margin: 1px 0; }
h2 { color: #1F3864; font-size: 11.5pt; text-transform: uppercase; border-bottom: 1.3px solid #1F3864;
     margin: 9px 0 4px; padding-bottom: 1px; }
.row { display: flex; justify-content: space-between; gap: 12px; font-weight: bold; margin-top: 5px; }
.row .right { font-weight: normal; white-space: nowrap; }
.sub { font-style: italic; color: #555; }
ul { margin: 2px 0 3px; padding-left: 17px; }
li { margin: 1.5px 0; }
p { margin: 2px 0; }
.letter p { margin: 0 0 9px; font-size: 11pt; line-height: 1.35; }
"""


def _e(text) -> str:
    return html.escape(str(text or ""))


def _header(cat: Catalogue, headline: str) -> str:
    ident = cat.identity
    sep = "&nbsp;&nbsp;|&nbsp;&nbsp;"
    contact = [ident.get("email"), ident.get("phone"), ident.get("location"), cat.work_rights.get("resume_line")]
    lines = [f"<h1>{_e(ident.get('name', '').upper())}</h1>",
             f"<p class='center'>{_e(headline)}</p>",
             f"<p class='center'>{sep.join(_e(c) for c in contact if c)}</p>"]
    links = [ident.get(k) for k in ("linkedin", "github", "portfolio") if ident.get(k)]
    if links:
        shown = [re.sub(r"^https?://(www\.)?", "", u).rstrip("/") for u in links]
        lines.append("<p class='center'>" + sep.join(f'<a href="{_e(u)}">{_e(s)}</a>' for u, s in zip(links, shown)) + "</p>")
    return "".join(lines)


def resume_html(r: TailoredResume, cat: Catalogue) -> str:
    out = [_header(cat, r.headline), "<h2>Professional Summary</h2>", f"<p>{_e(r.summary.text)}</p>"]
    out.append("<h2>Technical Skills</h2>")
    out += [f"<p><b>{_e(g.label)}:</b> {_e(', '.join(g.items))}</p>" for g in r.skills]

    for title, choices in (("Experience", r.experience), ("Projects", r.projects)):
        if not choices:
            continue
        out.append(f"<h2>{title}</h2>")
        for c in choices:
            e = cat.entries[c.ref]
            heading = _e(e.heading) + (f"&nbsp;&nbsp;|&nbsp;&nbsp;{_e(e.badge)}" if e.badge else "")
            right = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_e(x) for x in (e.dates, e.location) if x)
            out.append(f"<div class='row'><span>{heading}</span><span class='right'>{right}</span></div>")
            if e.subtitle:
                out.append(f"<div class='sub'>{_e(e.subtitle)}</div>")
            if c.bullets:
                out.append("<ul>" + "".join(f"<li>{_e(b.text)}</li>" for b in c.bullets) + "</ul>")

    out.append("<h2>Education</h2>")
    for ed in cat.education:
        right = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_e(x) for x in (fmt_range(ed.get("dates", "")), ed.get("location")) if x)
        out.append(f"<div class='row'><span>{_e(ed.get('degree'))}&nbsp;&nbsp;|&nbsp;&nbsp;{_e(ed.get('institution'))}</span>"
                   f"<span class='right'>{right}</span></div>")
    return _page("Resume", "".join(out))


def cover_letter_html(r: TailoredResume, cat: Catalogue, job) -> str:
    d = date.today()
    today = f"{d.day} {d.strftime('%B %Y')}"
    body = "".join(f"<p>{_e(b.text)}</p>" for b in r.cover_letter)
    name = cat.identity.get("name", "")
    content = (f"{_header(cat, r.headline)}<div class='letter' style='margin-top:22px'>"
               f"<p>{_e(today)}</p><p>Hiring Team<br>{_e(job.company)}</p>"
               f"<p><b>Re: {_e(job.title)}</b></p><p>Dear Hiring Team,</p>{body}"
               f"<p>Kind regards,<br>{_e(name)}</p></div>")
    return _page("Cover letter", content)


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>"


class Printer:
    """Prints HTML to A4 PDFs with one browser kept open for a batch (the page-fit loop prints repeatedly).
    Works on Windows, macOS and Linux via Playwright; see jobsearch/browser.py for which browser is used."""

    def __enter__(self):
        self._pw = sync_playwright().start()
        try:
            self._browser = browser.launch(self._pw, headless=True)
        except Exception:
            self._pw.stop()
            raise
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def pdf(self, html_text: str, out_pdf: Path) -> int:
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        self._page.set_content(html_text, wait_until="load")
        self._page.pdf(path=str(out_pdf), format="A4", print_background=True, prefer_css_page_size=True)
        return page_count(out_pdf)


def html_to_pdf(html_text: str, out_pdf: Path) -> int:
    """Print HTML to PDF and return its page count."""
    with Printer() as printer:
        return printer.pdf(html_text, out_pdf)


def page_count(pdf_path: Path) -> int:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()  # pdfium otherwise holds the file open and Windows blocks overwriting it


def _trim_one(r: TailoredResume) -> str | None:
    """Remove the single least important bullet. Returns a description, or None if nothing left to trim."""
    for c in reversed(r.projects):
        if len(c.bullets) > 1:
            c.bullets.pop()
            return f"trimmed a bullet from {c.ref}"
    if len(r.projects) > 1:
        dropped = r.projects.pop()
        return f"dropped project {dropped.ref}"
    for c in reversed(r.experience):
        if len(c.bullets) > 2:
            c.bullets.pop()
            return f"trimmed a bullet from {c.ref}"
    return None


def render_resume(r: TailoredResume, cat: Catalogue, out_pdf: Path) -> tuple[TailoredResume, list[str]]:
    """Render to one page, trimming as needed. Returns the content actually rendered and the trims made."""
    r = r.model_copy(deep=True)
    trims: list[str] = []
    with Printer() as printer:
        for _ in range(15):
            pages = printer.pdf(resume_html(r, cat), out_pdf)
            if pages <= 1:
                break
            trim = _trim_one(r)
            if trim is None:
                break
            trims.append(trim)
    out_pdf.with_suffix(".html").write_text(resume_html(r, cat), encoding="utf-8")
    return r, trims


def render_cover_letter(r: TailoredResume, cat: Catalogue, job, out_pdf: Path) -> None:
    html_to_pdf(cover_letter_html(r, cat, job), out_pdf)


def preview_png(pdf_path: Path, out_png: Path, scale: float = 1.4) -> Path:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        doc[0].render(scale=scale).to_pil().save(out_png)
    finally:
        doc.close()
    return out_png


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40]

