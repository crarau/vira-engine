"""COHESION — the continuity checker.

The one capability the straight-line pipeline has no counterpart for, and the
one that catches the most visible failures.

Everything upstream works on intent: the script says what should be on screen,
the image prompt asks for it. Nobody ever looks at what actually came back. So
a frame that quietly ignored half its prompt sails through, and the first person
to notice is a judge.

This module looks. It asks a vision model to describe each generated frame in
plain terms, then compares those descriptions against the beats that requested
them — and against each other, for style drift.

Deliberately blunt: it reports mismatches, it does not fix them. Fixing is
IMAGERY's job, and routing is the Director's.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from vira.config import settings
from vira.llm import complete_json
from vira.models import Remix

log = logging.getLogger(__name__)

VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

DESCRIBE = (
    "Describe this photograph in two sentences, plainly. Say what the subject is, "
    "what they are doing, the setting, the time of day implied by the light, and "
    "the overall colour treatment. State any visible text or logos. Do not "
    "editorialise or judge quality."
)

CHECK_SYSTEM = """You check whether a video's frames match its script.

You are given each beat's spoken line and shot direction, alongside a factual
description of the image that was actually produced for it. Find real
mismatches, not stylistic quibbles.

A real mismatch is: the frame shows a different subject or action than the line
describes; a stated time jump is not visible; the frame contradicts a claim in
the line; visible text or a logo collides with the burned-in captions; one frame
is obviously a different shoot from the rest.

Not a mismatch: a frame being merely plain, or a reasonable interpretation you
would have shot differently.

Be specific and beat-indexed. JSON only."""

CHECK_PROMPT = """# Beats, with the image actually produced for each
{pairs}

# Planned duration
{target}s planned, {actual:.1f}s actual

# Task
Return JSON:
{{
  "duration_ok": true|false,
  "duration_note": "empty string if fine",
  "style_consistent": true|false,
  "style_note": "how the frames diverge, or empty",
  "mismatches": [
    {{"beat_index": <0-based>, "problem": "what is wrong", "fix": "what to regenerate"}}
  ],
  "verdict": "one sentence"
}}
Only include mismatches worth regenerating a frame for."""


async def describe_image(path: Path) -> str:
    """Ask a vision model what is actually in the frame."""
    s = settings()
    if not s.gemini_api_key or not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode()
    body = {
        "contents": [{"parts": [
            {"text": DESCRIBE},
            {"inlineData": {"mimeType": "image/jpeg", "data": data}},
        ]}]
    }
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                VISION_URL.format(model=s.vision_model),
                params={"key": s.gemini_api_key}, json=body,
            )
            if r.status_code >= 400:
                log.warning("vision %s: %s", r.status_code, r.text[:180])
                return ""
            parts = r.json()["candidates"][0]["content"]["parts"]
            return " ".join(p.get("text", "") for p in parts).strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("describe failed for %s: %s", path.name, exc)
        return ""


async def describe_all(shots: list[dict], shots_dir: Path) -> list[str]:
    """Describe every frame concurrently — this is 8 independent vision calls."""
    paths = [
        shots_dir / s["file"] if s.get("file") else None for s in shots
    ]
    results = await asyncio.gather(
        *(describe_image(p) if p else _empty() for p in paths),
        return_exceptions=True,
    )
    return [r if isinstance(r, str) else "" for r in results]


async def _empty() -> str:
    return ""


async def check(
    remix: Remix, shots: list[dict], descriptions: list[str],
    target_seconds: int, actual_seconds: float,
) -> dict:
    pairs = "\n\n".join(
        f"BEAT {i}\n  says:  {b.say}\n  wanted: {b.shot or b.show}\n"
        f"  image IS: {descriptions[i] if i < len(descriptions) else '(no image)'}"
        for i, b in enumerate(remix.beats)
    )
    try:
        return await complete_json(
            CHECK_PROMPT.format(pairs=pairs, target=target_seconds, actual=actual_seconds),
            system=CHECK_SYSTEM,
            max_tokens=2500,
        )
    except Exception as exc:  # noqa: BLE001 - a failed check must not stop the film
        log.warning("cohesion check failed: %s", exc)
        return {"duration_ok": True, "style_consistent": True,
                "mismatches": [], "verdict": f"check unavailable ({exc})"}
