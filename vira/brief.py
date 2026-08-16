"""The Lovable brief, and where each field lands in the existing pipeline.

Lovable assembles a far richer statement of intent than `POST /v1/videos` ever
accepted: a brand with guardrails, up to six weighted references that have
already been OCR'd and colour-analysed, a narrative with authored beats, a style
contract, and hard constraints. None of that needs a second pipeline. Every
field maps onto a stage this engine already runs, and the mapping is the whole
of this module:

    brand                → models.Company            (the context every prompt gets)
    references[trendKey] → the verified corpus        (replaces category selection)
    references[imageKey] → the imagery style contract (lanes.Lane.look)
    narrative            → the plan and the writer's brief
    style                → look + pacing
    constraints/neverSay → hard prohibitions, in the brief the writer is handed
    durationSeconds      → beat count and narration length
    signalQuality        → a penalty at the evidence gate, never a bypass

**The trend references are strictly better retrieval than what we have.**
`vira.select.shortlist` reaches the corpus through one join — the company's
category — then ranks by `trend_score` and caps per format. It never sees the
product, and a category is a coarse instrument: "Food & Beverage" is one bucket
for an energy drink and a sourdough starter. Lovable picks its references
against the actual brand and the actual asset, so a brief carrying `trendKey`s
arrives with a shortlist this engine could not have produced. When they are
present, selection is bypassed rather than blended: mixing hand-picked
references with category leftovers would let the weaker half of the corpus back
into a prompt that had already been curated past it.

What does NOT change: the references are still fetched and verified before they
reach a prompt, and the evidence gate still runs in Python afterwards. A brief
is a better input, not a way around the two rules that make the output
trustworthy.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from vira.lanes import Lane
from vira.llm import complete_json
from vira.models import Company, Score, Trend
from vira.supa import Supa

log = logging.getLogger(__name__)

# Lovable speaks camelCase on the wire and this codebase speaks snake_case.
# `populate_by_name` accepts both, so a hand-written curl and their generated
# client are equally valid and neither has to learn the other's dialect.
WIRE = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

# The writer prompt's own figure for how fast a line is read. Used to turn a
# duration into a word budget, and afterwards to measure whether the script that
# came back fits inside it.
WORDS_PER_SECOND = 2.6

# durationSeconds → (beats, spoken words). A beat under ~4 words is a label
# rather than the finite clause the hook grammar demands, which is why 4 seconds
# is two beats and not four.
DURATION_SHAPE: dict[int, tuple[int, int]] = {4: (2, 10), 6: (3, 16), 8: (4, 21)}

# How far over the word budget a script may come back before it is reported.
# Some slack is right — the budget is derived from an average reading speed, not
# measured from this narration — but 40% over four seconds is nearly six, and a
# caller who asked for four deserves to be told.
OVERRUN_TOLERANCE = 1.4

# Remotion's composition is 1080×1920 and the caption band is derived from that
# height in `video/src/Captions.tsx`. Accepting another ratio would mean
# rendering text into the wrong third of the frame, so it is refused rather
# than silently letterboxed.
SUPPORTED_ASPECT = "9:16"

# `signalQuality: "low"` is Lovable saying its own references are thin. That is
# a statement about the evidence, so it is applied to the evidence dimension —
# subtracted, never added. A request can make this gate harder to pass and has
# no way to make it easier.
LOW_SIGNAL_EVIDENCE_PENALTY = 1.0


# --- the payload, in their names -----------------------------------------


class BrandBrief(BaseModel):
    model_config = WIRE

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    bio: str = ""
    mission: str = ""
    category: str = ""
    tone_guardrails: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    must_say: list[str] = Field(default_factory=list)
    never_say: list[str] = Field(default_factory=list)

    @field_validator("tone_guardrails", "palette", "must_say", "never_say", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        """A single guardrail arrives as a string often enough to be worth accepting."""
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


class Ocr(BaseModel):
    model_config = WIRE

    text: str = ""
    headline: str = ""
    cta: str = ""
    confidence: float = 0.0


class Sentiment(BaseModel):
    model_config = WIRE

    tone: str = ""
    score: float = 0.0
    emotion_tags: list[str] = Field(default_factory=list)
    intent: str = ""
    urgency: str = ""


class Texture(BaseModel):
    model_config = WIRE

    palette: list[str] = Field(default_factory=list)
    lighting: str = ""
    surface_texture: str = ""
    finish: str = ""
    contrast: str = ""
    saturation: str = ""
    noise_level: str = ""


class Composition(BaseModel):
    model_config = WIRE

    framing: str = ""
    subject: str = ""
    focal_depth: str = ""
    text_placement: str = ""
    negative_space: str = ""


class MotionHint(BaseModel):
    model_config = WIRE

    implied_motion: str = ""
    suggested_camera: str = ""
    suggested_beats: list[str] = Field(default_factory=list)

    @field_validator("suggested_beats", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


class ImageReference(BaseModel):
    """An asset Lovable has already looked at. Direction, not evidence.

    An image reference tells the engine what the ad should LOOK like. It is not
    a corpus row and never reaches the scorer's cited-sources list — nothing
    about a colour palette supports a claim.
    """

    model_config = WIRE

    image_key: str
    source_url: str | None = None
    image_url: str | None = None
    weight: float = 1.0
    lead: bool = False
    ocr: Ocr = Field(default_factory=Ocr)
    sentiment: Sentiment = Field(default_factory=Sentiment)
    texture: Texture = Field(default_factory=Texture)
    composition: Composition = Field(default_factory=Composition)
    motion: MotionHint = Field(default_factory=MotionHint)
    keep: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return self.image_key


class TrendReference(BaseModel):
    """A corpus row Lovable has already decided is relevant. Evidence.

    `hook`, `format` and `whyItWorks` are Lovable's own reading of the video.
    They are carried into the writer's brief as stated mechanisms, but the row
    itself is still fetched from the corpus and verified — the engine grounds on
    what it can check, not on what it was told.
    """

    model_config = WIRE

    trend_key: str
    platform: str = ""
    hook: str = ""
    format: str = ""
    why_it_works: str = ""
    weight: float = 1.0
    lead: bool = False

    @property
    def key(self) -> str:
        return self.trend_key


class BeatBrief(BaseModel):
    model_config = WIRE

    t: float = 0.0
    shot: str = ""
    on_screen_text: str = ""


class Narrative(BaseModel):
    model_config = WIRE

    hook: str = ""
    beats: list[BeatBrief] = Field(default_factory=list)
    voiceover: str = ""
    cta: str = ""
    text_overlay_policy: str = ""


class Style(BaseModel):
    model_config = WIRE

    look: str = ""
    palette: list[str] = Field(default_factory=list)
    pace: str = ""
    music_mood: str = ""
    captions: str = ""

    @field_validator("captions", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        """`captions` arrives as a bool from some clients and a name from others."""
        if v is None:
            return ""
        if isinstance(v, bool):
            return "burned-in" if v else "none"
        return str(v)

    @field_validator("palette", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


class Constraints(BaseModel):
    model_config = WIRE

    no_real_people_likeness: bool = False
    no_competitor_marks: bool = False
    language: str = "en"
    safety_notes: list[str] = Field(default_factory=list)

    @field_validator("safety_notes", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


class Brief(BaseModel):
    model_config = WIRE

    brand: BrandBrief
    duration_seconds: Literal[4, 6, 8] = 8
    aspect_ratio: str = SUPPORTED_ASPECT
    references: list[ImageReference | TrendReference] = Field(default_factory=list, max_length=6)
    narrative: Narrative = Field(default_factory=Narrative)
    style: Style = Field(default_factory=Style)
    constraints: Constraints = Field(default_factory=Constraints)
    excluded: list[str] = Field(default_factory=list)
    signal_quality: Literal["high", "low"] = "high"

    @field_validator("references", mode="before")
    @classmethod
    def _dispatch(cls, value: Any) -> Any:
        """Pick the reference type by which key is present, not by field overlap.

        A smart union would match an image reference against `TrendReference`
        whenever the image happened to carry a `format`, and the failure would
        be a silently missing style contract rather than a validation error.
        """
        if not isinstance(value, list):
            return value
        out: list[Any] = []
        for item in value:
            if not isinstance(item, dict):
                out.append(item)
                continue
            if item.get("trendKey") or item.get("trend_key"):
                out.append(TrendReference.model_validate(item))
            elif item.get("imageKey") or item.get("image_key"):
                out.append(ImageReference.model_validate(item))
            else:
                raise ValueError("every reference needs a trendKey or an imageKey")
        return out

    # -- the reference set, after exclusions and in dominance order --------

    @property
    def kept(self) -> list[ImageReference | TrendReference]:
        """References the brief did not reject, strongest first.

        "Lead asset dominates" is implemented as ordering rather than as a
        multiplier: the writer reads the corpus in the order it is given and
        `parse_remix` falls back to the first entry when the model cites
        nothing, so being first IS being dominant.
        """
        rejected = set(self.excluded)
        live = [r for r in self.references if r.key not in rejected]
        return sorted(live, key=lambda r: (not r.lead, -r.weight))

    @property
    def trend_refs(self) -> list[TrendReference]:
        return [r for r in self.kept if isinstance(r, TrendReference)]

    @property
    def image_refs(self) -> list[ImageReference]:
        return [r for r in self.kept if isinstance(r, ImageReference)]

    @property
    def lead(self) -> ImageReference | TrendReference | None:
        return self.kept[0] if self.kept else None

    @property
    def slug(self) -> str:
        return self.brand.slug or slugify(self.brand.name)

    @property
    def shape(self) -> tuple[int, int]:
        """(beat count, spoken word budget) for this duration."""
        return DURATION_SHAPE[self.duration_seconds]


# --- the plan the writer executes ----------------------------------------


class BriefPlan(BaseModel):
    """What `vira.remix.build_remix` and `vira.director.critique` read off a plan.

    Deliberately not `director.VideoPlan`: those two stages consume a plan
    through `getattr`, so a brief can supply one without the director running,
    and this module stays decoupled from a model that belongs to the stage it is
    replacing.
    """

    structure: str = ""
    device: str = ""
    beat_count: int = 4
    target_seconds: int = 8
    pacing: str = ""
    opening_move: str = ""
    turn_at: str = ""
    hook_shape: str = ""
    rationale: str = ""


# --- mapping --------------------------------------------------------------


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG_STRIP.sub("-", name.lower()).strip("-") or "brand"


def company_from_brief(brief: Brief, row: dict | None = None) -> Company:
    """The brand as `Company`, overlaid on the corpus row when one exists.

    The brief wins on every field it fills, because it is the newer and more
    considered statement of the brand — the row may be a signup form from weeks
    ago. What only the row can supply is the Lovable `id`, and that matters:
    without it `vira.select.shortlist` cannot reach the category join, which is
    the fallback when a brief carries no trend references.
    """
    b = brief.brand
    row = row or {}
    base = Company.from_row(row) if row.get("id") else None

    return Company(
        # The slug stands in for the id when the brand is not in Lovable Cloud.
        # `shortlist` will find no category for it and say so, which is the
        # correct outcome: an unknown brand with no references has nothing to
        # ground on and must fail loudly rather than invent a corpus.
        id=str(row.get("id") or brief.slug),
        name=b.name or (base.name if base else ""),
        slug=brief.slug,
        bio=b.bio or (base.bio if base else ""),
        mission=b.mission or (base.mission if base else ""),
        website=row.get("website") or (base.website if base else None),
        category=b.category or (base.category if base else ""),
        positioning=base.positioning if base else None,
        tone="; ".join(b.tone_guardrails) or (base.tone if base else None),
        # mustSay lands in keywords because `Company.context()` prints keywords
        # into every prompt in the pipeline — the planner, the writer, the
        # imagery director and the scorer all see them without a new argument.
        keywords=b.must_say or (base.keywords if base else []),
        ad_themes=base.ad_themes if base else [],
    )


def plan_from_brief(brief: Brief) -> BriefPlan:
    """Skip the director: the brief already decided the shape of the film.

    `durationSeconds` is the field with the biggest downstream effect. Four
    seconds is two beats and about ten spoken words, against the seven beats and
    twenty-eight seconds the director picks left to itself — so honouring it
    changes the script materially, not cosmetically.
    """
    beats, words = brief.shape
    authored = brief.narrative.beats
    if authored:
        # An authored beat list overrides the duration table. Lovable knows how
        # many shots it wants; the table is only a default for a brief that says
        # nothing.
        beats = len(authored)

    lead = brief.lead
    device = ""
    if isinstance(lead, TrendReference):
        device = lead.why_it_works
    elif isinstance(lead, ImageReference):
        device = lead.motion.implied_motion or lead.sentiment.intent

    return BriefPlan(
        structure=_structure(brief, beats),
        device=device or "the mechanism in the lead reference",
        beat_count=max(1, beats),
        target_seconds=brief.duration_seconds,
        pacing=brief.style.pace or ("urgent" if brief.duration_seconds <= 4 else "steady"),
        opening_move=brief.narrative.hook or "land the hook inside the first second",
        turn_at=_turn(authored),
        # Left unconstrained on purpose when the brief supplies its own hook:
        # forcing a measured hook shape onto a line the brand already wrote
        # would make the writer choose between two authorities.
        hook_shape="",
        rationale=(
            f"brief: {brief.duration_seconds}s, {beats} beats, "
            f"~{words} spoken words, signal {brief.signal_quality}"
        ),
    )


def _structure(brief: Brief, beats: int) -> str:
    authored = brief.narrative.beats
    if not authored:
        return f"{beats} beats in {brief.duration_seconds} seconds, one idea, no setup"
    shots = "; ".join(
        f"beat {i + 1} at {b.t:g}s — {b.shot or 'no shot note'}"
        + (f" — on screen: {b.on_screen_text}" if b.on_screen_text else "")
        for i, b in enumerate(authored)
    )
    return f"the brief's own beat list, executed in order: {shots}"


def _turn(authored: list[BeatBrief]) -> str:
    if len(authored) < 2:
        return "there is no room for a turn — the hook is the whole film"
    return f"beat {len(authored) // 2 + 1}, where the brief's shot list changes subject"


def look_from_brief(brief: Brief, fallback: str = "") -> str:
    """The imagery style contract, assembled from everything visual in the brief.

    This string is what `vira.imagegen.derive_prompts` is told the ad must obey,
    so it carries the palette, the lead asset's measured texture and framing, and
    the two prohibitions that are visual rather than verbal.
    """
    parts: list[str] = []
    if brief.style.look:
        parts.append(brief.style.look)

    palette = brief.style.palette or brief.brand.palette
    if palette:
        parts.append(f"Palette, strictly: {', '.join(palette[:8])}.")

    lead_image = next(iter(brief.image_refs), None)
    if lead_image:
        t, c = lead_image.texture, lead_image.composition
        measured = [
            v for v in (
                t.lighting, t.surface_texture, t.finish,
                f"{t.contrast} contrast" if t.contrast else "",
                f"{t.saturation} saturation" if t.saturation else "",
                f"{t.noise_level} grain" if t.noise_level else "",
                c.framing, c.subject, c.focal_depth,
                f"{c.negative_space} negative space" if c.negative_space else "",
            ) if v
        ]
        if measured:
            parts.append(
                f"Match the lead reference {lead_image.image_key}: {', '.join(measured)}."
            )
        if t.palette:
            parts.append(f"Sampled colours: {', '.join(t.palette[:6])}.")
        if lead_image.keep:
            parts.append(f"Keep: {', '.join(lead_image.keep)}.")

    avoid = sorted({a for r in brief.image_refs for a in r.avoid})
    if brief.constraints.no_real_people_likeness:
        avoid.append("any recognisable real person's face or likeness")
    if brief.constraints.no_competitor_marks:
        avoid.append("any competitor logo, packaging or trade dress")
    if avoid:
        parts.append(f"Never show: {'; '.join(avoid)}.")

    return " ".join(parts) or fallback


def direction_from_brief(brief: Brief, lane_brief: str = "") -> str:
    """The creative direction the writer is handed, prohibitions included.

    Everything a model must NOT do goes here rather than into a post-hoc filter.
    A banned phrase caught after generation costs a whole re-roll; a banned
    phrase stated in the brief usually never appears.
    """
    b = brief.brand
    n = brief.narrative
    beats, words = brief.shape
    if n.beats:
        beats = len(n.beats)

    L: list[str] = []
    if lane_brief:
        L.append(lane_brief)

    L.append(
        f"\nTHIS AD IS BUILT FROM A CLIENT BRIEF. The brief outranks the lane, and "
        f"the hardest thing in it is the clock.\n"
        f"  - The finished film runs {brief.duration_seconds} SECONDS. Not about "
        f"{brief.duration_seconds}. {brief.duration_seconds}.\n"
        f"  - Write EXACTLY {beats} beats. Not {beats + 1}. The CTA is the last "
        f"beat's line, not an extra beat after it.\n"
        f"  - Every `say` line added together must be {words} WORDS OR FEWER. "
        f"Count them before you answer. At {WORDS_PER_SECOND:g} words a second "
        f"that is the whole running time, and there is no room to trim later.\n"
        f"  - A {brief.duration_seconds}-second ad makes ONE point. Pick the "
        "strongest one and drop the rest — a compressed 30-second script reads "
        "as a list, which is the failure mode here."
    )

    if n.hook:
        L.append(f"\nREQUIRED HOOK — open on this line, or a faithful variant of it:\n{n.hook}")
    if n.voiceover:
        L.append(
            "\nREQUIRED VOICEOVER — this is the narration the client wrote. Deliver "
            f"its content across the beats; trim to fit the clock, never pad:\n{n.voiceover}"
        )
    if n.beats:
        L.append("\nREQUIRED BEATS — execute these in order, one beat each:")
        for i, beat in enumerate(n.beats):
            line = f"  {i + 1}. at {beat.t:g}s · shot: {beat.shot or '(director\'s choice)'}"
            if beat.on_screen_text:
                line += f" · on-screen text: {beat.on_screen_text}"
            L.append(line)
    if n.cta:
        L.append(f"\nREQUIRED CTA — the ad closes on this: {n.cta}")
    if n.text_overlay_policy:
        L.append(f"\nOn-screen text policy: {n.text_overlay_policy}")

    mechanisms = [
        f"  - {r.trend_key}: {r.why_it_works}" for r in brief.trend_refs if r.why_it_works
    ]
    if mechanisms:
        L.append(
            "\nWHY THE CLIENT PICKED THESE REFERENCES — borrow these mechanisms, "
            "in this order of importance:\n" + "\n".join(mechanisms)
        )

    tone = "; ".join(b.tone_guardrails)
    if tone:
        L.append(f"\nTone guardrails: {tone}")
    if b.must_say:
        L.append("\nMUST SAY — every one of these appears in the script verbatim:\n" +
                 "\n".join(f"  - {p}" for p in b.must_say))

    prohibited = _prohibitions(brief)
    if prohibited:
        L.append(
            "\nHARD PROHIBITIONS — these are not preferences. A script that "
            "breaks one is rejected outright:\n" + "\n".join(f"  - {p}" for p in prohibited)
        )

    if not brief.constraints.language.lower().startswith("en"):
        L.append(
            f"\nWrite every spoken line in {brief.constraints.language}. The hook "
            "grammar rules above were measured on English and are guidance here, "
            "not law."
        )

    if brief.signal_quality == "low":
        L.append(
            "\nSIGNAL QUALITY IS LOW — the client flagged its own references as "
            "thin. Claim less. Do not assert anything the sources below do not "
            "show; a vaguer ad that survives the evidence gate beats a confident "
            "one that does not."
        )
    return "\n".join(L)


def _prohibitions(brief: Brief) -> list[str]:
    out = [f'never say "{p}"' for p in brief.brand.never_say]
    if brief.constraints.no_real_people_likeness:
        out.append("no real person's name, likeness or implied endorsement")
    if brief.constraints.no_competitor_marks:
        out.append("no competitor brand names, logos or packaging, in copy or on screen")
    out += list(brief.constraints.safety_notes)
    out += sorted({a for r in brief.image_refs for a in r.avoid})
    return out


def lane_from_brief(lane: Lane, brief: Brief) -> Lane:
    """Fold the brief into the lane, which is how it reaches every stage.

    The lane is already the one object the planner, the writer, the voice and
    the imagery director all read, and `_fast` copies its brief into the
    company's mission before the writer sees it. Putting the brief here rather
    than threading a new argument through five call sites means a stage added
    tomorrow inherits the constraints for free.
    """
    return replace(
        lane,
        brief=direction_from_brief(brief, lane.brief),
        look=look_from_brief(brief, lane.look),
    )


# --- grounding ------------------------------------------------------------


TREND_COLUMNS = (
    "trend_key,platform,title,caption,source_url,author,format,hashtags,"
    "views,likes,engagement_rate,trend_score,posted_at"
)


async def resolve_trend_refs(
    supa: Supa, brief: Brief
) -> tuple[list[Trend], dict[str, int]]:
    """Fetch the brief's trend references from the corpus, in dominance order.

    Returns the same `(shortlist, rejection counts)` pair as
    `vira.select.shortlist`, so the caller cannot tell which retrieval produced
    its corpus — and the rejection panel keeps working, now reporting the
    references Lovable asked for that the corpus does not have.
    """
    # Imported rather than reimplemented: one bad row must be skipped the same
    # way here as in selection, and a second parser would drift from the first.
    from vira.select import _parse

    wanted = [r.trend_key for r in brief.trend_refs]
    if not wanted:
        return [], {}

    quoted = ",".join(f'"{k}"' for k in wanted)
    rows = await supa.select("trends", trend_key=f"in.({quoted})", select=TREND_COLUMNS)
    by_key = {}
    for row in rows:
        trend = _parse(row)
        if trend:
            by_key[trend.trend_key] = trend

    # Brief order, not corpus order. The lead reference has to arrive first —
    # that is the only thing that makes "dominates" mean anything downstream.
    picked = [by_key[k] for k in wanted if k in by_key]

    rejected: dict[str, int] = {}
    if missing := [k for k in wanted if k not in by_key]:
        log.warning("brief cited %d trend keys the corpus does not have: %s", len(missing), missing)
        rejected["cited by the brief but not in the corpus"] = len(missing)
    if dropped := [r for r in brief.references if r.key in set(brief.excluded)]:
        rejected["rejected by the brief"] = len(dropped)
    return picked, rejected


# --- did it fit? ----------------------------------------------------------


def budget_miss(brief: Brief, remix) -> str | None:
    """How far the finished script overran the brief's clock, or None if it fits.

    `durationSeconds` reaches the writer as an instruction, and an instruction is
    not a guarantee — a model handed "4 seconds, 10 words" has come back with 27.
    Measuring it afterwards, in Python, is the difference between a caller who
    knows their four-second ad is ten seconds long and one who finds out on
    playback. The clock is the one thing about a brief nothing downstream can fix:
    timings come from the synthesiser, so a long script is simply a long film.
    """
    beats, words = brief.shape
    if brief.narrative.beats:
        beats = len(brief.narrative.beats)

    spoken = len(remix.narration().split())
    if spoken <= words * OVERRUN_TOLERANCE and len(remix.beats) <= beats:
        return None
    return (
        f"the brief asked for {brief.duration_seconds}s — {beats} beats, "
        f"~{words} words. The script came back with {len(remix.beats)} beats and "
        f"{spoken} words, about {spoken / WORDS_PER_SECOND:.0f}s of narration"
    )


COMPRESS_SYSTEM = """You cut an ad script to length. You are not rewriting it.

