"""Stage 6.5 — generate the frame that was written, instead of hunting for one.

Stock search answers "what exists?". The ad director already wrote what the
frame should be, in the beat's `show` and `shot` fields, so the better question
is "render this". Gemini's image models take that directly and return a native
9:16 photograph.

Two things matter more than prompt quality here:

**Consistency.** Eight beats generated independently look like eight different
photographers. Every prompt therefore carries the same STYLE CONTRACT — one
palette, one lens, one lighting setup, one location — derived once per video.

**No text.** Captions are burned in later by Remotion. Any lettering the model
invents collides with them, so every prompt ends with an explicit negative.

Falls back to `vira.stock` per beat if generation fails, so a quota error costs
one photograph rather than the whole video.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from vira.config import settings
from vira.llm import complete_json
from vira.models import Company, Remix

log = logging.getLogger(__name__)

ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Appended to every prompt. Not optional — this is what stops the ad looking
# like a mood board.
NEGATIVE = (
    "No text, no lettering, no captions, no logos, no watermarks, no signage. "
    "No collage, no split screen, no borders."
)

DIRECTOR_SYSTEM = """You are a photography director for short-form vertical ads.

You produce two things: one STYLE CONTRACT that every frame in the ad obeys, and
one prompt per beat.

The style contract fixes the look so eight separately generated frames read as
one shoot: location, time of day, light source and quality, colour palette, lens
and depth of field, and who (if anyone) is in frame. Be concrete — "7am kitchen,
low east window light, warm neutrals, 35mm, shallow depth of field, one pair of
hands, no face" beats "warm and inviting".

Each beat prompt describes ONE photograph. Physical and specific: subject,
action, framing, what is in the background. Do not restate the style contract in
each prompt — it is prepended automatically. Do not describe camera moves;
a photograph does not pan. Never ask for text in the image.

Photographs, not illustrations. Candid and slightly imperfect, like a real
person shot it on a phone. JSON only."""

DIRECTOR_PROMPT = """# Brand
{brand} — {category}
{bio}

# Product in this ad
{product}

# The beats, with the director's own shot notes
{beats}

# Required look for THIS ad (the style contract must obey it)
{look}

# Task
Return JSON:
{{
  "style_contract": "one sentence, under 320 chars, obeyed by every frame",
  "prompts": ["photograph for beat 1", "photograph for beat 2", ...]
}}

Exactly {n} prompts, in order. Each under 320 characters."""


class ImageGenError(RuntimeError):
    pass


async def derive_prompts(
    company: Company, product: str, remix: Remix, look: str = ""
) -> tuple[str, list[str]]:
    beats = "\n".join(
        f"{i + 1}. say: {b.say}\n   show: {b.show}\n   shot: {b.shot}"
        for i, b in enumerate(remix.beats)
    )
    data = await complete_json(
        DIRECTOR_PROMPT.format(
            brand=company.name, category=company.category, bio=company.bio,
            product=product, beats=beats, n=len(remix.beats),
            look=look or "(no constraint — choose a coherent look)",
        ),
        system=DIRECTOR_SYSTEM,
        max_tokens=3000,
    )
    style = str(data.get("style_contract", "")).strip()
    prompts = [str(p) for p in data.get("prompts", [])]
    while len(prompts) < len(remix.beats):
        prompts.append(f"{product} on a kitchen counter, candid phone photograph")
    return style, prompts[: len(remix.beats)]


async def _one(
    client: httpx.AsyncClient, model: str, key: str, prompt: str, dest: Path, name: str
) -> dict | None:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "9:16"},
        },
    }
    try:
        r = await client.post(
            ENDPOINT.format(model=model), params={"key": key}, json=body
        )
        if r.status_code >= 400:
            log.warning("gemini %s: %s", r.status_code, r.text[:220])
            return None
        payload = r.json()
    except httpx.HTTPError as exc:
        log.warning("gemini request failed: %s", exc)
        return None

    for part in payload.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        inline = part.get("inlineData")
        if not inline:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(base64.b64decode(inline["data"]))
        return {"file": name, "credit": "generated · Gemini", "prompt": prompt}

    log.warning("gemini returned no image part (safety filter?)")
    return None


async def generate_shots(
    company: Company, product: str, remix: Remix, dest: Path, look: str = ""
) -> list[dict]:
    """One generated photograph per beat. Falls back to stock per failed beat."""
    s = settings()
    if not s.gemini_api_key:
        raise ImageGenError("GEMINI_API_KEY is not set")

    style, prompts = await derive_prompts(company, product, remix, look)
    log.info("style contract: %s", style)

    full = [f"{style} {p} {NEGATIVE}" for p in prompts]

    async with httpx.AsyncClient(timeout=180) as client:
        results = await asyncio.gather(
            *(
                _one(client, s.image_model, s.gemini_api_key, prompt, dest, f"shot{i:02d}.jpg")
                for i, prompt in enumerate(full)
            ),
            return_exceptions=True,
        )

    shots: list[dict] = []
    missing: list[int] = []
    for i, res in enumerate(results):
        if isinstance(res, dict):
            shots.append({**res, "query": prompts[i], "style_contract": style})
        else:
            shots.append({"file": None, "query": prompts[i], "credit": None})
            missing.append(i)

    if missing:
        log.info("falling back to stock for %d beat(s): %s", len(missing), missing)
        from vira.stock import fetch_shots

        try:
            stock = await fetch_shots(company, product, remix, dest)
            for i in missing:
                if i < len(stock) and stock[i].get("file"):
                    shots[i] = stock[i]
        except Exception as exc:  # noqa: BLE001
            log.warning("stock fallback failed: %s", exc)

    return shots
