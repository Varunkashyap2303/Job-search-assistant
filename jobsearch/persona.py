"""Everything the agents need to know about *this* user, derived from their profile and preferences.
No prompt should hard-code a name, city, visa or target role; they come from here."""

from jobsearch.config import preferences, profile, strip_todos

CLEARANCE_PATTERNS = [r"\bNV1\b|\bNV2\b|\bPV\b clearance", r"baseline (security )?clearance", r"security clearance", r"\bAGSVA\b"]
CITIZEN_ONLY_PATTERNS = [r"australian citizens? only", r"must be an australian citizen", r"citizenship (is )?required"]


def _p() -> dict:
    return strip_todos(profile())


def name() -> str:
    return _p().get("identity", {}).get("name", "")


def first_name() -> str:
    p = _p()
    return p.get("application", {}).get("first_name") or (name().split()[0] if name() else "")


def home_location() -> str:
    return _p().get("identity", {}).get("location", "")


def headline() -> str:
    return _p().get("identity", {}).get("headline", "")


def target_roles() -> list[str]:
    return preferences().get("search", {}).get("target_roles") or []


def avoid_roles() -> str:
    return preferences().get("search", {}).get("avoid_roles") or ""


def work_rights_status() -> str:
    wr = _p().get("work_rights", {})
    if wr.get("status"):
        return wr["status"]
    return "visa" if wr.get("visa") else "citizen"


def work_rights_text() -> str:
    """One paragraph the models can rely on for work-rights questions and filtering."""
    wr = _p().get("work_rights", {})
    status = work_rights_status()
    if status == "citizen":
        text = "Australian citizen with full work rights; can meet citizenship requirements."
        if wr.get("can_hold_clearance", True):
            text += " Eligible to apply for security clearances."
    elif status == "permanent_resident":
        text = ("Australian permanent resident with full work rights; no sponsorship needed. Cannot meet "
                "citizenship-only or security-clearance requirements.")
    else:
        rights = "full" if wr.get("full_work_rights", True) else "limited"
        visa = wr.get("visa", "a temporary visa")
        expiry = f" until {wr['visa_expiry']}" if wr.get("visa_expiry") else ""
        now = "requires visa sponsorship now" if wr.get("requires_sponsorship_now") else "does not need sponsorship now"
        text = f"Holds {visa} with {rights} work rights{expiry}; {now}. Cannot meet citizenship, permanent-residency or security-clearance requirements."
        if wr.get("wants_sponsorship_after_expiry") or wr.get("wants_sponsorship_later"):
            text += " Would like an employer able to sponsor a visa after the current one expires, so sponsorship signals are a mild plus."
    if wr.get("notes"):
        text += f" {wr['notes']}"
    return text


def hard_exclude_patterns() -> list[str]:
    """Posting phrases that rule a job out for this user before any LLM call.
    An explicit list in preferences.work_rights overrides the derived one."""
    explicit = preferences().get("work_rights", {}).get("hard_exclude_patterns")
    if explicit is not None:
        return explicit
    status = work_rights_status()
    wr = _p().get("work_rights", {})
    if status == "citizen" and wr.get("can_hold_clearance", True):
        return []
    return CLEARANCE_PATTERNS + (CITIZEN_ONLY_PATTERNS if status != "citizen" else [])


def role_family() -> str:
    """A short plural for asks like 'are you hiring ...?'"""
    roles = target_roles()
    return (roles[0] + "s") if roles else "people with my background"


def is_set_up() -> dict[str, bool]:
    """Setup checklist used by the dashboard."""
    import os

    p, prefs = _p(), preferences()
    has_experience = any((e.get("facts") for e in (p.get("experience") or []) + (p.get("projects") or [])))
    return {
        "api_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "profile": bool(p.get("identity", {}).get("name")) and has_experience,
        "preferences": bool(prefs.get("search", {}).get("target_roles")) and bool(prefs.get("search", {}).get("title_include")),
    }
