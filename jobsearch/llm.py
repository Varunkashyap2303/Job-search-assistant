"""Budget-guarded Claude client. Every LLM call in the project goes through here so spend is
capped before the call and recorded after it."""

import contextlib
import contextvars
import os
import re
from datetime import datetime, timezone
from typing import TypeVar

import anthropic
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import func
from sqlmodel import select

from jobsearch.config import local_tz, preferences
from jobsearch.db import LlmUsage, session

# USD per million tokens: (input, output). Cache writes (5 min TTL) bill at 1.25x input, reads at 0.1x.
PRICES_PER_MTOK = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),   # possible server-side fallback target for Opus 5
    "claude-sonnet-5": (2.00, 10.00),
}
# Opus 5 calls opt into server-side refusal fallbacks: if a request is declined, the API retries it
# on a substitute model within the same call. Billed at the model that actually served it.
FALLBACK_MODELS = {"claude-opus-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

T = TypeVar("T", bound=BaseModel)


# Per-agent shares stop scheduled agents starving each other. Actions you start yourself
# (dashboard buttons, CLI runs on a specific item) may use whatever is left of the daily cap.
_user_initiated = contextvars.ContextVar("user_initiated", default=False)


@contextlib.contextmanager
def user_initiated():
    token = _user_initiated.set(True)
    try:
        yield
    finally:
        _user_initiated.reset(token)


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Org-level API keys (not scoped to a workspace) must name the workspace on every request
        workspace = os.getenv("ANTHROPIC_WORKSPACE_ID")
        headers = {"anthropic-workspace-id": workspace} if workspace else None
        _client = anthropic.Anthropic(max_retries=3, default_headers=headers)
    return _client


def _price(model: str) -> tuple[float, float]:
    if model not in PRICES_PER_MTOK:
        raise LLMError(f"No price configured for {model}; add it to PRICES_PER_MTOK")
    return PRICES_PER_MTOK[model]


def usage_cost(model: str, usage) -> float:
    inp, out = _price(model)
    cache_write = usage.cache_creation_input_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    return (
        usage.input_tokens * inp
        + cache_write * inp * CACHE_WRITE_MULT
        + cache_read * inp * CACHE_READ_MULT
        + usage.output_tokens * out
    ) / 1_000_000


def spend_since(since: datetime, agent_group: str | None = None) -> float:
    """Total spend since `since`, optionally for one agent group ("tailor" covers "tailor.verifier")."""
    q = select(func.sum(LlmUsage.cost_usd)).where(LlmUsage.ts >= since)
    if agent_group:
        q = q.where((LlmUsage.agent == agent_group) | LlmUsage.agent.startswith(agent_group + "."))
    with session() as s:
        total = s.exec(q).one()
    return float(total or 0.0)


def _today_start() -> datetime:
    return datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def spend_today(agent_group: str | None = None) -> float:
    return spend_since(_today_start(), agent_group)


def spend_this_month() -> float:
    start = datetime.now(local_tz()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return spend_since(start.astimezone(timezone.utc))


def check_budget(model: str, est_input_tokens: int, max_tokens: int, agent: str = "") -> None:
    """Refuse a call whose worst-case cost would push spend over the agent's daily share,
    the overall daily cap, or the monthly cap."""
    budget = preferences().get("budget", {})
    inp, out = _price(model)
    reserve = (est_input_tokens * inp + max_tokens * out) / 1_000_000
    group = agent.split(".")[0]
    share = budget.get("per_agent_daily", {}).get(group)
    if share is not None and not _user_initiated.get():
        spent = spend_today(group)
        if spent + reserve > share:
            raise BudgetExceeded(f"'{group}' daily share ${share} reached (spent ${spent:.2f} today)")
    today, month = spend_today(), spend_this_month()
    if today + reserve > budget.get("daily_usd", 0):
        raise BudgetExceeded(f"Daily cap ${budget.get('daily_usd')} reached (spent ${today:.2f} today)")
    if month + reserve > budget.get("monthly_usd", 0):
        raise BudgetExceeded(f"Monthly cap ${budget.get('monthly_usd')} reached (spent ${month:.2f} this month)")


def _record(agent: str, model: str, response) -> float:
    u = response.usage
    served = response.model if response.model in PRICES_PER_MTOK else model
    cost = usage_cost(served, u)
    with session() as s:
        s.add(LlmUsage(
            agent=agent,
            model=served,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cost_usd=cost,
            request_id=response._request_id or "",
        ))
        s.commit()
    return cost


def parse(
    *,
    agent: str,
    model: str,
    system: str,
    user: str,
    output_format: type[T],
    max_tokens: int = 2000,
    effort: str = "low",
) -> T:
    """One structured-output call. The system prompt is cached, so keep it identical across calls
    and put everything that varies into `user`."""
    check_budget(model, est_input_tokens=(len(system) + len(user)) // 3, max_tokens=max_tokens, agent=agent)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": _schema_format(output_format)},
    )
    # create() + our own validation (rather than parse()) so usage is recorded even when the
    # output is truncated or invalid.
    try:
        if model in FALLBACK_MODELS:
            response = client().beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            response = client().messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        if "workspace" in str(e).lower():
            raise  # account/setup problem: stop the run rather than fail every item
        raise LLMError(f"Bad request: {e}") from e
    _record(agent, model, response)
    if response.stop_reason == "refusal":
        raise LLMError(f"Refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"Hit max_tokens={max_tokens} before finishing structured output")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        parsed = output_format.model_validate_json(text)
        # The model occasionally double-escapes inside JSON strings ("\\n", "\\u2014"), which would
        # otherwise show up as literal backslash sequences in resumes and messages.
        return output_format.model_validate(_unescape(parsed.model_dump()))
    except ValidationError as e:
        raise LLMError(f"Output didn't match {output_format.__name__}: {e}") from e


_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape(obj):
    if isinstance(obj, str):
        if "\\" not in obj:
            return obj
        obj = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), obj)
        return obj.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    if isinstance(obj, dict):
        return {k: _unescape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unescape(v) for v in obj]
    return obj


_formats: dict[type, dict] = {}


def _schema_format(model: type[BaseModel]) -> dict:
    if model not in _formats:
        _formats[model] = {"type": "json_schema", "schema": anthropic.transform_schema(TypeAdapter(model).json_schema())}
    return _formats[model]
