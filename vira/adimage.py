"""The static ad: the video pipeline with the clock taken out.

Built to `docs/IMAGE-API.md` §2, which is the contract.

An ad image is not "Gemini, draw me something with the hook on it". That
produces a picture, and a picture is not an ad — it is ungrounded, unscored, and
it looks nothing like the films the same brand is running. So this stage runs
the same pipeline the video runs, minus the two stages that only exist because a
video moves:

    select → verify → analyze → write → imagery → BURN TEXT → score
                                          ↑                     ↑
                            no voice, no motion          the same gate

Every one of those is the module the video path calls, unchanged. What this file
contributes is the three decisions a still forces:

**A one-beat script.** `build_remix` writes to a plan, and a plan of one beat
over three seconds is a legitimate plan — so the writer produces a hook, one
shot description and a CTA, under the same hook grammar and citing the same
corpus. The scorer then works with no changes at all, because what it grades is
a concept against its sources and a concept does not need a duration.

**The bottom third is spoken for.** `video/src/Captions.tsx` owns the lowest
third of the frame, so the photograph has to be composed for that. The
instruction goes into the style contract rather than being fixed by cropping
afterwards, because a model told to leave headroom does, and a crop cannot
recover a subject that was never given any.

**The clean frame survives.** Callers who want to lay out their own text get
`image_url`, so the burn is an addition to the output rather than a destruction
of it.

The evidence gate is the same call, after the creative work, in Python. A static
ad the corpus does not support is dropped exactly like a film that is not.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from vira.analyze import analyze_corpus
from vira.brief import Brief, BriefPlan, direction_from_brief, look_from_brief, temper
from vira.brief import confidence as brief_confidence
from vira.brief import resolve_trend_refs
from vira.config import settings
from vira.lanes import LANES, Lane
from vira.models import Company, Remix, Score, Trend
from vira.provenance import Recorder
from vira.remix import build_remix
from vira.render import VIDEO_DIR
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.shots import fetch_or_generate
from vira.still import build_still_props, render_still, write_still_props
from vira.supa import Supa, fresh_company_trends, get_company
from vira.verify import verify_all

log = logging.getLogger(__name__)

# Appended to whatever look the brief or lane asked for. Not negotiable: the
# caption band is a fixed third of the frame and a subject composed into it is a
# subject with a word printed over its face.
BAND_RULE = (
    "Compose for a 9:16 poster whose LOWEST THIRD is reserved for large printed "
    "text: keep the subject and every important detail in the upper two thirds, "
    "and leave the bottom third quiet — floor, surface, shadow or empty space."
)

# The hook grammar each lane's angle naturally takes, from `director.HOOK_SHAPES`.
# The video path has a model choose; a static ad has one line and no critic pass
# to recover from a bad choice, so the mapping is fixed and reportable.
LANE_HOOK_SHAPE: dict[str, str] = {
    "problem-first": "second-person-consequence",
    "demo-first": "first-person-plural-claim",
    "founder-story": "first-person-admission",
    "social-proof": "reported-speech",
    "contrarian": "withheld-referent",
}

ASPECTS = frozenset({"9:16", "16:9", "1:1", "4:3", "3:4", "2:3", "3:2"})
BURNABLE_ASPECT = "9:16"

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class AdImageFailed(RuntimeError):
    """A static ad that stopped for a reason worth showing the caller."""


class AdImageUnconfigured(RuntimeError):
    """An upstream this endpoint needs has no credential on this box. A 503."""


@dataclass(slots=True)
class AdImage:
    """One finished static ad and the engine's verdict on it."""

    ad_id: str
    lane: str
    hook_shape: str | None
    # Relative to the media root. `ad` is the burned poster, `frame` the clean
    # photograph under it; they are the same file when burn_text is off.
    ad_path: str
    frame_path: str
    recipe_path: str
    headline: str
    cta: str
    caption: str
    hashtags: list[str]
    score: Score
    disposition: str
    drop_reason: str | None
    confidence: str
    grounded_in: list[Trend] = field(default_factory=list)
    sources: list[Trend] = field(default_factory=list)
    image_prompt: str = ""
    style_contract: str = ""
    burned: bool = True
    elapsed_ms: int = 0


# --- the choices the caller left to the engine ---------------------------


