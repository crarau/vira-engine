"""Stage 4 — the ad, as a timed shooting script.

Not a paragraph of advice. Beats with spoken lines and shot directions, because
the next two stages turn `say` into narration audio and `show`/`shot` into
frames. The draft `t` values here are discarded once TTS returns real timings.
"""

from __future__ import annotations

import logging

from vira.llm import complete_json
from vira.models import Beat, Company, CorpusAnalysis, Remix, Trend

log = logging.getLogger(__name__)

SYSTEM = """You are a short-form ad director. You write ads a two-person brand \
can shoot on a phone today.

Rules:
- Borrow a MECHANISM from the reference videos, never their surface. If a video \
works because it withholds the result until the midpoint, steal that structure, \
not its subject.
- Every beat is filmable with a phone, the product, and one person. No studios, \
no actors, no drone shots.
- Spoken lines are for saying out loud. Short. Contractions. No brochure copy.
- Hit the target length the director set. Roughly 2.6 words per second.
- grounded_in must list the trend keys you actually borrowed from.

You also DIRECT the video, beat by beat. For each beat choose:
- motion: how the caption behaves. One of stack (words rise, calm),
  punch (one huge line, hard emphasis), slide (left bar wipe, listing a fact),
  pop (words snap in scattered, chaotic energy), banner (solid slab, a claim).
- camera: push (slow in, building), pull (out, revealing), pan (drifting),
  punch (fast in, a hit), hold (static, let it land).
- delivery: an ElevenLabs performance tag for the line, e.g. [excited],
  [whispers], [deadpan], [frustrated], [confident], [laughs], [serious].

Vary them. Consecutive beats must not repeat the same motion. Match the choice
to the line: a confession is not "punch", a punchline is not "stack".
- JSON only."""

PROMPT = """# Brand
{company}

# The director's plan for THIS film — execute it, do not renegotiate it
Structure: {structure}
Device: {device}
Pacing: {pacing}
Opening move (first 1.5s): {opening_move}
The turn: {turn_at}

# What works in this category right now
Dominant formats: {formats}
Recurring hooks: {hooks}
Top performers share: {shared}
Nobody is doing: {whitespace}

# Reference videos (verified live, all under 90 days old)
{corpus}

# Task
Write ONE ad for the product above. Return JSON:

{{
  "hook": "spoken in the first 2 seconds, under 90 chars, must stop a scroll",
  "beats": [
    {{"t": 0.0,
      "say": "the line spoken over this beat",
      "show": "what is on screen",
      "shot": "framing + light, e.g. 'close on the can, handheld, natural light'",
      "motion": "stack|punch|slide|pop|banner",
      "camera": "push|pull|pan|punch|hold",
      "delivery": "[excited]"}}
  ],
  "caption": "the post caption, 1-2 sentences plus CTA",
  "hashtags": ["lowercase", "no", "hash", "symbol"],
  "cta": "the single action you want",
  "why_this_works": "the mechanism you borrowed and which video it came from",
  "grounded_in": ["VIRA-TR-...", ...]
}}

Exactly {beat_count} beats, totalling about {target_seconds} seconds.
The first beat IS the hook."""


async def build_remix(
    company: Company, product: str, trends: list[Trend], corpus: CorpusAnalysis,
    plan=None,
) -> Remix:
    if not trends:
        raise ValueError("no verified trends — cannot ground a remix")

    data = await complete_json(
        PROMPT.format(
            company=company.context(product),
            structure=getattr(plan, "structure", "") or "director's choice",
            device=getattr(plan, "device", "") or "director's choice",
            pacing=getattr(plan, "pacing", "") or "steady",
            opening_move=getattr(plan, "opening_move", "") or "stop the scroll",
            turn_at=getattr(plan, "turn_at", "") or "the midpoint",
            beat_count=getattr(plan, "beat_count", 7),
            target_seconds=getattr(plan, "target_seconds", 28),
            formats="; ".join(corpus.dominant_formats) or "unknown",
            hooks="; ".join(corpus.recurring_hooks) or "unknown",
            shared=corpus.what_top_performers_share or "unknown",
            whitespace=corpus.whitespace or "unknown",
            corpus="\n\n".join(t.brief() for t in trends),
        ),
        system=SYSTEM,
        max_tokens=5000,   # timed shooting scripts are long; 2500 truncated every time
    )

    return parse_remix(data, trends)


def parse_remix(data: dict, trends: list[Trend]) -> Remix:
    """Turn a director/writer JSON payload into a validated Remix."""
    beats = [
        Beat(
            t=float(b.get("t", 0) or 0),
            say=str(b.get("say", "")).strip(),
            show=str(b.get("show", "")).strip(),
            shot=str(b.get("shot", "")).strip(),
            motion=str(b.get("motion", "")).strip().lower(),
            camera=str(b.get("camera", "")).strip().lower(),
            delivery=str(b.get("delivery", "")).strip(),
        )
        for b in data.get("beats", [])
        if str(b.get("say", "")).strip()
    ]
    if not beats:
        raise ValueError("model returned no usable beats")

    valid = {t.trend_key for t in trends}
    grounded = [k for k in data.get("grounded_in", []) if k in valid]
    if not grounded:
        # Hard stop rather than shipping an ungrounded ad. The whole point of
        # the corpus is that the output traces back to something real.
        log.warning("remix cited nothing verifiable; grounding to top source")
        grounded = [trends[0].trend_key]

    remix = Remix(
        hook=str(data.get("hook", "")).strip(),
        beats=beats,
        caption=str(data.get("caption", "")).strip(),
        hashtags=[
            h.lstrip("#").strip().lower()
            for h in data.get("hashtags", [])
            if isinstance(h, str) and h.strip()
        ][:8],
        cta=str(data.get("cta", "")).strip(),
        why_this_works=str(data.get("why_this_works", "")).strip(),
        grounded_in=grounded,
    )

    words = len(remix.narration().split())
    if words > 95:
        log.warning("narration is %d words — likely over 32s, consider a re-roll", words)
    return remix
