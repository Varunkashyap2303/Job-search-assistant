"""Sends the morning message to your own Telegram chat through a bot you create with @BotFather: only
what's new, else a check-in on drafts and follow-ups, else nothing (see digest.py).
It only ever messages TELEGRAM_CHAT_ID (you); outreach is never sent from here.

Setup: put TELEGRAM_BOT_TOKEN in .env, send your bot any message, run `python -m jobsearch telegram setup`
to see your chat id, put it in TELEGRAM_CHAT_ID, then `python -m jobsearch telegram test`."""

import logging
import os
from pathlib import Path

import httpx2 as httpx

from jobsearch.scout.sources.base import http

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 4096   # Telegram's limit per message


class NotifyError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def _call(method: str, data: dict | None = None, files: dict | None = None) -> dict | list:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise NotifyError("TELEGRAM_BOT_TOKEN is not set in .env")
    try:
        r = http().post(API.format(token=token, method=method), data=data, files=files)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        # httpx errors include the URL, which contains the bot token, so only the type is kept
        raise NotifyError(f"Telegram {method} failed: {type(e).__name__}") from None
    if not body.get("ok"):
        raise NotifyError(f"Telegram {method} failed: {body.get('description', r.status_code)}")
    return body["result"]


def send_text(text: str) -> None:
    _call("sendMessage", {"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": text[:MAX_TEXT],
                          "disable_web_page_preview": "true"})


def send_file(path: str | Path, caption: str = "") -> None:
    path = Path(path)
    with open(path, "rb") as f:
        _call("sendDocument", {"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "caption": caption[:1024]},
              files={"document": (path.name, f, "text/markdown")})


def recent_chats() -> list[tuple[int, str]]:
    """Chats that have messaged the bot recently, for finding your TELEGRAM_CHAT_ID."""
    chats = {}
    for update in _call("getUpdates"):
        chat = (update.get("message") or update.get("edited_message") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title") or ""
    return list(chats.items())


def morning_update(report_path: str) -> str:
    """The scheduled morning message: only what's new, else a check-in, else nothing (see digest.py).
    What was shown is recorded only after Telegram accepts it. Never raises."""
    if not configured():
        return "not configured"
    from jobsearch import digest

    message = digest.morning_message()
    if message is None:
        return "nothing new or pending: no message"
    kind, text, startup_ids, draft_ids = message
    try:
        send_text(text)
        if kind == "brief":
            send_file(report_path, "Full brief: every startup profile and drafted message")
    except (NotifyError, OSError) as e:
        log.warning("Telegram delivery failed: %s", e)
        return f"failed: {e}"
    digest.record(kind, text, startup_ids, draft_ids)
    return f"sent {kind}"


def deliver_brief(report_path: str, stats: dict | None = None) -> str:
    """Summary message plus the full brief as a file. Returns a status for the run log; never raises,
    so a Telegram outage can't fail the morning run."""
    if not configured():
        return "not configured"
    from jobsearch.intel.report import summary_text

    from jobsearch import digest

    try:
        send_text(summary_text(stats))
        send_file(report_path, "Full brief: every startup profile and drafted message")
    except (NotifyError, OSError) as e:
        log.warning("Telegram delivery failed: %s", e)
        return f"failed: {e}"
    digest.mark_all_seen()      # you've now seen everything, so tomorrow only covers what's new
    return "sent"
