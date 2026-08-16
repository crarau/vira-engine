"""A thin public proxy in front of Gemini image generation.

The point is the key, not the feature. Anyone can generate an image here without
holding a Gemini credential, and the credential never leaves the box. The
request shape deliberately mirrors the upstream one — prompt in, image out — so
this is a pass-through rather than an opinion.

Deliberately unauthenticated, like the rest of this service during the
hackathon (see CLAUDE.md, "Public by design"). That is a decision, not an
oversight, and it comes with one consequence worth naming: **every call spends
real money.** So there are two guards that are not auth — a per-process rate
limit and a hard daily ceiling — because "open to the team" and "open to a
script in a loop" should not cost the same.

Images land under `out/generated/` and are served by the existing `/media`
mount, so a caller gets a URL it can put straight in an `<img>`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from vira.config import settings

router = APIRouter(prefix="/v1/image", tags=["image"])

ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Gemini's image models. `flash` is the default because it returns in ~8s and
# the quality difference only shows on complex compositions.
MODELS = {
    "flash": "gemini-3.1-flash-image",
    "flash-lite": "gemini-3.1-flash-lite-image",
    "pro": "gemini-3-pro-image",
}

ASPECTS = {"9:16", "16:9", "1:1", "4:3", "3:4", "2:3", "3:2"}

# Costs money, no auth. A burst limit stops an accidental loop; the daily cap
# stops a deliberate one. Both are per-process and reset on restart, which is
# the right trade for something that will be taken down in days.
_BURST_PER_MINUTE = 20
_DAILY_MAX = 500
_recent: list[float] = []
_today: dict[str, int] = {}
_lock = asyncio.Lock()


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    aspect_ratio: str = Field(default="9:16")
    model: str = Field(default="flash")
    # Text in a generated image is almost always unwanted — it collides with
    # anything overlaid later and models render it badly. Opt in explicitly.
    allow_text: bool = False


class ImageResponse(BaseModel):
    url: str
    prompt: str
    model: str
    aspect_ratio: str
    bytes: int
    elapsed_ms: int


NO_TEXT = (
    " No text, no lettering, no captions, no logos, no watermarks, no signage."
)


async def _admit(day: str) -> None:
    """Rate limit. Raises 429 rather than quietly queueing — a caller that is
    looping should be told, not slowed."""
    async with _lock:
        now = time.monotonic()
        _recent[:] = [t for t in _recent if now - t < 60]
        if len(_recent) >= _BURST_PER_MINUTE:
            raise HTTPException(429, f"more than {_BURST_PER_MINUTE} images in a minute")
        if _today.get(day, 0) >= _DAILY_MAX:
            raise HTTPException(429, f"daily ceiling of {_DAILY_MAX} images reached")
        _recent.append(now)
        _today[day] = _today.get(day, 0) + 1


async def _generate(prompt: str, model_id: str, aspect: str) -> bytes:
    s = settings()
    if not s.gemini_api_key:
        raise HTTPException(503, "image generation is not configured on this server")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(
                ENDPOINT.format(model=model_id), params={"key": s.gemini_api_key}, json=body
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"upstream unreachable: {exc}") from exc

    if r.status_code >= 400:
        # Pass the upstream status through — a 429 from Gemini is not a 500
        # from us, and a caller deserves to know which it was. The key is never
        # in the body, so the message is safe to forward.
        raise HTTPException(r.status_code, f"upstream: {r.text[:300]}")

    for part in r.json().get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if inline := part.get("inlineData"):
            return base64.b64decode(inline["data"])

    # No image part and no error status means a safety filter ate it.
    raise HTTPException(422, "the model returned no image — likely a safety filter")


@router.post("", response_model=ImageResponse)
async def create_image(
    req: ImageRequest,
    request: Request,
    raw: bool = Query(False, description="return the JPEG bytes instead of JSON"),
):
    """Generate one image. Returns a URL by default, bytes with ?raw=true."""
    if req.aspect_ratio not in ASPECTS:
        raise HTTPException(422, f"aspect_ratio must be one of {sorted(ASPECTS)}")
    model_id = MODELS.get(req.model, req.model)

    await _admit(time.strftime("%Y-%m-%d"))

    prompt = req.prompt if req.allow_text else req.prompt + NO_TEXT
    started = time.monotonic()
    data = await _generate(prompt, model_id, req.aspect_ratio)
    elapsed = int((time.monotonic() - started) * 1000)

    if raw:
        return Response(content=data, media_type="image/jpeg")

    # Same root the /media mount serves from, so the URL below resolves.
    dest = Path(os.environ.get("VIRA_OUT_DIR", "out")) / "generated"
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    (dest / name).write_bytes(data)

    # Built from the request host so the URL works behind the tunnel; the
    # internal bind address would be useless to the caller.
    base = str(request.base_url).rstrip("/")
    return ImageResponse(
        url=f"{base}/media/generated/{name}",
        prompt=req.prompt,
        model=model_id,
        aspect_ratio=req.aspect_ratio,
        bytes=len(data),
        elapsed_ms=elapsed,
    )


@router.get("/models")
async def models() -> dict:
    """What this proxy will accept, so a caller does not have to guess."""
    return {
        "models": MODELS,
        "default": "flash",
        "aspect_ratios": sorted(ASPECTS),
        "limits": {
            "burst_per_minute": _BURST_PER_MINUTE,
            "daily_max": _DAILY_MAX,
            "note": "unauthenticated on purpose; these exist because each call costs money",
        },
    }
