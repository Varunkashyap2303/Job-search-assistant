"""Dashboard tab: build and edit your profile (the source of truth every agent works from)."""

import streamlit as st

from jobsearch import llm
from jobsearch.config import profile, save_profile
from jobsearch.onboarding import import_resume, merge_import, resume_text

WORK_STATUS = {"citizen": "Australian citizen", "permanent_resident": "Permanent resident", "visa": "Visa holder"}


def _lines(text: str) -> list[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip(" -•\t")]


def _csv(text: str) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _save(prof: dict, message: str) -> None:
    save_profile(prof)
    st.session_state["profile_saved"] = message
    st.rerun()


def _entry_form(kind: str, entry: dict, idx: int | None) -> dict | None:
    """Form for one experience/project entry. Returns the edited entry, {} to delete, or None if untouched."""
    key = f"{kind}_{idx if idx is not None else 'new'}"
    with st.form(key):
        c1, c2 = st.columns(2)
        if kind == "experience":
            title = c1.text_input("Job title", entry.get("title", ""))
            company = c2.text_input("Company", entry.get("company", ""))
            c3, c4, c5 = st.columns(3)
            location = c3.text_input("Location", entry.get("location", ""))
            start = c4.text_input("Start (YYYY-MM)", str(entry.get("start", "")))
            end = c5.text_input("End (YYYY-MM or present)", str(entry.get("end", "present")))
            project = st.text_input("Main project or product (optional)", entry.get("project", ""))
        else:
            title = c1.text_input("Project name", entry.get("name", ""))
            company = c2.text_input("Dates (e.g. 2025-07 to 2025-11)", str(entry.get("dates", "")))
            project = st.text_input("Award or recognition (optional)", entry.get("award", ""))
            location = start = end = ""
        stack = st.text_input("Tools / technologies (comma separated)", ", ".join(entry.get("stack") or []))
        facts = st.text_area("What you did: one fact per line. Keep numbers exact; only what's true.",
                             "\n".join(entry.get("facts") or []), height=160)
        a, b = st.columns([1, 1])
        saved = a.form_submit_button("Save" if idx is not None else "Add", type="primary")
        delete = b.form_submit_button("Delete", disabled=idx is None)
    if delete:
        return {}
    if not saved:
        return None
    out = dict(entry)
    if kind == "experience":
        out.update(title=title, company=company, location=location, start=start, end=end or "present", project=project)
    else:
        out.update(name=title, dates=company, award=project)
    out.update(stack=_csv(stack), facts=_lines(facts))
    if not out.get("id"):
        from jobsearch.onboarding import _slug

        taken = {e.get("id") for e in (profile().get("experience") or []) + (profile().get("projects") or [])}
        out["id"] = _slug(company if kind == "experience" else title, set(filter(None, taken)))
    return out


