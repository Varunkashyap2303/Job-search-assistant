"""Dashboard tab: what jobs you want, which companies to watch, budget and API keys."""

import os

import streamlit as st
from dotenv import set_key

from jobsearch import llm
from jobsearch.config import ROOT, preferences, profile, save_preferences, save_watchlist, strip_todos, watchlist
from jobsearch.onboarding import apply_search_settings, generate_search_settings, keyword_to_regex

AU_CITIES = ["Melbourne", "Sydney", "Brisbane", "Perth", "Adelaide", "Canberra", "Hobart", "Darwin",
             "Gold Coast", "Geelong", "Newcastle", "Wollongong", "Sunshine Coast", "Townsville", "Cairns"]
ENV_KEYS = [("ANTHROPIC_API_KEY", "Anthropic API key (required)"),
            ("ANTHROPIC_WORKSPACE_ID", "Anthropic workspace ID (only for org-level keys)"),
            ("ADZUNA_APP_ID", "Adzuna app ID (free, optional)"), ("ADZUNA_APP_KEY", "Adzuna app key"),
            ("JOOBLE_API_KEY", "Jooble API key (free, optional)")]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _search_settings() -> None:
    prefs = preferences()
    search = prefs.get("search") or {}
    st.subheader("What are you looking for?")
    desc = st.text_area(
        "Describe the jobs you want in your own words: roles, level, industries, locations, what to avoid.",
        search.get("looking_for", ""), height=130, key="looking_for",
        placeholder="e.g. Junior to mid data analyst roles in Brisbane or remote. SQL and Power BI heavy, "
                    "ideally in health or government. Not sales or call-centre roles.")
    if st.button("Suggest search settings (Sonnet, ~$0.01)", disabled=not desc.strip()):
        summary = strip_todos(profile()).get("summary", "")
        with st.spinner("Working out search settings..."), llm.user_initiated():
            try:
                st.session_state["suggested_prefs"] = apply_search_settings(prefs, desc, generate_search_settings(desc, summary))
            except (llm.BudgetExceeded, llm.LLMError) as e:
                st.error(str(e))
    shown = st.session_state.get("suggested_prefs") or prefs
    if "suggested_prefs" in st.session_state:
        st.info("Suggested settings are filled in below. Adjust anything, then save.")
    s = shown.get("search") or {}
    locs = s.get("locations") or {}
    with st.form("search_settings"):
        c1, c2 = st.columns(2)
        roles = c1.text_area("Target job titles (one per line)", "\n".join(s.get("target_roles") or []), height=140)
        queries = c2.text_area("Job board searches (one per line)", "\n".join(s.get("queries") or []), height=140)
        include = c1.text_area("A relevant job title contains one of (keywords or regex, one per line)",
                               "\n".join(s.get("title_include") or []), height=140)
        exclude = c2.text_area("Skip titles containing (keywords or regex, one per line)",
                               "\n".join(s.get("title_exclude") or []), height=140)
        avoid = st.text_input("Roles to rank lower", s.get("avoid_roles", ""))
        preferred = st.multiselect("Preferred cities", sorted(set(AU_CITIES) | set(locs.get("preferred") or [])),
                                   default=locs.get("preferred") or [])
        c3, c4, c5 = st.columns(3)
        remote = c3.checkbox("Remote in Australia is fine", locs.get("allow_remote_au", True))
        max_age = c4.number_input("Ignore jobs older than (days)", 1, 90, int(s.get("max_age_days", 30)))
        threshold = c5.number_input("Shortlist at fit score", 0, 100, int(shown.get("scoring", {}).get("shortlist_threshold", 70)))
        intel = shown.get("intel") or {}
        topic = st.text_input("Startup research focus (for the funded-startups list)", intel.get("topic", "AI"))
        if st.form_submit_button("Save search settings", type="primary"):
            new = dict(shown)
            new["search"] = {**s, "looking_for": desc, "target_roles": _lines(roles), "queries": _lines(queries),
                             "title_include": [keyword_to_regex(x) for x in _lines(include)],
                             "title_exclude": [keyword_to_regex(x) for x in _lines(exclude)],
                             "avoid_roles": avoid, "max_age_days": int(max_age),
                             "locations": {**locs, "preferred": preferred, "allow_remote_au": remote,
                                           "acceptable": [c for c in (locs.get("acceptable") or AU_CITIES) if c not in preferred]}}
            new["scoring"] = {**(shown.get("scoring") or {}), "shortlist_threshold": int(threshold)}
            new["intel"] = {**intel, "topic": topic}
            save_preferences(new)
            st.session_state.pop("suggested_prefs", None)
            st.success("Saved. The next scout run uses these settings.")


