"""What to type in the product box — proposed from the corpus, not invented.

The Generate page hands a user one free-text field and the cost of a bad answer
is measured: "Selling chips" scored 2.6, a product that named its mechanism
scored 3.8. Nobody arrives knowing which one they are typing, so the box needs
candidates — and the candidates have to come from the same material the ad will
later be graded against, or this is just a fluent model guessing, which is the
exact failure the evidence gate exists to catch.

Three properties make these worth showing.

**They are grounded in the rows selection would actually pick.** The corpus
slice is `vira.select.shortlist` verbatim: the same database-side freshness
filter, the same English heuristic, the same format quota. Anything grounded in
a row the selector would reject is a suggestion the evidence gate punishes
later, and re-deriving the filters here would let the two drift.

**A useless bio is reported, not smoothed over.** Half the companies in the
database have one-line bios — "I am the ceo", "Selling chips", "rkwejtkwegrg".
Paraphrasing one of those produces garbage in a confident tone, so the bio is
graded before the model is called and the verdict travels in the response.

**Grounding is enforced in Python.** A suggestion that cites no trend key in the
slice, or a lane that does not exist, is dropped after the model answers. Asking
for citations and trusting the reply is how ungrounded output ships.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from vira.lanes import BY_NAME, LANES
from vira.llm import LLMError, complete_json
from vira.models import Company, Trend
from vira.select import shortlist
from vira.supa import Supa, SupabaseError, get_company

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/suggest", tags=["suggest"])

# Wider than SHORTLIST_SIZE on purpose: this is a browsing decision, not a
# generation, and the extra rows buy angle variety at no render cost.
CORPUS_SLICE = 24

WANT_MIN, WANT_MAX = 4, 6

# --- cache ---------------------------------------------------------------
#
# One LLM call, ~10s, and an answer that is a function of the company row and
# the category's corpus slice — both of which move on the scale of days while a
# user reloads the Generate page on the scale of seconds. So: cache hard.
#
# Keyed by (slug, category_slug) rather than slug alone so a recategorised
# company misses naturally instead of serving suggestions grounded in a corpus
# it no longer belongs to. One hour is well inside how fast the corpus turns
# over (~1,600 new rows in an evening) and well outside a browsing session.
#
# In-process, not Redis: there is one uvicorn worker, and a suggestion that
# survives a restart is not worth a dependency. `?refresh=true` is the escape
# hatch, and it is the only thing that evicts early.
TTL_SECONDS = 3600
CACHE_MAX = 64


@dataclass(frozen=True)
class _Entry:
    payload: "Suggestions"
    at: float


_CACHE: dict[tuple[str, str], _Entry] = {}
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _lock_for(key: tuple[str, str]) -> asyncio.Lock:
    """One lock per key, so a cold page load twice over is one LLM call.

    Safe to build lazily: there is no await between the miss and the insert, so
    the event loop cannot interleave two creations of the same lock.
    """
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def _cached(key: tuple[str, str]) -> "Suggestions | None":
    entry = _CACHE.get(key)
    if entry is None:
        return None
    if time.monotonic() - entry.at > TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return entry.payload


def _store(key: tuple[str, str], payload: "Suggestions") -> None:
    if len(_CACHE) >= CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k].at)
        _CACHE.pop(oldest, None)
        _LOCKS.pop(oldest, None)
    _CACHE[key] = _Entry(payload=payload, at=time.monotonic())


# --- bio quality ---------------------------------------------------------

# A bio that is only a self-identification says nothing about a product. These
# are the real ones in the database, not hypotheticals: rebull is "I am the
# ceo", squirt is "i am the founder", vira is "I am the best sports drink ever".
_SELF_ID = re.compile(r"^\s*(i\s*am|i'm|we\s*are|we're|this\s+is)\b", re.IGNORECASE)

MIN_USABLE_CHARS = 60
MIN_USABLE_WORDS = 12


def _looks_like_words(text: str) -> bool:
    """Crude English-shape test, to catch keyboard mash like "rkwejtkwegrg".

    Two signals, either of which is enough to pass: English runs about 38%
    vowels, and English words almost never stack five consonants inside one
    token. A real bio clears the first comfortably; a mashed one clears
    neither.
    """
    tokens = re.findall(r"[A-Za-z]{2,}", text)
    if not tokens:
        return False
    letters = "".join(tokens).lower()
    vowel_share = sum(c in "aeiouy" for c in letters) / len(letters)
    longest_run = max(
        (len(run) for tok in tokens for run in re.findall(r"[^aeiouy]+", tok.lower())),
        default=0,
    )
    return vowel_share >= 0.30 or longest_run < 4


def grade_bio(bio: str, mission: str = "") -> "BioQuality":
    """Decide how much weight the bio can carry before the model sees it.

    The heuristic is deliberately blunt and deliberately pessimistic. A false
    "thin" costs a sentence of prompt telling the model to lean on the corpus,
    which it should be doing anyway. A false "usable" costs a suggestion
    paraphrased out of "I am the ceo".
    """
    text = (bio or "").strip()
    words = len(text.split())

    if not text:
        return BioQuality(
            verdict="junk", reason="no bio at all", chars=0, words=0,
            lean_on_corpus=True,
        )
    if not _looks_like_words(text):
        return BioQuality(
            verdict="junk", reason="does not read as words — looks like filler text",
            chars=len(text), words=words, lean_on_corpus=True,
        )
    if _SELF_ID.match(text) and words <= 10:
        return BioQuality(
            verdict="junk",
            reason="says who the author is, not what the product does",
            chars=len(text), words=words, lean_on_corpus=True,
        )
    if len(text) < 25 or words < 5:
        return BioQuality(
            verdict="junk", reason="too short to describe a product",
            chars=len(text), words=words, lean_on_corpus=True,
        )
    if len(text) < MIN_USABLE_CHARS or words < MIN_USABLE_WORDS:
        return BioQuality(
            verdict="thin",
            reason="names a category but no mechanism, claim or customer",
            chars=len(text), words=words, lean_on_corpus=True,
        )
    return BioQuality(
        verdict="usable",
        reason="long enough to describe a product in its own terms",
        chars=len(text), words=words,
        # Even a good bio is a claim about the brand, never evidence about the
        # market. The corpus is still what the suggestions must cite.
        lean_on_corpus=not (mission or "").strip(),
    )


# --- wire shapes ---------------------------------------------------------


class BioQuality(BaseModel):
    verdict: Literal["usable", "thin", "junk"]
    reason: str
    chars: int
    words: int
    lean_on_corpus: bool


class Suggestion(BaseModel):
    product: str = Field(description="drops straight into the product field")
    angle: str
    lane: str
    lane_reason: str
    grounded_in: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SourceRow(BaseModel):
    """Enough of a cited trend for a UI to link it without a second request."""

    trend_key: str
    source_url: str
    author: str = ""
    caption: str = ""
    format: str = ""
    views: int = 0
    age_days: float | None = None


class CorpusFacts(BaseModel):
    slice_size: int
    rejected: dict[str, int] = Field(default_factory=dict)
    category: str = ""
    max_age_days: int = 90


class Suggestions(BaseModel):
    company_slug: str
    company_name: str
    category: str = ""
    bio_quality: BioQuality
    suggestions: list[Suggestion] = Field(default_factory=list)
    sources: list[SourceRow] = Field(default_factory=list)
    corpus: CorpusFacts
    note: str | None = None
    generated_at: str
    cached: bool = False
    elapsed_ms: int = 0


# --- the prompt ----------------------------------------------------------

SYSTEM = """You propose product angles for a short-form video ad engine. Two \
hard rules, and breaking either makes the output worthless.

