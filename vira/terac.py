"""Terac — the human-labour MCP. It recruits the panel that judges our ads.

Using this is a submission requirement for the hackathon, but it is also the
one capability the engine genuinely cannot fake: the engine grades its own
output on five dimensions and has no way to know which of five equally-grounded
cuts a person would actually watch.

**The integration is smaller than it looks, because the hard half already
exists.** `vira/api/routes/reviews.py` is an unauthenticated, token-keyed judge
surface — a stranger opens a link, watches the films, rates them, and never
sees an engine score. Terac's `activity` task type takes a `task_url`, which is
exactly a link handed to a stranger. So the integration is not "build a panel
UI"; it is "hand Terac the judge_url we already mint".

Three things about this transport are worth stating, because each one costs an
afternoon to rediscover:

**The endpoint is a path, not a subdomain.** `https://terac.com/api/mcp`.
`mcp.terac.com`, `api.terac.com` and `docs.terac.com` are all NXDOMAIN, which
is why every guess at the URL failed until someone tried a path.

**Responses are SSE-framed even though there is no stream.** A single JSON-RPC
reply comes back as `text/event-stream` with one `data:` frame. You must send
`Accept: application/json, text/event-stream` or the server rejects the request
outright, and you must unwrap the frame before you have JSON.

**It is stateless.** No `Mcp-Session-Id`, no `initialize`, no
`notifications/initialized`. Every call is an independent POST, which is why
this module holds no client and no session — it is a function per call.

Nothing here logs the API key, and nothing here spends money except
`launch_draft`, which is deliberately awkward to reach: it refuses unless the
caller passes `i_understand_this_spends_real_money=True`. Drafts cost nothing;
launching bills CPI x participants + platform fee against a real balance.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Sequence
from urllib.parse import urlsplit

import httpx

from vira.config import settings

log = logging.getLogger(__name__)

MCP_URL = "https://terac.com/api/mcp"

# The org dashboard is where a human reviews this work. Never assemble one of
# these by pattern — Terac's own guidance is that the routes are not uniform.
# Every response carries absolute `dashboard_url` / `links.dashboard` strings;
# use those. This constant exists only for the "we are configured" status line.
ORG_SLUG = "vira-msuo4fry"

TaskReview = Literal["auto_approve", "manual_review", "self_report"]


class TeracError(RuntimeError):
    """The MCP answered, and the answer was an error."""


class TeracNotConfigured(RuntimeError):
    """No API key. Raised instead of sending an unauthenticated request that
    would come back as a 401 the caller has to decode."""


def configured() -> bool:
    return bool(settings().terac_api_key)


def _headers() -> dict[str, str]:
    key = settings().terac_api_key
    if not key:
        raise TeracNotConfigured(
            "TERAC_API_KEY is unset. It lives in Azure Key Vault "
            "kv-zerohuman-hack as terac-api-key."
        )
    return {
        "Content-Type": "application/json",
        # Both halves are mandatory. Send only application/json and the server
        # refuses the request before it looks at the body.
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {key}",
    }


def _frames(body: str) -> list[dict[str, Any]]:
    """Every JSON-RPC message in an SSE body.

    Written as a parser rather than a `startswith("data: ")` one-liner because
    the spec allows `data:` with no space, allows a message to span several
    `data:` lines, and interleaves `event:`/`id:` lines that must be skipped.
    A single reply happens to arrive as one tidy frame today; that is not a
    guarantee worth betting the integration on.
    """
    messages: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        raw = "\n".join(buffer)
        buffer.clear()
        if raw.strip():
            messages.append(json.loads(raw))

    for line in body.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("data:"):
            buffer.append(line[5:].lstrip(" "))
    flush()
    return messages


async def rpc(method: str, params: dict[str, Any] | None = None, *, timeout: float = 90.0) -> Any:
    """One stateless JSON-RPC round trip. Returns the `result` member."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(MCP_URL, headers=_headers(), json=payload)
        if response.status_code >= 400:
            # The body carries the real reason (bad key, bad tool, validation
            # detail); the status alone says almost nothing. It never echoes
            # the key back.
            raise TeracError(f"terac {method} HTTP {response.status_code}: {response.text[:600]}")
        messages = _frames(response.text)

    for message in messages:
        if "error" in message:
            raise TeracError(f"terac {method}: {message['error']}")
        if "result" in message:
            return message["result"]
    raise TeracError(f"terac {method}: no data frame in response ({response.text[:200]!r})")