def _watchlist() -> None:
    st.subheader("Company watchlist")
    st.caption("Companies whose own job boards (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) are checked "
               "on every scout run. Tick 'any role' for companies where every role interests you.")
    companies = watchlist()
    edited = st.data_editor(
        [{"name": c.get("name"), "ats": c.get("ats"), "slug": c.get("slug"), "any role": bool(c.get("ai_native"))}
         for c in companies],
        num_rows="dynamic", width="stretch", key="watchlist_editor",
        column_config={"ats": st.column_config.SelectboxColumn(
            "ats", options=["greenhouse", "lever", "ashby", "workable", "smartrecruiters"])})
    c1, c2, c3 = st.columns([2, 1, 1])
    name = c1.text_input("Add a company by name", key="watch_add", placeholder="e.g. Canva")
    if c2.button("Find its job board", disabled=not name.strip()):
        from jobsearch.intel.enrich import detect_ats

        with st.spinner(f"Looking for {name}'s job board..."):
            found = detect_ats(name.strip())
        if found:
            companies.append({"name": name.strip(), "ats": found["ats"], "slug": found["slug"]})
            save_watchlist(companies)
            st.success(f"Added {name} ({found['ats']}, {found['open_roles']} open roles).")
            st.rerun()
        st.warning(f"No public job board with Australian roles found for '{name}'. Add it by hand in the table "
                   "if you know its board.")
    if c3.button("Save table changes"):
        rows = [r for r in edited if r.get("name") and r.get("ats") and r.get("slug")]
        save_watchlist([{"name": r["name"], "ats": r["ats"], "slug": r["slug"], **({"ai_native": True} if r["any role"] else {})}
                        for r in rows])
        st.success(f"Watchlist saved ({len(rows)} companies).")


def _budget_and_keys() -> None:
    prefs = preferences()
    b = prefs.get("budget") or {}
    st.subheader("Budget")
    with st.form("budget"):
        c1, c2 = st.columns(2)
        daily = c1.number_input("Daily cap (USD)", 0.0, 100.0, float(b.get("daily_usd", 1.5)), step=0.25)
        monthly = c2.number_input("Monthly cap (USD)", 0.0, 1000.0, float(b.get("monthly_usd", 40.0)), step=5.0)
        shares = dict(b.get("per_agent_daily") or {})
        cols = st.columns(max(1, len(shares)))
        for col, (agent, val) in zip(cols, list(shares.items())):
            shares[agent] = col.number_input(f"{agent} share", 0.0, 100.0, float(val), step=0.05)
        if st.form_submit_button("Save budget", type="primary"):
            prefs["budget"] = {**b, "daily_usd": daily, "monthly_usd": monthly, "per_agent_daily": shares}
            save_preferences(prefs)
            st.success("Budget saved.")

    st.subheader("API keys")
    st.caption("Stored in the .env file on this machine only. Leave a box empty to keep the current value.")
    with st.form("api_keys"):
        values = {k: st.text_input(f"{label}{' (set)' if os.getenv(k) else ''}", type="password", key=f"env_{k}")
                  for k, label in ENV_KEYS}
        if st.form_submit_button("Save keys", type="primary"):
            env_path = ROOT / ".env"
            env_path.touch(exist_ok=True)
            for k, v in values.items():
                if v.strip():
                    set_key(str(env_path), k, v.strip())
                    os.environ[k] = v.strip()
            llm._client = None  # pick up the new key on the next call
            st.success("Keys saved.")


def render() -> None:
    _search_settings()
    st.divider()
    _watchlist()
    st.divider()
    _budget_and_keys()
