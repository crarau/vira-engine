"""Thin LLM wrapper. One place to swap providers or add caching."""

from __future__ import annotations

import json
import logging
import re
import time

from vira.config import settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


async def complete(
    prompt: str, *, system: str, max_tokens: int = 4000
) -> tuple[str, str | None]:
    """Return (text, stop_reason). The stop reason is how we detect truncation."""
    from anthropic import AsyncAnthropic

    s = settings()
    if not s.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    client = AsyncAnthropic(api_key=s.anthropic_api_key)
    started = time.monotonic()
    msg = await client.messages.create(
        model=s.llm_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = "".join(b.text for b in msg.content if b.type == "text")

    # Two destinations, and they are not the same thing. The Recorder is the
    # durable record — it lands in the recipe next to the mp4 and outlives the
    # process. The event bus is the live one, for someone watching the run
    # happen. Both are no-ops when nothing is listening, which is what keeps
    # `variants.py` and `agentic_video.py` unchanged.
    from vira.provenance import current

    stage = _publish(
        system=system or "", prompt=prompt, model=s.llm_model,
        max_tokens=max_tokens, response=text, stop_reason=msg.stop_reason,
        elapsed_ms=elapsed_ms,
    )
    if rec := current():
        rec.capture(
            system=system or "", prompt=prompt, model=s.llm_model,
            max_tokens=max_tokens, response=text, stop_reason=msg.stop_reason,
            stage=stage,
        )

    return text, msg.stop_reason


def _publish(
    *, system: str, prompt: str, model: str, max_tokens: int,
    response: str, stop_reason: str | None, elapsed_ms: int,
) -> str:
    """Announce the call on the live job feed, and report which stage it was in.

    Imported lazily and defensively: this module is the CLI's LLM wrapper and it
    must not acquire an import-time dependency on the REST service, nor fail a
    generation because a progress feed did.
    """
    try:
        from vira.api import events

        events.publish_llm_call(
            model=model, max_tokens=max_tokens, system_prompt=system,
            user_prompt=prompt, response=response, stop_reason=stop_reason,
            elapsed_ms=elapsed_ms,
        )
        return events.current_stage()
    except Exception:  # noqa: BLE001 - a trace line cannot cost a paid generation
        log.debug("could not publish the prompt for this call", exc_info=True)
        return ""


def _extract(raw: str) -> str:
    text = raw.strip()
    if m := _FENCE.search(text):
        return m.group(1).strip()
    if (i := text.find("{")) > 0:
        return text[i:]
    return text


TERSE = (
    "\n\nIMPORTANT: keep every string under 220 characters. Be terse. "
    "Truncated output is unusable, so favour brevity over completeness."
)


async def complete_json(
    prompt: str, *, system: str, max_tokens: int = 4000
) -> dict:
    """Ask for JSON, and survive the two ways models fail to deliver it.

    Truncation is the common one: a model writes a 200-word string inside a list
    and runs out of budget mid-token, leaving unparseable JSON. Retrying with a
    bigger budget and an explicit brevity instruction fixes it; asking a model to
    "repair" its own truncated output does not, because the tail is simply gone.
    """
    attempts: list[tuple[int, str]] = [
        (max_tokens, prompt),
        (max_tokens * 2, prompt + TERSE),
    ]

    last_error = ""
    for budget, body in attempts:
        raw, stop = await complete(body, system=system, max_tokens=budget)
        if stop == "max_tokens":
            last_error = f"truncated at {budget} tokens"
            log.warning("%s — retrying with a larger budget and a brevity hint", last_error)
            continue
        try:
            parsed = json.loads(_extract(raw))
        except json.JSONDecodeError as exc:
            last_error = f"unparseable: {exc}"
            log.warning("JSON parse failed (%s) — retrying", exc)
            continue
        if not isinstance(parsed, dict):
            last_error = "expected a JSON object"
            continue
        return parsed

    raise LLMError(f"could not get JSON after {len(attempts)} attempts: {last_error}")