def render() -> None:
    if msg := st.session_state.pop("profile_saved", None):
        st.success(msg)
    prof = profile()
    st.caption("Your profile is the only source of truth the agents use: resumes, cover letters, answers and "
               "outreach can only claim what's written here. It's stored in config/profile.yaml on this machine.")

    # ---- import ----
    st.subheader("1. Start from your resume")
    up = st.file_uploader("Upload your resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"], key="resume_upload")
    pasted = st.text_area("…or paste it here", key="resume_paste", height=100)
    if st.button("Read my resume into a profile (Opus, ~$0.10)", disabled=not (up or pasted.strip())):
        text = resume_text(up.name, up.getvalue()) if up else pasted
        if len(text.strip()) < 200:
            st.error("That file had very little readable text (a scanned PDF?). Try the DOCX, or paste the text.")
        else:
            with st.spinner("Reading your resume..."), llm.user_initiated():
                try:
                    st.session_state["imported_profile"] = import_resume(text)
                except (llm.BudgetExceeded, llm.LLMError) as e:
                    st.error(str(e))
    imp = st.session_state.get("imported_profile")
    if imp:
        n_facts = sum(len(e["facts"]) for e in imp["experience"] + imp["projects"])
        st.info(f"Found {imp['identity']['name'] or 'your details'}: {len(imp['experience'])} role(s), "
                f"{len(imp['projects'])} project(s), {n_facts} facts, {len(imp['education'])} qualification(s). "
                "Check it below before saving.")
        with st.expander("What was extracted"):
            st.json(imp)
        if prof.get("experience"):
            st.warning("Saving replaces your summary, skills, experience, projects and education. "
                       "Work rights and application answers are kept.")
        a, b = st.columns(2)
        if a.button("Save to my profile", type="primary"):
            st.session_state.pop("imported_profile")
            _save(merge_import(prof, imp), "Profile saved from your resume. Review and add detail below.")
        if b.button("Discard"):
            st.session_state.pop("imported_profile")
            st.rerun()

    # ---- edit ----
    st.subheader("2. Check and add detail")
    st.caption("Add anything true that your resume leaves out: numbers, scope, tools, outcomes. "
               "The more facts here, the better every tailored resume gets.")

    ident = prof.get("identity") or {}
    with st.expander("Contact details", expanded=not ident.get("name")):
        with st.form("identity"):
            c1, c2 = st.columns(2)
            vals = {
                "name": c1.text_input("Full name", ident.get("name", "")),
                "headline": c2.text_input("Headline (line under your name)", ident.get("headline", "")),
                "email": c1.text_input("Email", ident.get("email", "")),
                "phone": c2.text_input("Phone", ident.get("phone", "")),
                "location": c1.text_input("Location (e.g. Brisbane, QLD)", ident.get("location", "")),
                "linkedin": c2.text_input("LinkedIn URL", ident.get("linkedin", "")),
                "github": c1.text_input("GitHub URL", ident.get("github", "")),
                "portfolio": c2.text_input("Portfolio URL", ident.get("portfolio", "")),
            }
            if st.form_submit_button("Save contact details", type="primary"):
                prof["identity"] = {**ident, **vals}
                _save(prof, "Contact details saved.")

    wr = prof.get("work_rights") or {}
    with st.expander("Work rights"):
        with st.form("work_rights"):
            status = st.selectbox("Status", list(WORK_STATUS), format_func=WORK_STATUS.get,
                                  index=list(WORK_STATUS).index(wr.get("status") or ("visa" if wr.get("visa") else "citizen")))
            c1, c2 = st.columns(2)
            visa = c1.text_input("Visa (if a visa holder)", wr.get("visa", ""))
            expiry = c2.text_input("Visa expiry (YYYY-MM-DD)", str(wr.get("visa_expiry", "")))
            full = st.checkbox("Full work rights", wr.get("full_work_rights", True))
            now = st.checkbox("Need visa sponsorship now", wr.get("requires_sponsorship_now", False))
            later = st.checkbox("Would like an employer who can sponsor later",
                                wr.get("wants_sponsorship_later", wr.get("wants_sponsorship_after_expiry", False)))
            clearance = st.checkbox("Can hold an Australian security clearance", wr.get("can_hold_clearance", status == "citizen"))
            line = st.text_input("Line shown on your resume", wr.get("resume_line", "Full Australian work rights"))
            notes = st.text_input("Anything else employers should know (optional)", wr.get("notes", ""))
            if st.form_submit_button("Save work rights", type="primary"):
                prof["work_rights"] = {**wr, "status": status, "visa": visa, "visa_expiry": expiry, "full_work_rights": full,
                                       "requires_sponsorship_now": now, "wants_sponsorship_later": later,
                                       "can_hold_clearance": clearance, "resume_line": line, "notes": notes}
                _save(prof, "Work rights saved. Job filtering and application answers now follow them.")

    with st.expander("Summary and skills"):
        with st.form("summary_skills"):
            summary = st.text_area("Professional summary", " ".join(str(prof.get("summary") or "").split()), height=120)
            skills_text = "\n".join(f"{g}: {', '.join(items)}" for g, items in (prof.get("skills") or {}).items())
            skills = st.text_area("Skills: one group per line as 'group: skill, skill, skill'", skills_text, height=160)
            if st.form_submit_button("Save summary and skills", type="primary"):
                groups = {}
                for line in _lines(skills):
                    group, _, items = line.partition(":")
                    if items.strip():
                        groups[group.strip().lower().replace(" ", "_")] = _csv(items)
                prof["summary"], prof["skills"] = summary, groups
                _save(prof, "Summary and skills saved.")

    for kind, label in (("experience", "Experience"), ("projects", "Projects")):
        st.markdown(f"**{label}**")
        entries = list(prof.get(kind) or [])
        for i, e in enumerate(entries):
            title = f"{e.get('title', '')} | {e.get('company', '')}" if kind == "experience" else e.get("name", "")
            with st.expander(f"{title} ({len(e.get('facts') or [])} facts)"):
                edited = _entry_form(kind, e, i)
                if edited is not None:
                    if edited:
                        entries[i] = edited
                    else:
                        entries.pop(i)
                    prof[kind] = entries
                    _save(prof, f"{label} saved.")
        with st.expander(f"Add {'a role' if kind == 'experience' else 'a project'}"):
            new = _entry_form(kind, {}, None)
            if new:
                prof[kind] = entries + [new]
                _save(prof, f"Added to {label.lower()}.")

    with st.expander("Education, certifications and awards"):
        with st.form("education"):
            edu_text = "\n".join(" | ".join(str(e.get(k, "")) for k in ("degree", "institution", "location", "dates"))
                                 for e in prof.get("education") or [])
            edu = st.text_area("One per line: degree | institution | location | dates", edu_text, height=100)
            certs = st.text_area("Certifications, one per line", "\n".join(prof.get("certifications") or []))
            awards = st.text_area("Awards, one per line", "\n".join(prof.get("awards") or []))
            if st.form_submit_button("Save", type="primary"):
                rows = []
                for line in _lines(edu):
                    parts = [p.strip() for p in line.split("|")] + ["", "", "", ""]
                    rows.append(dict(zip(("degree", "institution", "location", "dates"), parts[:4])))
                prof["education"], prof["certifications"], prof["awards"] = rows, _lines(certs), _lines(awards)
                _save(prof, "Education saved.")

    app = prof.get("application") or {}
    with st.expander("Answers for application forms"):
        st.caption("Used to fill application forms. Leave anything you'd rather decide per job empty; "
                   "it will show up as 'Needs you'.")
        with st.form("application"):
            c1, c2 = st.columns(2)
            fields = [("first_name", "First name"), ("last_name", "Last name"), ("phone_international", "Phone (+61 ...)"),
                      ("city", "City"), ("location", "Location (City, State, Australia)"), ("current_company", "Current employer"),
                      ("current_title", "Current title"), ("salary_expectation", "Salary expectation"),
                      ("notice_period", "Notice period"), ("earliest_start", "Earliest start"),
                      ("willing_to_relocate", "Willing to relocate?"), ("pronouns", "Pronouns (optional)"),
                      ("how_did_you_hear", "How did you hear about the job (default)")]
            vals = {}
            for i, (k, lab) in enumerate(fields):
                v = app.get(k, "")
                vals[k] = (c1 if i % 2 == 0 else c2).text_input(lab, "" if v == "TODO" else str(v or ""))
            eeo = st.selectbox("Demographic / diversity questions", ["decline", "ask me"],
                               index=0 if app.get("eeo_default", "decline") == "decline" else 1)
            if st.form_submit_button("Save answers", type="primary"):
                prof["application"] = {**app, **{k: (v if v else "TODO") for k, v in vals.items()},
                                       "eeo_default": eeo, "pronouns": vals["pronouns"]}
                _save(prof, "Application answers saved.")
