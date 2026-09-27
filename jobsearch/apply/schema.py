"""Reads an application form's questions from the job board's public form definition
(no browser, nothing entered). Greenhouse, Lever and Ashby are supported; everything else is 'guided'."""

import html
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from jobsearch.scout.sources.base import get_json, html_to_text, http

# text | textarea | email | phone | url | select | multiselect | radio | checkbox | boolean | file | location
@dataclass
class FormField:
    key: str                    # DOM id / name used to fill it
    label: str
    type: str
    required: bool = False
    options: list[str] = field(default_factory=list)
    section: str = "custom"     # standard | custom | eeo
    answer: str | list = ""
    source: str = ""            # profile | default | drafted | you | file
    needs_you: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


EEO = re.compile(r"gender|race|ethnic|hispanic|latin|veteran|disabilit|transgender|lgbt|sexual orientation|pronoun|"
                 r"aboriginal|torres strait|caregiver|age range|parents/guardians|neurodiver|religio", re.I)


def detect(job) -> tuple[str, str, dict]:
    """Return (ats, apply_url, ids) for the job's best direct ATS source, or ('guided', url, {})."""
    for src in job.sources:
        url = src.get("url", "")
        if src["source"] == "greenhouse":
            m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url) or re.search(r"/boards/([^/]+)/jobs/(\d+)", url)
            if m:
                slug, jid = m.groups()
                return "greenhouse", f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}", {"slug": slug, "id": jid}
        if src["source"] == "lever":
            m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url)
            if m:
                slug, jid = m.groups()
                return "lever", f"https://jobs.lever.co/{slug}/{jid}/apply", {"slug": slug, "id": jid}
        if src["source"] == "ashby":
            m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
            if m:
                slug, jid = m.groups()
                return "ashby", f"https://jobs.ashbyhq.com/{slug}/{jid}/application", {"slug": slug, "id": jid}
    return "guided", job.url, {}


def _is_eeo(label: str) -> bool:
    return bool(EEO.search(label))


