"""Loads YAML config and .env. Files are re-read on every call so edits apply to the next run."""

import copy
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobsearch.db"
LOG_DIR = DATA_DIR / "logs"

load_dotenv(ROOT / ".env")


EXAMPLES_DIR = CONFIG_DIR / "examples"


def _ensure(name: str) -> Path:
    """Your config files live only on your machine (git-ignored). On first run each one is created
    from the committed template in config/examples/."""
    path = CONFIG_DIR / name
    if not path.exists() and (EXAMPLES_DIR / name).exists():
        shutil.copyfile(EXAMPLES_DIR / name, path)
    return path


def _load(name: str) -> dict:
    with open(_ensure(name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save(name: str, data: dict, header: str) -> None:
    with open(CONFIG_DIR / name, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=110)


def save_profile(data: dict) -> None:
    """Write the whole profile (e.g. from the dashboard editor). Facts previously added through
    'Close the gaps' are already merged into `data`, so the additions file is folded in and cleared."""
    _save("profile.yaml", data, "# MASTER PROFILE: the single source of truth for every agent. Edited from the dashboard.\n"
                                "# Values that are exactly \"TODO\" are hidden from agents.\n\n")
    if PROFILE_ADDITIONS.exists():
        PROFILE_ADDITIONS.unlink()


def save_preferences(data: dict) -> None:
    _save("preferences.yaml", data, "# Search preferences and system limits. Edited from the dashboard.\n\n")


def save_watchlist(companies: list[dict]) -> None:
    _save("watchlist.yaml", {"companies": companies}, "# Companies polled through their public ATS job boards.\n\n")


def preferences() -> dict:
    return _load("preferences.yaml")


PROFILE_ADDITIONS = CONFIG_DIR / "profile_additions.yaml"
_ADDITIONS_HEADER = """# Facts and skills you added from the dashboard (Approvals -> Close the gaps).
# Merged into profile.yaml by every agent. Edit or delete lines freely; only keep what's true.
"""


def profile() -> dict:
    return merge_additions(_load("profile.yaml"), load_additions())


def load_additions() -> dict:
    data = {}
    if PROFILE_ADDITIONS.exists():
        with open(PROFILE_ADDITIONS, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return {"skills": data.get("skills") or {}, "facts": data.get("facts") or {}}


def merge_additions(base: dict, additions: dict) -> dict:
    p = copy.deepcopy(base)
    skills = p.setdefault("skills", {})
    for group, items in additions.get("skills", {}).items():
        existing = skills.setdefault(group, []) or []
        skills[group] = existing + [i for i in items if i not in existing]
    for section in ("experience", "projects"):
        for entry in p.get(section) or []:
            extra = additions.get("facts", {}).get(entry.get("id"), [])
            if extra:
                entry["facts"] = list(entry.get("facts") or []) + [f for f in extra if f not in (entry.get("facts") or [])]
    return p


def add_to_profile(kind: str, target: str, text: str) -> bool:
    """kind: 'skill' (target = skills group) or 'fact' (target = experience/project id).
    Returns False if it was already there."""
    text = " ".join(text.split())
    if not text:
        return False
    additions = load_additions()
    bucket = additions["skills" if kind == "skill" else "facts"].setdefault(target, [])
    current = merge_additions(_load("profile.yaml"), additions)
    if kind == "skill":
        already = text.lower() in (s.lower() for s in current.get("skills", {}).get(target) or [])
    else:
        entries = (current.get("experience") or []) + (current.get("projects") or [])
        already = any(text in (e.get("facts") or []) for e in entries if e.get("id") == target)
    if already:
        return False
    bucket.append(text)
    with open(PROFILE_ADDITIONS, "w", encoding="utf-8") as f:
        f.write(_ADDITIONS_HEADER)
        yaml.safe_dump(additions, f, sort_keys=False, allow_unicode=True, width=120)
    return True


def strip_todos(obj):
    """Drop TODO placeholders and `todo` notes so agents only ever see real facts."""
    if isinstance(obj, dict):
        return {k: strip_todos(v) for k, v in obj.items() if k != "todo" and v != "TODO"}
    if isinstance(obj, list):
        return [strip_todos(v) for v in obj]
    return obj


def watchlist() -> list[dict]:
    return _load("watchlist.yaml").get("companies", [])


def local_tz() -> ZoneInfo:
    return ZoneInfo(preferences().get("timezone", "Australia/Melbourne"))