1. GROUNDED. You are shown a verified slice of the corpus this ad will actually \
be built from and later graded against. Every suggestion must cite the \
trend_keys it drew from, and its evidence lines must point at what those \
specific rows contain — a format that recurs, a hook that repeats, an absence \
worth owning. A suggestion you cannot cite does not go in the output.

2. NO INVENTION. Do not propose a product line the company plainly does not \
sell. You are choosing which real thing to push and how to frame it, not \
extending the catalogue. If the brand sells one product, all your suggestions \
are angles on that one product.

What a good `product` string looks like: it names the mechanism, the specific \
claim, or the moment of use. 8-20 words. It reads like a sentence someone who \
has used the thing would write.
  bad:  "Selling chips"                                    (scored 2.6)
  bad:  "sunscreen"
  good: "a mineral SPF 50 serum that leaves no white cast on deeper skin tones"  (scored 3.8)
  good: "a slow-release treat dispenser that keeps anxious dogs settled when left alone"

Keep every string under 220 characters. JSON only."""

PROMPT = """# The company
{company}

# Bio quality: {verdict} — {reason}
{bio_guidance}

# The corpus slice these suggestions must be grounded in
{n} verified rows, category "{category}", all under {max_age} days old, English, \
format-quota'd. This is the same slice the generator will select from.