def greenhouse_fields(ids: dict) -> list[FormField]:
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{ids['slug']}/jobs/{ids['id']}", params={"questions": "true"})
    if not d:
        raise RuntimeError("Greenhouse form definition unavailable")
    out: list[FormField] = []
    for q in d.get("questions", []):
        label = q["label"]
        for f in q["fields"]:
            name, ftype = f["name"], f["type"]
            if name.endswith("_text"):
                continue  # "or paste as text" alternatives to file uploads
            t = {"input_text": "text", "textarea": "textarea", "input_file": "file",
                 "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}.get(ftype, "text")
            if name == "email":
                t = "email"
            if name == "phone":
                t = "phone"
            out.append(FormField(key=name, label=label, type=t, required=bool(q.get("required")),
                                 options=[v["label"] for v in f.get("values", [])],
                                 section="standard" if not name.startswith("question_") else ("eeo" if _is_eeo(label) else "custom")))
    # The hosted form also asks for a city, which the API lists separately
    if d.get("location_questions"):
        out.append(FormField(key="candidate-location", label="Location (City)", type="location", required=True, section="standard"))
    for block in d.get("compliance") or []:
        for q in block.get("questions", []):
            for f in q["fields"]:
                out.append(FormField(key=f["name"], label=q["label"], type="select", required=bool(q.get("required")),
                                     options=[v["label"] for v in f.get("values", [])], section="eeo"))
    demo = d.get("demographic_questions") or {}
    for q in demo.get("questions", []):
        out.append(FormField(key=str(q["id"]), label=q["label"],
                             type="multiselect" if "multi" in q.get("type", "") else "select",
                             required=bool(q.get("required")),
                             options=[a["label"] for a in q.get("answer_options", [])], section="eeo"))
    return out


def lever_fields(ids: dict) -> list[FormField]:
    r = http().get(f"https://jobs.lever.co/{ids['slug']}/{ids['id']}/apply")
    if r.status_code != 200:
        raise RuntimeError(f"Lever form unavailable (HTTP {r.status_code})")
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    out: dict[str, FormField] = {}
    for el in form.find_all(["input", "textarea", "select"]):
        name, typ = el.get("name", ""), (el.get("type") or el.name)
        if not name or typ in ("hidden", "submit") or name.startswith(("h-captcha", "g-recaptcha")):
            continue
        box = el.find_parent("li", class_="application-question") or el.find_parent("li")
        label_el = (box.find(class_="application-label") or box.find(class_="text")) if box else None
        label = re.sub(r"\s+", " ", (label_el or box or el).get_text(" ", strip=True)).replace("✱", "").strip()[:200]
        if label.startswith("Select"):  # EEO dropdowns have no visible label, only their options
            label = name.replace("eeo[", "").rstrip("]").title()
        if name in out:  # radios/checkboxes: one field, many options
            if typ in ("radio", "checkbox") and el.get("value"):
                out[name].options.append(el["value"])
            continue
        t = {"file": "file", "email": "email", "textarea": "textarea", "select": "select", "radio": "radio",
             "checkbox": "checkbox"}.get(typ, "text")
        if name == "location":
            t = "location"
        opts = [o.get_text(strip=True) for o in el.find_all("option") if o.get("value")] if el.name == "select" else (
            [el["value"]] if typ in ("radio", "checkbox") and el.get("value") else [])
        section = "eeo" if (name.startswith(("eeo[", "surveysResponses")) or _is_eeo(label)) else (
            "standard" if name in ("name", "email", "phone", "location", "org", "resume") or name.startswith("urls[") else "custom")
        out[name] = FormField(key=name, label=label or name, type=t,
                              required=el.has_attr("required") or "✱" in (box.get_text() if box else ""),
                              options=opts, section=section)
    return list(out.values())


ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    applicationForm { sections { fieldEntries { ... on FormFieldEntry { field isRequired descriptionHtml } } } } } }"""


def ashby_fields(ids: dict) -> list[FormField]:
    r = http().post("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
                    json={"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
                          "variables": {"organizationHostedJobsPageName": ids["slug"], "jobPostingId": ids["id"]}})
    jp = ((r.json() if r.status_code == 200 else {}).get("data") or {}).get("jobPosting")
    if not jp:
        raise RuntimeError("Ashby form definition unavailable")
    out = []
    for sec in jp["applicationForm"]["sections"]:
        for fe in sec["fieldEntries"]:
            f = fe.get("field") or {}
            path, title = f.get("path", ""), html.unescape(f.get("title", ""))
            desc = html_to_text(fe.get("descriptionHtml") or "")
            t = {"String": "text", "Email": "email", "Phone": "phone", "LongText": "textarea", "File": "file",
                 "Boolean": "boolean", "Location": "location", "ValueSelect": "select",
                 "MultiValueSelect": "multiselect", "Date": "text", "Number": "text"}.get(f.get("type"), "text")
            opts = [v.get("label", "") for v in f.get("selectableValues") or []] or (["Yes", "No"] if t == "boolean" else [])
            section = "standard" if path.startswith("_systemfield") else ("eeo" if _is_eeo(title) else "custom")
            out.append(FormField(key=path, label=title + (f" ({desc})" if desc and len(desc) < 300 else ""), type=t,
                                 required=bool(fe.get("isRequired")), options=opts, section=section))
    return out


# Guided applications: the screening questions most AU application forms (Workday, SuccessFactors, Seek) ask
GUIDED_QUESTIONS = [
    ("work_rights", "Do you have the right to work in Australia? What is your visa status?", "textarea"),
    ("sponsorship", "Will you require visa sponsorship now or in the future?", "textarea"),
    ("salary", "What are your salary expectations?", "text"),
    ("notice", "What is your notice period / earliest start date?", "text"),
    ("relocate", "Are you willing to relocate?", "text"),
    ("why_role", "Why are you interested in this role?", "textarea"),
    ("why_company", "Why do you want to work for this company?", "textarea"),
    ("relevant_experience", "Describe your most relevant experience for this role.", "textarea"),
]


def guided_fields() -> list[FormField]:
    return [FormField(key=k, label=label, type=t, required=True, section="custom") for k, label, t in GUIDED_QUESTIONS]


def read_form(job) -> tuple[str, str, list[FormField]]:
    ats, url, ids = detect(job)
    if ats == "greenhouse":
        return ats, url, greenhouse_fields(ids)
    if ats == "lever":
        return ats, url, lever_fields(ids)
    if ats == "ashby":
        return ats, url, ashby_fields(ids)
    return "guided", url, guided_fields()
