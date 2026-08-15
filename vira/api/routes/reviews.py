"""Review batches — the human signal the engine cannot generate for itself.

The engine already grades its own output on five dimensions. What it cannot do
is tell you which of five equally-grounded cuts a person would actually watch.
That is what a panel is for, and it is only worth collecting if the panel is
independent of the engine.

So the judge surface is built as its own thing, not as the operator surface with
fields hidden:

  - **Judges never see engine scores.** Not the overall, not evidence, not the
    disposition. A judge shown "4.2" ranks the engine's opinion back at us and
    the vote stops being an independent measurement. Enforced by returning
    `JudgeVideo`, which has no score field to populate.
  - **Judges never see the lane name either.** "founder-story" is a label with
    its own pull; the hook and the film are the thing being judged.
  - **The judge route is unauthenticated and keyed by an unguessable token.**
    Zero friction: a reviewer clicks a link and votes. No account, no login.
  - **Results are keyed by batch id, not by the public token.** Holding the
    judge link must not also hand you the running tally.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from vira.api import store
from vira.api.schemas import (
    BatchResults,
    JudgeBatch,
    JudgeVideo,
    ReviewBatchOut,
    ReviewBatchRequest,
    VideoResult,
    VoteAccepted,
    VoteRequest,
)
from vira.api.worker import media_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["reviews"])

# Where the judge actually lands. Set this to the frontend's review route; it
# falls back to this API's own JSON endpoint so the link is never dead.
JUDGE_BASE_URL = os.environ.get("VIRA_JUDGE_BASE_URL", "").rstrip("/")


def _judge_url(request: Request, token: str) -> str:
    if JUDGE_BASE_URL:
        return f"{JUDGE_BASE_URL}/{token}"
    return f"{str(request.base_url).rstrip('/')}/v1/review-batches/{token}"


@router.post("/review-batches", response_model=ReviewBatchOut, status_code=201)
async def create_batch(body: ReviewBatchRequest, request: Request) -> ReviewBatchOut:
    missing = [vid for vid in body.video_ids if not await store.get_video(vid)]
    if missing:
        raise HTTPException(422, f"unknown video ids: {', '.join(missing)}")

    batch = await store.create_review_batch(video_ids=body.video_ids, title=body.title)
    token = str(batch["public_token"])
    return ReviewBatchOut(
        batch_id=str(batch["id"]),
        public_token=token,
        judge_url=_judge_url(request, token),
    )


@router.get("/review-batches/{public_token}", response_model=JudgeBatch)
async def judge_payload(public_token: str, request: Request) -> JudgeBatch:
    """PUBLIC. What a reviewer sees: a title and the films, in a fixed order.

    Order is fixed per batch rather than shuffled per visitor so every judge
    ranks the same sequence and the results stay comparable across reviewers.

    Every field is chosen by hand. Do not build this response by spreading a
    video row — the engine's score travels in that row, and a judge who sees it
    is no longer an independent signal.
    """
    batch = await store.get_batch_with_videos(public_token=public_token)
    if not batch:
        raise HTTPException(404, "no such review batch")

    base = str(request.base_url)
    videos = [
        JudgeVideo(
            video_id=str(row["id"]),
            position=i + 1,
            hook=row.get("hook") or "",
            duration_s=float(row.get("duration_s") or 0.0),
            mp4_url=media_url(base, row.get("mp4_path") or ""),
        )
        for i, row in enumerate(batch.get("videos") or [])
    ]
    return JudgeBatch(title=batch.get("title") or "", videos=videos)


@router.post("/review-batches/{public_token}/votes", response_model=VoteAccepted, status_code=201)
async def cast_vote(public_token: str, body: VoteRequest) -> VoteAccepted:
    """PUBLIC. One reviewer's verdict on one video.

    The response says only that it was recorded — echoing a tally back would
    leak the aggregate to anyone holding the judge link, and would let an early
    voter's number anchor a later one.
    """
    batch = await store.get_batch_with_videos(public_token=public_token)
    if not batch:
        raise HTTPException(404, "no such review batch")

    in_batch = {str(v["id"]) for v in (batch.get("videos") or [])}
    if body.video_id not in in_batch:
        raise HTTPException(422, f"video {body.video_id} is not in this batch")

    await store.record_vote(
        batch_id=str(batch["id"]),
        reviewer_ref=body.reviewer_ref,
        video_id=body.video_id,
        rating=body.rating,
        picked=body.picked,
        comment=body.comment,
    )
    return VoteAccepted(reviewer_ref=body.reviewer_ref, video_id=body.video_id)


@router.get("/review-batches/{batch_id}/results", response_model=BatchResults)
async def results(batch_id: str) -> BatchResults:
    """Operator view: human rating next to the engine's own score.

    That comparison is the whole reason the panel exists — it is what turns a
    ranking into a correction to the scoring weights rather than an anecdote.
    """
    batch = await store.get_batch_with_videos(batch_id=batch_id)
    if not batch:
        raise HTTPException(404, f"no review batch {batch_id}")
    rows = await store.batch_results(batch_id)
    videos = [VideoResult.of(row) for row in rows]
    return BatchResults(
        batch_id=str(batch["id"]),
        title=batch.get("title") or "",
        total_votes=sum(v.votes for v in videos),
        videos=videos,
    )