def pick_lane(slug: str, product: str) -> Lane:
    """The lane, when the caller did not name one.

    Deterministic on (brand, product) rather than random or model-chosen, for
    one reason: every ad writes a recipe, and a recipe whose first decision is a
    coin toss cannot be re-run. The same request twice produces the same angle,
    and `GET /v1/lanes` is there for a caller who wants to pin a different one.
    """
    digest = hashlib.sha1(f"{slug}:{product}".lower().encode()).digest()
    return LANES[digest[0] % len(LANES)]


def still_plan(lane: Lane, brief: Brief | None, hook_supplied: bool) -> BriefPlan:
    """One beat, no clock. The writer still gets a plan; it is just a short one."""
    device = "the mechanism in the strongest reference, compressed into one frame"
    if brief and (lead := brief.lead) is not None:
        device = getattr(lead, "why_it_works", "") or device
    return BriefPlan(
        structure=(
            "ONE still frame. Nothing moves and nothing is spoken — the hook is "
            "PRINTED over the photograph, so it has to land read rather than heard."
        ),
        device=device,
        beat_count=1,
        target_seconds=3,
        pacing="instant",
        opening_move="the hook is the entire ad; there is no build-up to hide behind",
        turn_at="there is no turn — the tension and its release are in one line",
        # A supplied headline is already fixed, so naming a shape for it would
        # hand the writer two authorities that can disagree.
        hook_shape="" if hook_supplied else LANE_HOOK_SHAPE.get(lane.name, ""),
        rationale="static ad: one beat, one photograph, one printed line",
    )


def _static_direction(brief: Brief | None, lane: Lane, headline: str | None) -> str:
    base = direction_from_brief(brief, lane.brief) if brief else lane.brief
    out = (
        f"{base}\n\nTHIS IS A STATIC AD, NOT A FILM. Write exactly one beat. Its "
        "`say` is the line that will be PRINTED on the image, so it must read as "
        "well in silence as it would out loud. Its `show` and `shot` describe the "
        "single photograph. The CTA is printed under the line — keep it to a few "
        "words."
    )
    if headline:
        out += (
            "\n\nTHE HEADLINE IS ALREADY WRITTEN. Use this line verbatim as the "
            f"hook and as the beat's `say`:\n{headline}\n"
            "Your job is the photograph, the CTA and the grounding, not the line."
        )
    return out


# --- grounding ------------------------------------------------------------


async def _by_category(supa: Supa, category: str) -> tuple[list[Trend], dict[str, int]]:
    """Shortlist against a named category slug, for a brand Lovable has never seen.

    `vira.select.shortlist` reaches the category through the company row, which
    an unregistered brand does not have. The filters below are the same ones —
    imported, not restated — so a category-grounded ad is filtered exactly like
    a company-grounded one.
    """
    from vira.select import _looks_english, _parse

    s = settings()
    cats = await supa.select("categories", slug=f"eq.{category}", select="id")
    if not cats:
        raise AdImageFailed(f"no category {category!r} in the corpus")

    since = (datetime.now(timezone.utc) - timedelta(days=s.max_age_days)).isoformat()
    rows = await fresh_company_trends(supa, cats[0]["id"], since_iso=since, limit=300)
    candidates = [t for t in (_parse(r) for r in rows) if t is not None]

    rejected: Counter[str] = Counter()
    kept: list[Trend] = []
    for t in candidates:
        if not t.source_url:
            rejected["no source url"] += 1
        elif t.age_days > s.max_age_days:
            rejected[f"older than {s.max_age_days}d"] += 1
        elif s.english_only and not _looks_english(t.caption):
            rejected["not english"] += 1
        else:
            kept.append(t)

    kept.sort(key=lambda t: t.trend_score, reverse=True)
    per_format: Counter[str] = Counter()
    diverse: list[Trend] = []
    for t in kept:
        fmt = t.format or "unknown"
        if per_format[fmt] >= s.max_per_format:
            rejected["format quota"] += 1
            continue
        per_format[fmt] += 1
        diverse.append(t)
        if len(diverse) >= s.shortlist_size:
            break
    return diverse, dict(rejected)


async def _ground(
    supa: Supa, company: Company, product: str, brief: Brief | None,
    category: str | None, registered: bool,
) -> tuple[list[Trend], dict[str, int]]:
    """The brief's references when it has them, then the company, then a category."""
    if brief and brief.trend_refs:
        picked, rejected = await resolve_trend_refs(supa, brief)
        if picked:
            return picked, rejected
        log.warning("no brief reference resolved against the corpus; falling back")
    if registered:
        return await shortlist(supa, company, product)
    if category:
        return await _by_category(supa, category)
    raise AdImageFailed(
        f"{company.name!r} is not in the corpus and no category was given — "
        "send `category`, or a brief carrying trendKey references"
    )


