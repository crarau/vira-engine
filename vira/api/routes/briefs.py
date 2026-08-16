"""`POST /v1/briefs` — Lovable's payload, on the pipeline that already exists.

This endpoint accepts a strictly richer input than `POST /v1/videos` and answers
identically: 202, a job id, a poll URL and an ETA. That symmetry is the point.
Lovable's poll and stream code was written against `/v1/videos` and does not
change, because the difference between the two endpoints is entirely in what the
engine is told, not in how the caller waits.

Where each field goes is documented in `vira/brief.py` and in `docs/BRIEFS.md`.
Three things are decided here rather than there, because they are HTTP concerns:

**A brand that Lovable has not registered still works.** The job row needs a
company, so one is created from the brief on the spot. Requiring a separate
`POST /v1/companies` first would be a step between the caller and the value for
no gain — the brief already carries everything that row holds.

**An unsupported aspect ratio is a 422, not a crop.** The composition is
1080×1920 and the caption band is derived from that height, so anything else
would print the text into the wrong third of the frame. Refusing is honest;
silently rendering 9:16 and calling it 1:1 is not.

**Warnings come back in the 202.** A brief can be perfectly valid and still ask
for something the engine cannot do — a music mood with no soundtrack stage, a
language the hook grammar was not measured in. Those are worth saying at accept
time, while someone is still looking at the response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from vira.api import store, worker
from vira.api.schemas import JobAccepted, Mode
from vira.api.routes.videos import ETA_SECONDS
from vira.brief import SUPPORTED_ASPECT, Brief
from vira.lanes import BY_NAME

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["briefs"])


class BriefRequest(Brief):
    """Their payload verbatim, plus the three things the engine needs and they do not send.

    Subclassed rather than wrapped so a brief posted exactly as their type
    system emits it validates unchanged, and `product`/`lane`/`mode` are simply
    extra keys alongside it.
    """

    product: str | None = None
    lane: str = "founder-story"
    mode: Mode = "fast"


class BriefAccepted(JobAccepted):
    """`JobAccepted` plus what the engine decided about the brief.

    Additive on purpose: a client reading `job_id` and `poll` sees exactly the
    `/v1/videos` response it already handles.
    """

    company_slug: str
    product: str
    duration_seconds: int
    beats: int
    grounded_on: str
    references_used: int
    signal_quality: str
    warnings: list[str] = []


def _warnings(body: BriefRequest) -> list[str]:
    out: list[str] = []
    if not body.trend_refs:
        out.append(
            "no trendKey references — falling back to category selection, which "
            "is coarser than the references you can pick"
        )
    if body.style.music_mood:
        out.append(
            f"musicMood {body.style.music_mood!r} is recorded but ignored: the "
            "engine renders narration only, there is no music track"
        )
    if not body.constraints.language.lower().startswith("en"):
        out.append(
            f"language {body.constraints.language!r}: the hook grammar was "
            "measured on English and is applied as guidance, not as a rule"
        )
    if body.signal_quality == "low":
        out.append(
            "signalQuality low — the evidence dimension is scored down by "
            "1.0 before the gate, so this brief is likelier to be dropped"
        )
    if body.duration_seconds <= 4:
        out.append(
            "durationSeconds 4 leaves room for about ten spoken words across two "
            "beats; the engine also adds its fixed 2.4s call-to-action card"
        )
    if body.mode == "agentic" and body.narrative.beats:
        out.append(
            "mode agentic ignores the brief's fixed plan — the Director shapes "
            "its own film and reads your beats as direction. Use fast to pin them."
        )
    return out


@router.post("/briefs", response_model=BriefAccepted, status_code=202)
async def create_from_brief(body: BriefRequest, request: Request) -> BriefAccepted:
    if body.aspect_ratio != SUPPORTED_ASPECT:
        raise HTTPException(
            422,
            f"aspectRatio {body.aspect_ratio!r} is not renderable — the "
            f"composition and its caption band are built for {SUPPORTED_ASPECT}",
        )
    if body.lane not in BY_NAME:
        raise HTTPException(422, f"unknown lane {body.lane!r}. known: {', '.join(BY_NAME)}")

    # The brand is the product when nothing else is named. A brief is written
    # about one thing; making the caller repeat its name would be friction with
    # no information in it.
    product = (body.product or body.brand.name).strip()
    slug = body.slug

    company = await worker.resolve_company(slug)
    if not company:
        company = await store.upsert_company(
            slug=slug, name=body.brand.name, bio=body.brand.bio,
            mission=body.brand.mission, category=body.brand.category,
            website=None, owner_name="lovable",
        )

    # The engine fields are stripped back off before the brief travels, so the
    # worker only ever sees the payload Lovable actually authored.
    brief = Brief.model_validate(
        body.model_dump(mode="json", by_alias=True, exclude={"product", "lane", "mode"})
    )

    job = await store.create_job(
        company_id=company["id"], product=product, lane=body.lane, mode=body.mode,
    )
    job_id = str(job["id"])
    worker.spawn(
        job_id,
        company_slug=slug,
        product=product,
        lane_name=body.lane,
        mode=body.mode,
        brief=brief,
    )

    beats = len(body.narrative.beats) or body.shape[0]
    return BriefAccepted(
        job_id=job_id,
        poll=f"{str(request.base_url).rstrip('/')}/v1/jobs/{job_id}",
        estimated_seconds=ETA_SECONDS.get(body.mode, 120),
        company_slug=slug,
        product=product,
        duration_seconds=body.duration_seconds,
        beats=beats,
        grounded_on="brief references" if body.trend_refs else "category selection",
        references_used=len(body.kept),
        signal_quality=body.signal_quality,
        warnings=_warnings(body),
    )
