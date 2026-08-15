"""The wire contract. What a frontend is allowed to send, and to see.

Two things make this file more than DTO boilerplate.

**The judge payload is a separate type on purpose.** `JudgeVideo` has no score
field, and cannot grow one by accident the way a shared model with an optional
`score=None` would. A judge who can see that the engine already graded a cut
4.2 is no longer an independent signal — they are agreeing with the engine.
Human ranking is the one input the engine does not already have; contaminating
it costs more than any convenience a shared model would buy.

**Everything the store returns is read defensively.** The persistence layer is
owned by another module; these builders pull with `.get` and default rather than
failing a whole response over one absent column.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["fast", "agentic"]
JobState = Literal["queued", "running", "done", "failed"]


# --- companies -----------------------------------------------------------


class CompanyIn(BaseModel):
    """Input quality is the biggest single lever on the eventual score.

    "Selling chips" scores 2.6; a real bio naming a mechanism scores 3.8. So bio
    and mission are required and are expected to say what the product does.
    """

    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(description="category slug, e.g. 'pets'")
    bio: str = Field(min_length=20)
    mission: str = Field(min_length=20)
    website: str | None = None
    owner_name: str = "vira"


class CompanyOut(BaseModel):
    id: str | None = None
    slug: str
    name: str
    category: str = ""
    bio: str = ""
    mission: str = ""
    website: str | None = None
    video_count: int | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> "CompanyOut":
        cat = row.get("categories") or {}
        category = row.get("category") or (cat.get("name") if isinstance(cat, dict) else "")
        return cls(
            id=str(row["id"]) if row.get("id") is not None else None,
            slug=row.get("slug", ""),
            name=row.get("name", ""),
            category=category or "",
            bio=row.get("bio") or "",
            mission=row.get("mission") or "",
            website=row.get("website"),
            video_count=row.get("video_count"),
        )


# --- lanes ---------------------------------------------------------------


class LaneOut(BaseModel):
    """A creative lane, as a UI needs to present it.

    `voice_id` is deliberately absent: which ElevenLabs voice sits behind
    "warm storyteller" is an engine decision, and exposing the id invites a
    frontend to start picking voices out of band from the lane's look and brief.
    """

    name: str
    brief: str
    voice_note: str
    look: str


# --- generation ----------------------------------------------------------


class VideoRequest(BaseModel):
    company_slug: str
    product: str = Field(min_length=2, max_length=200)
    lane: str = "founder-story"
    mode: Mode = "fast"


class RegenerateRequest(BaseModel):
    notes: list[str] = Field(default_factory=list, max_length=20)
    lane: str | None = Field(default=None, description="switch lanes; defaults to the original")


class JobAccepted(BaseModel):
    job_id: str
    status: JobState = "queued"
    poll: str
    estimated_seconds: int


class JobOut(BaseModel):
    job_id: str
    status: JobState
    progress_note: str = ""
    video_id: str | None = None
    error: str | None = None
    company_slug: str | None = None
    lane: str | None = None
    mode: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> "JobOut":
        # The job row has no video_id column — the store joins the videos the
        # job produced. A generate job makes exactly one, so the first is it.
        videos = row.get("videos") or []
        return cls(
            job_id=str(row["id"]),
            status=row.get("status", "queued"),
            progress_note=row.get("progress_note") or "",
            video_id=str(videos[0]["id"]) if videos else None,
            error=row.get("error"),
            company_slug=row.get("company_slug"),
            lane=row.get("lane"),
            mode=row.get("mode"),
            created_at=_iso(row.get("created_at")),
            updated_at=_iso(row.get("finished_at") or row.get("started_at")),
        )


# --- videos --------------------------------------------------------------


class ScoreOut(BaseModel):
    relevance: float = 0.0
    specificity: float = 0.0
    actionability: float = 0.0
    differentiation: float = 0.0
    evidence: float = 0.0
    overall: float = 0.0


class VideoOut(BaseModel):
    """The operator-facing view. Carries the engine's own verdict."""

    id: str
    job_id: str | None = None
    company_slug: str = ""
    product: str = ""
    lane: str = ""
    mode: str = ""
    hook: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""
    duration_s: float = 0.0
    mp4_url: str
    score: ScoreOut | None = None
    disposition: str | None = None
    drop_reason: str | None = None
    created_at: str | None = None

    @classmethod
    def of(cls, row: dict[str, Any], mp4_url: str, *, company_slug: str = "") -> "VideoOut":
        # `score` is the stored average, `score_breakdown` the five dimensions.
        breakdown = row.get("score_breakdown") or {}
        score = (
            ScoreOut(**{**breakdown, "overall": row.get("score") or 0.0})
            if breakdown
            else None
        )
        return cls(
            id=str(row["id"]),
            job_id=str(row["job_id"]) if row.get("job_id") else None,
            company_slug=row.get("company_slug") or company_slug,
            product=row.get("product") or "",
            lane=row.get("lane") or "",
            mode=row.get("mode") or "",
            hook=row.get("hook") or "",
            caption=row.get("caption") or "",
            hashtags=row.get("hashtags") or [],
            cta=row.get("cta") or "",
            duration_s=float(row.get("duration_s") or 0.0),
            mp4_url=mp4_url,
            score=score,
            disposition=row.get("disposition"),
            drop_reason=row.get("drop_reason"),
            created_at=_iso(row.get("created_at")),
        )