# --- the frame ------------------------------------------------------------


async def _gemini_frame(prompt: str, dest: Path, name: str, aspect: str) -> dict:
    """One image at an aspect ratio the video path never needs.

    `vira.imagegen` is pinned to 9:16 because the composition is, so a caller
    asking for a clean 1:1 frame cannot go through it. This is the same call
    with the ratio unpinned, and it deliberately has no stock fallback: a
    fallback photograph would not be the aspect ratio that was asked for.
    """
    s = settings()
    if not s.gemini_api_key:
        raise AdImageUnconfigured("image generation is not configured on this server")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect},
        },
    }
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            GEMINI_ENDPOINT.format(model=s.image_model),
            params={"key": s.gemini_api_key}, json=body,
        )
    if r.status_code >= 400:
        raise AdImageFailed(f"upstream image model: {r.text[:220]}")
    for part in r.json().get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if inline := part.get("inlineData"):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / name).write_bytes(base64.b64decode(inline["data"]))
            return {"file": name, "credit": "generated · Gemini", "prompt": prompt}
    raise AdImageFailed("the image model returned no frame — likely a safety filter")


async def _draw(
    company: Company, product: str, remix: Remix, look: str,
    dest: Path, aspect: str,
) -> list[dict]:
    if aspect == BURNABLE_ASPECT:
        # The 9:16 path goes through the pipeline's own generator so it inherits
        # the style contract and the per-frame fallback to stock.
        return await fetch_or_generate(company, product, remix, dest, look)

    from vira.imagegen import NEGATIVE, derive_prompts

    style, prompts = await derive_prompts(company, product, remix, look)
    shot = await _gemini_frame(
        f"{style} {prompts[0]} {NEGATIVE}", dest, "shot00.jpg", aspect
    )
    return [{**shot, "query": prompts[0], "style_contract": style}]


# --- the whole thing ------------------------------------------------------


