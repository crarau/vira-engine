"""`POST /v1/ads/image` — the generated ad. Built to `docs/IMAGE-API.md` §2.

Two things about this endpoint are deliberate and both are worth defending.

**It runs the whole pipeline, not a prompt.** `POST /v1/image` already exists and
is a pass-through to Gemini: prompt in, picture out. This is the other thing. It
selects and verifies sources, works out what the category rewards, writes a
concept under the same hook grammar the films use, draws the frame, prints the
line with Remotion, and puts the result through the same evidence gate. A static
ad from here can be dropped, and being able to drop one is the whole difference
between an ad and a picture.

**It answers synchronously, which the video endpoint refuses to do.** That is a
real exception to "generation never runs inside a request", taken with eyes
open: the work is ~35 seconds rather than 74–350, the caller wants the artefact
rather than a job, and scoring runs alongside drawing so the tail is shorter
than the sum of its stages. A job queue would need a table for a result that is
two URLs. If this ever grows to a batch, it should become a job like everything
else.

Three status codes carry meaning and all three are in the contract:

  **422** a lane that does not exist, or a supplied `headline` that breaks the
          measured hook rules. The response names the rule — a caller who wrote
          the line deserves to be told which one, not just "invalid".
  **409** generated, then dropped on evidence. The whole ad is in the body,
          `drop_reason` included. Not a 200 with a flag, because a caller that
          forgets to read a flag ships an ungrounded ad; not a 500, because
          nothing failed.
  **503** an upstream this needs has no credential on this box.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from vira.adimage import (
    ASPECTS,
    BURNABLE_ASPECT,
    AdImage,
    AdImageFailed,
    AdImageUnconfigured,
    pick_lane,
    produce_ad_image,
)
from vira.api import worker
from vira.api.imagelimit import admit
from vira.api.schemas import ScoreOut
from vira.brief import Brief, slugify
from vira.lanes import BY_NAME, get as get_lane
from vira.llm import LLMError
from vira.remix import hook_faults

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ads", tags=["ads"])

# Every field the brief carries, in both dialects, so a brief posted at the top
# level can be told apart from this endpoint's own arguments.
_BRIEF_KEYS = set(Brief.model_fields) | {to_camel(f) for f in Brief.model_fields}


class AdImageRequest(BaseModel):
    """Either the documented simple shape or a full brief.

    Simple is `{brand, product, lane}` with `brand` a name. A brief sends
    `brand` as an object, which is how the two are told apart — and a brief
    posted at the top level, exactly as `POST /v1/briefs` takes it, is lifted
    into `brief` so the same JSON that makes a video makes a poster.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    brand: str | None = Field(default=None, min_length=2, max_length=80)
    product: str | None = Field(default=None, min_length=2, max_length=200)
    lane: str | None = Field(default=None, description="omit and the engine picks")
    category: str | None = Field(default=None, description="corpus slice, when the brand is new")
    headline: str | None = Field(
        default=None,
        description="skip the writer's line. Still checked against the hook rules.",
    )
    aspect_ratio: str = BURNABLE_ASPECT
    burn_text: bool = True
    # Accepted alongside `brand` for callers that already hold the engine's slug.
    company_slug: str | None = None
    brief: Brief | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_bare_brief(cls, data: Any) -> Any:
        """A brief arrives with `brand` as an object; the simple shape as a string."""
        if not isinstance(data, dict) or data.get("brief"):
            return data
        if not isinstance(data.get("brand"), dict):
            return data
        lifted = {k: v for k, v in data.items() if k in _BRIEF_KEYS}
        rest = {k: v for k, v in data.items() if k not in _BRIEF_KEYS}
        return {**rest, "brief": lifted}


class AdSource(BaseModel):
    url: str = ""
    views: int = 0
    trend_key: str = ""
    author: str = ""