class RecipeOut(BaseModel):
    """The whole provenance record, verbatim.

    Passed through untyped on purpose. This is the "change a prompt and re-run"
    surface, and pinning a schema over it would quietly drop any stage added
    later — which is exactly the field an operator would then be missing.
    """

    video_id: str
    recipe: dict[str, Any]


# --- review batches ------------------------------------------------------


class ReviewBatchRequest(BaseModel):
    video_ids: list[str] = Field(min_length=2, max_length=20)
    title: str = Field(min_length=1, max_length=200)


class ReviewBatchOut(BaseModel):
    batch_id: str
    public_token: str
    judge_url: str


class JudgeVideo(BaseModel):
    """One cut as a judge sees it.

    No score, no disposition, no drop reason, no lane name. A judge told this is
    the "contrarian" cut ranks the label; a judge told the engine gave it 4.2
    ranks the engine. They get the hook and the film.
    """

    video_id: str
    position: int
    hook: str = ""
    duration_s: float = 0.0
    mp4_url: str


class JudgeBatch(BaseModel):
    title: str
    videos: list[JudgeVideo]


class VoteRequest(BaseModel):
    reviewer_ref: str = Field(min_length=1, max_length=120)
    video_id: str
    rating: int = Field(ge=1, le=5)
    picked: bool = False
    comment: str = Field(default="", max_length=2000)


class VoteAccepted(BaseModel):
    recorded: bool = True
    reviewer_ref: str
    video_id: str


class VideoResult(BaseModel):
    video_id: str
    position: int = 0
    hook: str = ""
    lane: str = ""
    votes: int = 0
    avg_rating: float | None = None
    picks: int = 0
    comments: list[str] = Field(default_factory=list)
    engine_score: float | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> "VideoResult":
        return cls(
            video_id=str(row.get("video_id") or row.get("id", "")),
            position=row.get("position") or 0,
            hook=row.get("hook") or "",
            lane=row.get("lane") or "",
            votes=row.get("votes") or 0,
            avg_rating=row.get("avg_rating"),
            picks=row.get("picks") or 0,
            comments=[c for c in (row.get("comments") or []) if c],
            engine_score=row.get("engine_score"),
        )


class BatchResults(BaseModel):
    """The operator view of a finished panel.

    Engine score sits next to human rating here — that comparison is the whole
    point of collecting the votes. It appears only after voting, never during.
    """

    batch_id: str
    title: str = ""
    total_votes: int = 0
    videos: list[VideoResult] = Field(default_factory=list)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
