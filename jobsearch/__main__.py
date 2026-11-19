"""CLI entry point: python -m jobsearch <command>"""

import argparse
import json
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler

from jobsearch.config import LOG_DIR, ROOT


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(LOG_DIR / "jobsearch.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def user_scope(targeted: bool):
    """Runs aimed at one specific item are your explicit choice, so they skip per-agent shares."""
    import contextlib

    from jobsearch import llm

    return llm.user_initiated() if targeted else contextlib.nullcontext()


def cmd_scout(args) -> None:
    from jobsearch.scout.pipeline import run_scout

    stats = run_scout(score=not args.no_score, score_limit=args.limit)
    print(json.dumps(stats, indent=2, sort_keys=True))


def cmd_tailor(args) -> None:
    from jobsearch.tailor.pipeline import run_tailor

    with user_scope(args.job is not None):
        print(json.dumps(run_tailor(limit=args.limit, job_id=args.job), indent=2, sort_keys=True))


def cmd_intel(args) -> None:
    from jobsearch.intel.pipeline import run_intel

    with user_scope(args.startup is not None):
        stats = run_intel(profile_limit=args.profiles, startup_id=args.startup, report=args.startup is not None)
    if args.startup is None:
        # The morning run: draft outreach for your shortlist and the best new startups, then write the brief
        from jobsearch.config import preferences
        from jobsearch.intel.report import write_report
        from jobsearch.outreach.pipeline import run_outreach

        auto = preferences()["outreach"].get("auto_draft", True) and not args.no_outreach
        stats.update({f"outreach.{k}": v for k, v in run_outreach(auto=auto).items()})
        stats["report"] = write_report(stats)

        from jobsearch import notify

        stats["telegram"] = notify.morning_update(stats["report"])
    print(json.dumps(stats, indent=2, sort_keys=True))


def cmd_telegram(args) -> None:
    from jobsearch import notify

    if args.action == "setup":
        chats = notify.recent_chats()
        if not chats:
            print("No messages found. Open your bot in Telegram, send it any message, then run this again.")
        for chat_id, name in chats:
            print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")
    elif args.action == "test":
        notify.send_text("Job search connected. Your morning startup brief will arrive here after the 06:00 run.")
        print("Sent a test message.")
    elif args.action == "preview":
        from jobsearch import digest

        message = digest.morning_message()
        print(f"[{message[0]}]\n{message[1]}" if message else "Nothing new or pending: no message would be sent.")
    elif args.action == "baseline":
        from jobsearch import digest

        startups, drafts = digest.mark_all_seen()
        print(f"Marked {startups} startup(s) and {drafts} draft(s) as already seen.")
    else:
        from jobsearch.config import DATA_DIR

        reports = sorted((DATA_DIR / "reports").glob("intel_*.md"))
        if not reports:
            print("No brief yet. Run `python -m jobsearch intel` first.")
            return
        print(notify.deliver_brief(str(reports[-1])))


def cmd_outreach(args) -> None:
    from jobsearch.outreach.pipeline import run_outreach

    with user_scope(args.startup is not None):
        print(json.dumps(run_outreach(startup_id=args.startup, limit=args.limit), indent=2, sort_keys=True))


def cmd_mcp(args) -> None:
    from jobsearch.mcp_server import serve

    serve()


def cmd_schedule(args) -> None:
    from jobsearch import schedule

    print({"show": schedule.show, "install": schedule.install, "remove": schedule.remove}[args.action]())


def cmd_status(args) -> None:
    from sqlalchemy import func
    from sqlmodel import select

    from jobsearch import llm
    from jobsearch.config import preferences
    from jobsearch.db import Job, session

    budget = preferences()["budget"]
    with session() as s:
        counts = dict(s.exec(select(Job.status, func.count()).group_by(Job.status)).all())
    print(f"Spend today:  ${llm.spend_today():.2f} / ${budget['daily_usd']:.2f}")
    print(f"Spend month:  ${llm.spend_this_month():.2f} / ${budget['monthly_usd']:.2f}")
    print("Jobs by status:", json.dumps(counts, indent=2))


def cmd_dashboard(args) -> None:
    # Streamlit only re-imports changed modules that live in the script's folder or on PYTHONPATH.
    # Putting the project root on PYTHONPATH means edits to jobsearch/ are picked up on the next page
    # load instead of leaving stale modules in memory (the source of ImportErrors after updates).
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
    cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py"), "--server.port", str(args.port)]
    if args.headless:
        cmd += ["--server.headless", "true"]
    subprocess.run(cmd, cwd=ROOT, env=env, check=False)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="jobsearch")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scout", help="fetch, filter, dedupe and score jobs")
    p.add_argument("--no-score", action="store_true", help="skip LLM scoring (no token spend)")
    p.add_argument("--limit", type=int, default=None, help="max jobs to score this run")
    p.set_defaults(func=cmd_scout)

    p = sub.add_parser("tailor", help="tailor resumes for shortlisted jobs (newest first)")
    p.add_argument("--job", type=int, default=None, help="tailor one specific job id")
    p.add_argument("--limit", type=int, default=None, help="max resumes this run")
    p.set_defaults(func=cmd_tailor)

    p = sub.add_parser("intel", help="morning run: funded AU startups likely to hire AI engineers, profiles, outreach drafts, brief")
    p.add_argument("--profiles", type=int, default=None, help="max Opus profiles this run")
    p.add_argument("--startup", type=int, default=None, help="(re)profile one startup id only")
    p.add_argument("--no-outreach", action="store_true", help="only draft for startups you shortlisted")
    p.set_defaults(func=cmd_intel)

    p = sub.add_parser("outreach", help="draft outreach for shortlisted startups (never sends anything)")
    p.add_argument("--startup", type=int, default=None, help="draft for one startup id")
    p.add_argument("--limit", type=int, default=None, help="max startups this run")
    p.set_defaults(func=cmd_outreach)

    p = sub.add_parser("telegram", help="morning message on Telegram: setup (find your chat id), test, "
                                        "preview (tomorrow's message, not sent), send (full brief now), "
                                        "baseline (mark everything current as seen)")
    p.add_argument("action", choices=["setup", "test", "preview", "send", "baseline"])
    p.set_defaults(func=cmd_telegram)

    sub.add_parser("mcp", help="MCP server over stdio for Hermes Agent, Claude Desktop or Claude Code").set_defaults(func=cmd_mcp)

    p = sub.add_parser("schedule", help="daily runs via Task Scheduler (Windows), launchd (macOS) or cron (Linux)")
    p.add_argument("action", choices=["show", "install", "remove"])
    p.set_defaults(func=cmd_schedule)

    sub.add_parser("status", help="spend and pipeline counts").set_defaults(func=cmd_status)
    p = sub.add_parser("dashboard", help="open the review dashboard")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--headless", action="store_true", help="don't open a browser tab")
    p.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