{corpus}

# The lanes available (pick the one whose creative identity fits the angle)
{lanes}

# Task
Propose {want_min}-{want_max} distinct products/angles for THIS company that \
THIS corpus can support. Different angles, not five rewordings of one.

Return JSON:
{{
  "suggestions": [
    {{
      "product": "the product string, 8-20 words, names the mechanism or claim",
      "angle": "one sentence on why this framing, under 200 chars",
      "lane": "one of: {lane_names}",
      "lane_reason": "why that lane, under 150 chars",
      "grounded_in": ["VIRA-TR-...", ...],   // trend keys from the slice above, at least one
      "evidence": ["what in those rows supports this — quote the caption or name the pattern", ...]  // 1-2 lines
    }}
  ]
}}"""

JUNK_BIO_GUIDANCE = """This bio cannot be trusted to describe the product. Do \
NOT paraphrase it and do NOT treat it as a product description. Derive the \
suggestions from the corpus slice and the category, and propose products that a \
brand in this category plausibly sells — say plainly in each `angle` that the \
framing comes from the category corpus rather than from anything the brand told \
us."""

THIN_BIO_GUIDANCE = """This bio names a category but not a mechanism. Use it \
only to stay inside the right product space; the specificity in each suggestion \
must come from the corpus, not from expanding the bio into more words."""

USABLE_BIO_GUIDANCE = """This bio is substantial enough to say what the company \
sells. Stay inside it — but the reason for each angle still has to come from \
the corpus, not from the bio."""

_GUIDANCE = {
    "junk": JUNK_BIO_GUIDANCE,
    "thin": THIN_BIO_GUIDANCE,
    "usable": USABLE_BIO_GUIDANCE,
}


def _lane_block() -> str:
    return "\n".join(f"- {l.name}: {l.brief}" for l in LANES)


def _corpus_block(trends: list[Trend]) -> str:
    return "\n\n".join(t.brief() for t in trends)


def _source_rows(trends: list[Trend], keys: set[str]) -> list[SourceRow]:
    return [
        SourceRow(
            trend_key=t.trend_key,
            source_url=t.source_url,
            author=t.author,
            caption=t.caption[:200],
            format=t.format,
            views=t.views,
            age_days=round(t.age_days, 1) if t.posted_at else None,
        )
        for t in trends
        if t.trend_key in keys
    ]


def _clean(value: object, cap: int = 220) -> str:
    return str(value or "").strip()[:cap]


def _validate(raw: list, valid_keys: set[str]) -> tuple[list[Suggestion], list[str]]:
    """Enforce the two hard rules after the fact, and report what that cost.

    A model told to cite will mostly cite, and "mostly" is how an ungrounded
    suggestion reaches a user. Uncitable suggestions and invented lanes are
    dropped here rather than corrected, because a silently repaired lane is a
    recommendation nobody made.
    """
    kept: list[Suggestion] = []
    dropped: list[str] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        product = _clean(item.get("product"))
        if len(product.split()) < 4:
            dropped.append(f"{product or '(empty)'!r}: not a product sentence")
            continue

        cited = [k for k in item.get("grounded_in") or [] if k in valid_keys]
        if not cited:
            dropped.append(f"{product!r}: cited nothing in the slice")
            continue

        lane = _clean(item.get("lane"), 64)
        if lane not in BY_NAME:
            dropped.append(f"{product!r}: unknown lane {lane!r}")
            continue

        kept.append(Suggestion(
            product=product,
            angle=_clean(item.get("angle")),
            lane=lane,
            lane_reason=_clean(item.get("lane_reason"), 150),
            grounded_in=cited[:6],
            evidence=[_clean(e, 300) for e in (item.get("evidence") or [])][:2],
        ))
        if len(kept) >= WANT_MAX:
            break

    return kept, dropped


# --- the endpoint --------------------------------------------------------


@router.get("/{company_slug}", response_model=Suggestions)
async def suggest(
    company_slug: str,
    refresh: bool = Query(False, description="bypass the cache and pay for a new call"),
) -> Suggestions:
    """Four to six product angles this company's corpus can actually support."""
    started = time.monotonic()
    supa = Supa()

    try:
        row = await get_company(supa, company_slug)
    except SupabaseError as exc:
        raise HTTPException(502, f"could not read the company: {exc}") from exc
    if not row:
        raise HTTPException(404, f"no company with slug {company_slug!r}")

    company = Company.from_row(row)
    category_slug = (row.get("categories") or {}).get("slug") or ""
    key = (company_slug, category_slug)

    if not refresh and (hit := _cached(key)):
        return hit.model_copy(update={
            "cached": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })

    async with _lock_for(key):
        # A queue of page loads behind one cold call must not each pay for it.
        if not refresh and (hit := _cached(key)):
            return hit.model_copy(update={
                "cached": True,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
        payload = await _build(supa, company, category_slug)
        _store(key, payload)

    return payload.model_copy(update={
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    })


async def _build(supa: Supa, company: Company, category_slug: str) -> Suggestions:
    from vira.config import settings

    s = settings()
    bio_quality = grade_bio(company.bio, company.mission)

    try:
        trends, rejected = await shortlist(supa, company, "", limit=CORPUS_SLICE)
    except SupabaseError as exc:
        raise HTTPException(502, f"could not read the corpus: {exc}") from exc

    facts = CorpusFacts(
        slice_size=len(trends),
        rejected=rejected,
        category=company.category or category_slug,
        max_age_days=s.max_age_days,
    )
    base = dict(
        company_slug=company.slug,
        company_name=company.name,
        category=company.category or category_slug,
        bio_quality=bio_quality,
        corpus=facts,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    if not trends:
        # An empty slice is a real answer — pets has 380 trends and no voice
        # corpus at all, and a model handed nothing will still write five
        # confident suggestions. Say it instead.
        return Suggestions(
            **base,
            note=(
                f"no fresh, English, {facts.category or 'category'} rows in the "
                f"{s.max_age_days}-day window — nothing here can be grounded"
            ),
        )

    prompt = PROMPT.format(
        company=company.context("(not chosen yet — that is what you are proposing)"),
        verdict=bio_quality.verdict,
        reason=bio_quality.reason,
        bio_guidance=_GUIDANCE[bio_quality.verdict],
        n=len(trends),
        category=facts.category,
        max_age=s.max_age_days,
        corpus=_corpus_block(trends),
        lanes=_lane_block(),
        lane_names=", ".join(BY_NAME),
        want_min=WANT_MIN,
        want_max=WANT_MAX,
    )

    try:
        # Six suggestions with two evidence lines each measured at ~2.5k output
        # tokens, and `complete_json` answers truncation by paying for a second,
        # larger call — 72s instead of 36s. Budget past the observed ceiling.
        data = await complete_json(prompt, system=SYSTEM, max_tokens=6000)
    except LLMError as exc:
        raise HTTPException(502, f"the model would not produce suggestions: {exc}") from exc

    valid_keys = {t.trend_key for t in trends}
    kept, dropped = _validate(data.get("suggestions") or [], valid_keys)
    if dropped:
        log.warning("dropped %d ungrounded suggestions: %s", len(dropped), dropped)

    cited = {k for sug in kept for k in sug.grounded_in}
    notes: list[str] = []
    if bio_quality.verdict == "junk":
        notes.append(
            f"bio is unusable ({bio_quality.reason}) — these lean on the "
            f"{facts.category} corpus, not on anything the brand said"
        )
    if dropped:
        # Returning four weak suggestions would hide this; naming it is the
        # same discipline as the drop panel on a scored video.
        notes.append(
            f"{len(dropped)} suggestion(s) dropped — cited nothing in the slice, "
            "or named a lane that does not exist"
        )
    elif len(kept) < WANT_MIN:
        notes.append(f"the corpus only supported {len(kept)}")

    return Suggestions(
        **base,
        suggestions=kept,
        sources=_source_rows(trends, cited),
        note="; ".join(notes) or None,
    )