class AdImageOut(BaseModel):
    id: str
    url: str
    image_url: str
    recipe_url: str
    headline: str
    cta: str = ""
    lane: str
    hook_shape: str | None = None
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    grounded_in: list[AdSource] = Field(default_factory=list)
    # The engine's own verdict, the same five dimensions the video carries.
    score: ScoreOut
    disposition: str
    drop_reason: str | None = None
    # Separate from disposition: an ad built on thin references can still clear
    # the gate, and a caller deserves to know the engine is less sure than the
    # number reads.
    confidence: str = "medium"
    burned: bool = True
    image_prompt: str = ""
    style_contract: str = ""
    elapsed_ms: int

    @classmethod
    def of(cls, ad: AdImage, base_url: str) -> "AdImageOut":
        def url(path: str) -> str:
            return worker.media_url(base_url, path)

        return cls(
            id=ad.ad_id,
            url=url(ad.ad_path),
            image_url=url(ad.frame_path),
            recipe_url=url(ad.recipe_path),
            headline=ad.headline,
            cta=ad.cta,
            lane=ad.lane,
            hook_shape=ad.hook_shape,
            caption=ad.caption,
            hashtags=ad.hashtags,
            grounded_in=[
                AdSource(url=t.source_url, views=t.views, trend_key=t.trend_key,
                         author=t.author)
                for t in (ad.grounded_in or ad.sources)
            ],
            score=ScoreOut(**ad.score.model_dump(), overall=ad.score.overall),
            disposition=ad.disposition,
            drop_reason=ad.drop_reason,
            confidence=ad.confidence,
            burned=ad.burned,
            image_prompt=ad.image_prompt,
            style_contract=ad.style_contract,
            elapsed_ms=ad.elapsed_ms,
        )


def _validate_headline(headline: str) -> None:
    """A supplied line is held to the same measured rules as a written one.

    The rules come from 2,669 ranked TikToks, not from taste, so waiving them
    for a caller who typed the line themselves would only mean the engine
    knowingly renders a hook it can prove under-performs. The fault list is
    returned verbatim: "reads as a verbless fragment, not a clause" is
    actionable and "invalid headline" is not.
    """
    if faults := hook_faults(headline):
        raise HTTPException(
            422,
            {
                "detail": "the supplied headline breaks the measured hook rules",
                "headline": headline,
                "broken_rules": faults,
                "reference": "docs/HOOK-CRAFT.md",
            },
        )


@router.post("/image", response_model=AdImageOut, responses={409: {"model": AdImageOut}})
async def create_ad_image(body: AdImageRequest, request: Request):
    """Generate one static ad. Holds the connection for ~35 seconds."""
    brief = body.brief
    brand = body.brand or (brief.brand.name if brief else None)
    if not brand:
        raise HTTPException(422, "send a brand name, or a brief carrying one")

    product = body.product or (brief.brand.name if brief else None)
    if not product:
        raise HTTPException(422, "send a product")

    aspect = (brief.aspect_ratio if brief and body.aspect_ratio == BURNABLE_ASPECT
              else body.aspect_ratio)
    if aspect not in ASPECTS:
        raise HTTPException(422, f"aspect_ratio must be one of {sorted(ASPECTS)}")
    if body.burn_text and aspect != BURNABLE_ASPECT:
        # The caption band is derived from a 1920px height in Captions.tsx, so
        # burning into another ratio would print the line into the wrong third.
        raise HTTPException(
            422,
            f"burn_text only works at {BURNABLE_ASPECT} — the caption band is "
            f"built for it. Send burn_text: false for a clean {aspect} frame.",
        )

    if body.lane is not None and body.lane not in BY_NAME:
        raise HTTPException(422, f"unknown lane {body.lane!r}. known: {', '.join(BY_NAME)}")
    headline = body.headline or (brief.narrative.hook if brief else "") or None
    if headline:
        _validate_headline(headline)

    slug = body.company_slug or (brief.slug if brief else slugify(brand))
    lane = get_lane(body.lane) if body.lane else pick_lane(slug, product)

    # Shared with POST /v1/image, not a second bucket: both endpoints spend on
    # the same Gemini account and a per-endpoint limit would not be a limit.
    await admit(time.strftime("%Y-%m-%d"))

    def note(message: str, stage: str = "ads", **data: Any) -> None:
        log.info("[%s %s] %s", slug, stage, message)

    out_dir = worker.new_out_dir(slug, lane.name, "image")
    try:
        ad = await produce_ad_image(
            brand=brand,
            product=product,
            out_dir=out_dir,
            out_root=worker.OUT_DIR,
            lane=lane,
            category=body.category,
            headline=headline,
            aspect_ratio=aspect,
            burn_text=body.burn_text,
            company_slug=slug,
            brief=brief,
            render_slot=worker.render_slot(),
            note=note,
        )
    except AdImageUnconfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, f"the text model is not usable here: {exc}") from exc
    except AdImageFailed as exc:
        # The engine declining to build on nothing is a 422 about the input, not
        # a 500 about this service.
        raise HTTPException(422, str(exc)) from exc

    payload = AdImageOut.of(ad, str(request.base_url))
    if ad.disposition == "dropped":
        # The ad exists and both URLs resolve — the status is the engine saying
        # the corpus did not support it, which is a fact about the request.
        return JSONResponse(status_code=409, content=payload.model_dump(mode="json"))
    return payload