async def list_tools() -> list[dict[str, Any]]:
    """Every tool the server exposes. Cheap, unauthenticated-adjacent proof of life."""
    return list((await rpc("tools/list")).get("tools") or [])


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Call one tool and return its text content.

    MCP tool results are a list of content blocks, and Terac only ever uses
    text blocks — sometimes markdown (`terac_get_context`), sometimes a JSON
    document (everything else). Joining them is the whole unwrap.
    """
    result = await rpc("tools/call", {"name": name, "arguments": arguments or {}})
    text = "\n".join(
        block.get("text", "")
        for block in (result.get("content") or [])
        if block.get("type", "text") == "text"
    )
    if result.get("isError"):
        raise TeracError(f"terac {name}: {text or result}")
    return text


async def call_json(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """`call_tool`, parsed. Falls back to `{"text": ...}` for the markdown tools."""
    text = await call_tool(name, arguments)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


# -- thin wrappers over the tools this project actually uses ----------------
#
# Deliberately not one wrapper per tool. Twenty-three passthroughs would be
# twenty-three things to keep in step with a vendor's schema for no gain; the
# CLI can reach anything else through `call_json`.


async def get_context() -> dict[str, Any]:
    """Org identity, live balance, dashboard URLs. Terac says to call it first.

    Returns markdown, so the useful numbers are pulled out by `org_summary`.
    """
    return await call_json("terac_get_context")


def _first_match(text: str, *needles: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("-* ").strip()
        for needle in needles:
            if stripped.lower().startswith(needle.lower()):
                return stripped[len(needle):].strip(" :*")
    return ""


async def org_summary() -> dict[str, Any]:
    """The three facts a status line needs, scraped out of the context markdown.

    Scraping is not lovely, but `terac_get_context` is a prose tool by design —
    it returns an operating playbook for an agent to read, and the balance is a
    line in it. The raw text is kept on the result so nothing is lost.
    """
    context = await get_context()
    text = context.get("text", "") if isinstance(context, dict) else str(context)
    return {
        "organization": _first_match(text, "**Organization:**", "Organization:") or "Vira",
        "balance": _first_match(text, "**Balance:**", "Balance:"),
        "dashboard": f"https://terac.com/{ORG_SLUG}",
        "raw": text,
    }


async def list_opportunities(status: str | None = None) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"limit": 100}
    if status:
        args["status"] = status
    payload = await call_json("terac_list_opportunities", args)
    return list(payload.get("data") or []) if isinstance(payload, dict) else []


async def get_opportunity(opportunity_id: str) -> dict[str, Any]:
    return await call_json("terac_get_opportunity", {"opportunityId": opportunity_id})


async def get_submissions(opportunity_id: str, status: str | None = None) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"opportunityId": opportunity_id, "limit": 100}
    if status:
        args["status"] = status
    payload = await call_json("terac_get_submissions", args)
    return list(payload.get("data") or []) if isinstance(payload, dict) else []


async def get_submission(submission_id: str) -> dict[str, Any]:
    return await call_json("terac_get_submission", {"submissionId": submission_id})


def judge_opportunity_payload(
    *,
    batch_id: str,
    judge_url: str,
    title: str,
    num_participants: int,
    duration_minutes: int = 5,
    review_type: TaskReview = "manual_review",
    business_type: Literal["b2c", "b2b"] = "b2c",
    project_id: str | None = None,
) -> dict[str, Any]:
    """The exact `terac_create_opportunity` arguments for a judging panel.

    Split out from the call so it can be printed, reviewed and diffed before a
    single request is made. On a real-money balance, "show me what you would
    send" has to be a first-class operation, not a docstring.

    Four choices in here are load-bearing:

    - **`activity`, not `interview`.** Both take a `task_url`; `interview`
      frames it as a session the participant books into. Rating five films in a
      browser tab is an activity.
    - **No `screening_questions`.** A screener triggers a mandatory AI voice
      interview with every applicant who passes the form. It costs nothing and
      takes hours, and rating an ad needs no filtering.
    - **`unrestricted_audience: true`, no `filters`.** "General Population" is
      the fastest fill, and Terac rejects a create that carries neither.
    - **`manual_review` by default.** `auto_approve` pays on a redirect to
      Terac's completion callback, so it only pays correctly once the judge
      page performs that redirect. Until it does, manual review is the mode
      where a bad submission can still be refused.

    `internal_title` carries the batch id so a human reading the Terac
    dashboard can trace an opportunity back to a batch; the authoritative link
    is the public token inside `task_url`, which `batch_token_of` reads back.
    """
    return {
        "title": title,
        "internal_title": f"vira · review batch {batch_id}",
        "description": (
            "Watch a short set of AI-generated video ads and rate each one. "
            "No account needed — open the link, watch, rate 1-5, pick your "
            "favourite, and add a sentence on why. Takes about "
            f"{duration_minutes} minutes."
        ),
        "project_id": project_id or settings().terac_project_id,
        "num_participants": num_participants,
        "business_type": business_type,
        "unrestricted_audience": True,
        "tasks": [
            {
                "sequence": 1,
                "task_type": "activity",
                "review_type": review_type,
                "task_url": judge_url,
                "title": "Rate the ads",
                "description": (
                    "Open the link, watch each video, and rate it 1-5. Pick the "
                    "single one you would actually stop scrolling for, and say "
                    "in one sentence why."
                ),
                "duration_minutes": duration_minutes,
            }
        ],
    }


async def create_judge_opportunity(**kwargs: Any) -> dict[str, Any]:
    """Create the panel as a DRAFT. Free, reversible, recruits nobody.

    A draft is also the only way to learn the real price: Terac computes CPI
    while creating it, and the response carries `pricing.total_cost_cents`.
    Estimating that number instead of asking for it invents a budget.
    """
    payload = judge_opportunity_payload(**kwargs)
    return await call_json("terac_create_opportunity", payload)


async def delete_opportunity(opportunity_id: str) -> dict[str, Any]:
    """Undo of `create_judge_opportunity`. DRAFT only, and it costs nothing."""
    return await call_json("terac_delete_opportunity", {"opportunityId": opportunity_id})


async def launch_draft(
    opportunity_id: str, *, i_understand_this_spends_real_money: bool = False
) -> dict[str, Any]:
    """SPENDS MONEY. Recruits and pays a real panel against a real balance.

    The keyword argument is not ceremony. Every other function in this module
    is safe to call while exploring, and a launch is a debit that cannot be
    undone — `terac_stop_opportunity` refunds only the unused remainder. The
    flag makes the spend a thing someone typed, and it means no route, CLI
    default or retry loop can reach it by accident.
    """
    if not i_understand_this_spends_real_money:
        raise TeracError(
            f"refusing to launch {opportunity_id}: launching debits the live Terac "
            "balance and cannot be undone. Pass "
            "i_understand_this_spends_real_money=True if that is intended."
        )
    log.warning("launching terac opportunity %s — this spends real money", opportunity_id)
    return await call_json("terac_launch_draft_opportunity", {"opportunityId": opportunity_id})


# -- reading our own batches back out of Terac ------------------------------


def batch_token_of(opportunity: dict[str, Any]) -> str | None:
    """The review batch's public token, recovered from an opportunity's task_url.

    This is how a batch and an opportunity stay linked without a schema
    change: the link is the URL we handed over, and Terac stores it for us.
    `/v1/review-batches/<token>` and `<frontend>/<token>` both end in the
    token, so the last path segment is it.
    """
    for task in opportunity.get("tasks") or []:
        url = task.get("task_url")
        if not url:
            continue
        segments = [s for s in urlsplit(url).path.split("/") if s]
        if segments:
            return segments[-1]
    return None


def submission_ref(submission: dict[str, Any]) -> str:
    """The `reviewer_ref` a Terac participant's vote is stored under.

    Namespaced, because `review_votes.reviewer_ref` is a free-text opaque id
    shared with any other panel source. `terac:<submission id>` keeps a Terac
    judge distinguishable from a colleague who opened the same link, and it
    matches what Terac appends to the task URL as `teracSubmissionId` — so a
    vote cast in the browser and a submission read back over the API land on
    the same row instead of double-counting.
    """
    return f"terac:{submission.get('id') or submission.get('submission_id') or ''}"


def submission_text(submission: dict[str, Any]) -> str:
    """Whatever the participant actually wrote, flattened to one string.

    Terac's submission shape varies by task type and is not pinned by the tool
    schema, so this walks the plausible places rather than indexing one path
    and raising KeyError on a live panel at 20:00.
    """
    parts: list[str] = []

    def harvest(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, dict):
            for key in ("response", "answer", "text", "value", "notes", "result"):
                if key in value:
                    harvest(value[key])
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                harvest(item)

    for key in ("tasks", "task_results", "responses", "answers", "screening_answers"):
        harvest(submission.get(key))
    return " · ".join(dict.fromkeys(parts))[:2000]