async def produce_ad_image(
    *,
    brand: str,
    product: str,
    out_dir: Path,
    out_root: Path,
    lane: Lane | None = None,
    category: str | None = None,
    headline: str | None = None,
    aspect_ratio: str = BURNABLE_ASPECT,
    burn_text: bool = True,
    company_slug: str | None = None,
    brief: Brief | None = None,
    render_slot: asyncio.Semaphore | None = None,
    note=None,
) -> AdImage:
    """Ground, write, draw, burn, score. Returns the ad and the engine's verdict.

    Never decides whether the caller should see it — `disposition` is returned
    and the route turns a drop into a 409. Keeping the HTTP status out of here
    is what lets the CLI use the same function.
    """
    t0 = time.monotonic()
    say = note or (lambda *_a, **_kw: None)
    s = settings()
    ad_id = f"img_{uuid.uuid4().hex[:6]}"

    if aspect_ratio not in ASPECTS:
        raise AdImageFailed(f"aspect_ratio must be one of {sorted(ASPECTS)}")

    supa = Supa()
    from vira.brief import company_from_brief, slugify

    slug = company_slug or (brief.slug if brief else slugify(brand))
    row = await get_company(supa, slug)
    if brief is not None:
        company = company_from_brief(brief, row)
    elif row:
        company = Company.from_row(row)
    else:
        # An unregistered brand is not an error — it is the common case for a
        # first ad. It just has to say which slice of the corpus to stand on.
        company = Company(id=slug, name=brand, slug=slug, category=category or "")

    lane = lane or pick_lane(slug, product)
    look = (
        f"{look_from_brief(brief, lane.look)} {BAND_RULE}"
        if brief
        else f"{lane.look} {BAND_RULE}"
    )
    fixed_headline = headline or (brief.narrative.hook if brief else "") or None
    direction = _static_direction(brief, lane, fixed_headline)
    shots_dir = VIDEO_DIR / "public" / "shots" / ad_id
    # The Recorder only creates this when it finishes, and the frame lands here
    # before then.
    out_dir.mkdir(parents=True, exist_ok=True)

    async with Recorder(out_dir) as rec:
        rec.note("kind", "static-ad")
        rec.note("ad_id", ad_id)
        rec.note("lane", lane.name)
        rec.note("lane_brief", direction)
        rec.note("look", look)
        rec.note("aspect_ratio", aspect_ratio)
        rec.note("burn_text", burn_text)
        rec.note("supplied_headline", fixed_headline)
        rec.note(
            # In their names, so the recorded brief can be posted straight back.
            "brief", brief.model_dump(mode="json", by_alias=True) if brief else None,
        )

        say("grounding the ad in the corpus", "select")
        picked, rejected = await _ground(
            supa, company, product, brief, category, registered=bool(row)
        )
        say(f"verifying {len(picked)} source URLs", "verify", sources=len(picked))
        picked, dead = await verify_all(picked)
        if not picked:
            raise AdImageFailed("nothing survived verification — no sources to ground on")
        rec.note("rejected_at_selection", rejected)
        rec.note("dead_urls", len(dead))

        say(f"analysing {len(picked)} verified sources", "analyze", verified=len(picked))
        corpus = await analyze_corpus(company, product, picked)

        plan = still_plan(lane, brief, hook_supplied=bool(fixed_headline))
        steered = Company(**{
            **company.model_dump(),
            "mission": f"{company.mission}\n\nCREATIVE DIRECTION FOR THIS AD: {direction}",
        })
        say("writing the frame", "write")
        remix = await build_remix(steered, product, picked, corpus, plan)
        if fixed_headline:
            # The caller's line is the printed line, whatever the writer did with
            # it. Validation already happened at the edge, so this cannot smuggle
            # a hook past the rules.
            remix = remix.model_copy(update={"hook": fixed_headline})
        rec.note("plan", plan.model_dump())

        # Scoring needs only the script and drawing needs only the script, so
        # neither waits for the other. Same argument as voice ‖ imagery on the
        # video path: the gate is the last thing consulted, not the last thing run.
        say("drawing the frame and grading the concept", "imagery")
        score, shots = await asyncio.gather(
            score_remix(company, product, remix, picked),
            _draw(company, product, remix, look, shots_dir, aspect_ratio),
        )

        name = shots[0].get("file") if shots else None
        if not name:
            raise AdImageFailed("no frame was produced — nothing to build the ad on")
        frame = out_dir / name
        shutil.copy(shots_dir / name, frame)

        if burn_text:
            say("printing the headline", "render")
            props = build_still_props(company, remix, image=f"{ad_id}/{name}")
            props_path = write_still_props(props, out_dir)
            poster = out_dir / "ad.jpg"
            slot = render_slot or asyncio.Semaphore(1)
            async with slot:
                await asyncio.to_thread(render_still, props_path, poster)
        else:
            poster = frame

        if brief is not None:
            before = score.evidence
            score = temper(score, brief)
            if score.evidence < before:
                say("signal quality is low — evidence scored down before the gate",
                    "score", level="warn")

        say("scoring against the cited sources", "score")
        dispo, reason = disposition(score)
        rec.finish(
            company=company, product=product, remix=remix, score=score,
            shots=shots, sources=picked, voice_id=None,
            settings_snapshot={
                "kind": "static-ad",
                "lane": lane.name,
                "hook_shape": plan.hook_shape,
                "aspect_ratio": aspect_ratio,
                "burn_text": burn_text,
                "llm_model": s.agent_model,
                "image_model": s.image_model,
                "max_age_days": s.max_age_days,
                "evidence_floor": s.evidence_floor,
                "surface_threshold": s.surface_threshold,
                "watchlist_threshold": s.watchlist_threshold,
                "signal_quality": brief.signal_quality if brief else "high",
            },
        )

    shot = shots[0]
    cited = {t.trend_key for t in picked} & set(remix.grounded_in)
    return AdImage(
        ad_id=ad_id,
        lane=lane.name,
        hook_shape=plan.hook_shape or None,
        ad_path=str(poster.relative_to(out_root)),
        frame_path=str(frame.relative_to(out_root)),
        recipe_path=str((out_dir / "RECIPE.md").relative_to(out_root)),
        headline=remix.hook,
        cta=remix.cta,
        caption=remix.caption,
        hashtags=remix.hashtags,
        score=score,
        disposition=dispo,
        drop_reason=reason,
        confidence=brief_confidence(brief, score) if brief else (
            "high" if score.evidence >= 4.0 else "medium"
        ),
        grounded_in=[t for t in picked if t.trend_key in cited],
        sources=picked,
        image_prompt=str(shot.get("prompt") or shot.get("query") or ""),
        style_contract=str(shot.get("style_contract") or ""),
        burned=burn_text,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
