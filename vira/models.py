"""DTOs. Mirrors the Lovable Cloud schema where it matters, nothing more."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Trend(BaseModel):
    trend_key: str
    platform: str = "tiktok"
    title: str = ""
    caption: str = ""
    source_url: str
    author: str = ""
    format: str = ""
    hashtags: list[str] = Field(default_factory=list)
    views: int = 0
    likes: int = 0
    engagement_rate: float = 0.0
    trend_score: float = 0.0
    posted_at: datetime | None = None
    relevance_rank: int = 1

    # Set by the verify stage.
    verified: bool = False
    drop_reason: str | None = None

    @property
    def age_days(self) -> float:
        if not self.posted_at:
            return 9_999.0
        now = datetime.now(timezone.utc)
        return (now - self.posted_at).total_seconds() / 86_400

    def brief(self) -> str:
        """One compact line for an LLM prompt. Keeps token cost sane at N=20."""
        return (
            f"[{self.trend_key}] @{self.author} · {self.format} · "
            f"{self.views:,} views · eng {self.engagement_rate:.1%} · "
            f"score {self.trend_score:.0f} · {self.age_days:.0f}d old\n"
            f'  "{self.caption[:220]}"\n'
            f"  tags: {', '.join(self.hashtags[:8])}\n"
            f"  {self.source_url}"
        )


class Company(BaseModel):
    id: str
    name: str
    slug: str
    bio: str = ""
    mission: str = ""
    website: str | None = None
    category: str = ""
    positioning: str | None = None
    tone: str | None = None
    keywords: list[str] = Field(default_factory=list)
    ad_themes: list[str] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict) -> "Company":
        cat = row.get("categories") or {}
        insights = row.get("company_insights") or []
        latest = insights[0] if insights else {}
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            bio=row.get("bio") or "",
            mission=row.get("mission") or "",
            website=row.get("website"),
            category=cat.get("name") or "",
            positioning=latest.get("positioning"),
            tone=latest.get("tone"),
            keywords=latest.get("keywords") or [],
            ad_themes=latest.get("ad_themes") or [],
        )

    def context(self, product: str) -> str:
        lines = [
            f"Company: {self.name}",
            f"Category: {self.category}",
            f"Bio: {self.bio}",
            f"Mission: {self.mission}",
            f"Product being pushed: {product}",
        ]
        if self.website:
            lines.append(f"Website: {self.website}")
        if self.positioning:
            lines.append(f"Positioning: {self.positioning}")
        if self.tone:
            lines.append(f"Tone: {self.tone}")
        if self.keywords:
            lines.append(f"Keywords: {', '.join(self.keywords)}")
        return "\n".join(lines)


class Word(BaseModel):
    """One spoken word with real timing, derived from character timestamps."""

    w: str
    start: float
    end: float


class Beat(BaseModel):
    """One shot. `t` is a draft until the voice stage replaces it with real timing."""

    t: float = 0.0
    say: str
    show: str
    shot: str = ""
    # Authored by the director, not inferred downstream.
    motion: str = ""      # caption treatment: stack|punch|slide|pop|banner
    delivery: str = ""    # ElevenLabs v3 performance tag, e.g. "[excited]"
    camera: str = ""      # push|pull|pan|punch|hold
    # Populated by the voice stage from character timestamps.
    start_s: float | None = None
    end_s: float | None = None
    # Word-level timing drives karaoke captions.
    words: list[Word] = Field(default_factory=list)


class Remix(BaseModel):
    hook: str
    beats: list[Beat]
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""
    why_this_works: str = ""
    grounded_in: list[str] = Field(default_factory=list)

    def narration(self) -> str:
        """The full spoken track — what gets sent to TTS as one utterance."""
        return " ".join(b.say.strip() for b in self.beats if b.say.strip())


class Score(BaseModel):
    relevance: float = 0.0
    specificity: float = 0.0
    actionability: float = 0.0
    differentiation: float = 0.0
    evidence: float = 0.0

    @property
    def overall(self) -> float:
        vals = [
            self.relevance,
            self.specificity,
            self.actionability,
            self.differentiation,
            self.evidence,
        ]
        return round(sum(vals) / len(vals), 2)


class CorpusAnalysis(BaseModel):
    dominant_formats: list[str] = Field(default_factory=list)
    recurring_hooks: list[str] = Field(default_factory=list)
    what_top_performers_share: str = ""
    whitespace: str = ""
    citations: list[str] = Field(default_factory=list)


class CompetitorFinding(BaseModel):
    competitor: str
    present_in_corpus: bool
    what_they_run: str = ""
    citations: list[str] = Field(default_factory=list)