The script below is too long for the slot it was commissioned for. Cut it to fit.

Rules:
- Hit the beat count and the word budget exactly. They are the brief, not a target.
- Keep the hook verbatim. It is the line the client approved.
- Keep the call to action, as the last beat's line. It is not an extra beat.
- Cut whole ideas, not adjectives. A four-second ad makes ONE point; find the
  point and delete everything that is not it. Trimming every line by a word
  produces a compressed list, which is worse than a short ad that says one thing.
- Preserve the beat schema exactly, including motion, camera and delivery.
- JSON only."""

COMPRESS_PROMPT = """# The script, which is too long
{script}

# The slot it has to fit
{beats} beats. {words} words across every `say` line, TOTAL. It currently has
{have_beats} beats and {have_words} words.

# Task
Return the cut script in the SAME JSON shape:
{{"hook": "...", "beats": [{{"t":0.0,"say":"...","show":"...","shot":"...",
   "motion":"stack|punch|slide|pop|banner","camera":"push|pull|pan|punch|hold",
   "delivery":"[tag]"}}],
  "caption":"...", "hashtags":[...], "cta":"...",
  "why_this_works":"...", "grounded_in":[...]}}"""


async def compress(remix, brief: Brief, trends: list[Trend]):
    """One pass to cut an over-long script to the brief's clock.

    Asking for four seconds and getting nine is not a near miss — timings come
    from the synthesiser, so the film is nine seconds and nothing later can
    shorten it. Measured on gpt-5.4, a 4-second brief comes back at 23–27 words
    against a 10-word budget however hard the instruction is phrased, because
    the writer is solving for a good ad and length is one constraint among
    twenty. Handing it back the finished script with cutting as the ONLY task is
    a different and much easier problem, and it is the same move
    `director.revise` already makes for a different kind of note.

    Bounded to one attempt and returns the original on any failure: a long ad is
    a worse ad, and no ad at all is worse than both.
    """
    import json as _json

    from vira.remix import parse_remix

    beats, words = brief.shape
    if brief.narrative.beats:
        beats = len(brief.narrative.beats)
    try:
        data = await complete_json(
            COMPRESS_PROMPT.format(
                script=_json.dumps(remix.model_dump(mode="json"), indent=2),
                beats=beats, words=words,
                have_beats=len(remix.beats),
                have_words=len(remix.narration().split()),
            ),
            system=COMPRESS_SYSTEM,
            max_tokens=3000,
        )
        cut = parse_remix(data, trends)
    except Exception as exc:  # noqa: BLE001 - a failed cut keeps the long script
        log.warning("compression failed, keeping the long script: %s", exc)
        return remix

    if len(cut.narration().split()) >= len(remix.narration().split()):
        log.warning("compression did not shorten anything; keeping the original")
        return remix
    # The client approved this line; a cut must not quietly reword it.
    if brief.narrative.hook:
        cut = cut.model_copy(update={"hook": brief.narrative.hook})
    return cut


# --- the gate -------------------------------------------------------------


def temper(score: Score, brief: Brief) -> Score:
    """Lower the engine's confidence when the brief admits its sources are thin.

    Only ever downward, and only on `evidence`. `signalQuality` is a claim about
    the corpus, so it belongs on the dimension that measures the corpus — and
    routing it through the score means it reaches `disposition` without any
    request parameter being able to move `EVIDENCE_FLOOR`, which stays where the
    architecture rules put it.
    """
    if brief.signal_quality != "low":
        return score
    return score.model_copy(
        update={"evidence": max(0.0, score.evidence - LOW_SIGNAL_EVIDENCE_PENALTY)}
    )


def confidence(brief: Brief, score: Score) -> str:
    """One word for a UI to print next to the result.

    Separate from `disposition`: a low-signal brief that still clears the gate
    produced a usable ad from weak inputs, and a caller deserves to know that
    the engine is less sure than the number suggests.
    """
    if brief.signal_quality == "low":
        return "low"
    return "high" if score.evidence >= 4.0 else "medium"
