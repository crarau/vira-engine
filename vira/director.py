"""Stage 3.5 — the director. Plans the film before anyone writes a line.

Without this stage every video comes out the same shape, because the script
prompt hard-codes "5-8 beats, 20-32 seconds". Five ads that differ only in
wording are one ad in five costumes — which is exactly what the scorer kept
flagging as low differentiation.

So the structure is a decision, not a constant. The director picks how long the
film is, how many beats it has, how the pace moves, and what structural device
carries it. The writer then works inside that plan, and the executors (voice,
imagery, motion) inherit it.

There is also a critic. It reads the finished script the way a viewer would —
cold, on a phone, ready to scroll — and says what is flat. One revision pass on
concrete notes beats three re-rolls hoping for a better sample.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from vira.llm import complete_json
from vira.models import Company, CorpusAnalysis, Remix, Trend

log = logging.getLogger(__name__)


class VideoPlan(BaseModel):
    structure: str = ""          # "cold-open montage", "single-take confession"
    device: str = ""             # the trick that carries it
    beat_count: int = 7
    target_seconds: int = 28
    pacing: str = ""             # "accelerating", "front-loaded", "slow burn"
    opening_move: str = ""       # what happens in the first 1.5 seconds
    turn_at: str = ""            # where it changes gear
    hook_shape: str = ""         # the GRAMMAR of the first line, see HOOK_SHAPES
    rationale: str = ""


# The measured-permitted opening classes, from docs/HOOK-CRAFT.md. The director
# picks one per film. Without this every lane converges on the same grammar --
# which is what "the hooks are samey" actually means: not a vocabulary problem,
# an identical clause structure across all five variants.
HOOK_SHAPES: dict[str, str] = {
    "first-person-admission": (
        "Open on 'I' plus a past-tense verb. A thing you did, believed or got "
        "wrong. e.g. 'I gave up on X for two YEARS.'"
    ),
    "first-person-plural-claim": (
        "Open on 'We' plus a present-tense verb. What the brand does or refuses "
        "to do, stated flatly. e.g. 'We tested this on FORTY people first.'"
    ),
    "second-person-consequence": (
        "'You' is the subject and something happens TO them. Not a command. "
        "e.g. 'You have been reapplying this WRONG.'"
    ),
    "reported-speech": (
        "Attribute the wrong belief to someone else, then stand against it. "
        "e.g. 'My dermatologist told me to STOP using it.'"
    ),
    "counted-anchor": (
        "One number as subject or object of a real verb -- never a bare "
        "listicle label. e.g. 'I wore it for THIRTY days straight.'"
    ),
    "withheld-referent": (
        "A finite clause whose object is deliberately unnamed, forcing the next "
        "beat. e.g. 'I stopped buying the one thing everybody RECOMMENDS.'"
    ),
    "overheard-question": (
        "A genuine question the viewer has been asked or has asked. Ends in '?'. "
        "e.g. 'Have you actually READ what is in yours?'"
    ),
}


class Critique(BaseModel):
    weakest_beat: int = 0
    verdict: str = ""
    notes: list[str] = Field(default_factory=list)
    scroll_risk: str = ""        # why a viewer would swipe away


PLAN_SYSTEM = """You are a short-form video director. Before anyone writes copy,
you decide the SHAPE of the film.

Shape is a real decision with real range:
- Length: 12 seconds can outperform 35. Choose deliberately.
- Beat count: 4 long beats is a different film from 10 quick cuts.
- Pacing: accelerating, front-loaded, slow burn, or metronomic.
- Device: the structural trick doing the work — withhold the reveal, count down,
  repeat-and-break, false ending, list, single unbroken take, before/after.

Vary the shape to the angle. A founder confession wants few long beats and a
slow burn. A social-proof cut wants many short ones, accelerating. Do NOT
default to seven beats at thirty seconds — that is the safe average and it is
why generated ads all feel the same.

You also fix the GRAMMAR of the first line, by picking one hook shape from the
list you are given. Pick the one that fits the angle, not the one that sounds
best in isolation — the whole point is that five ads for one brand do not all
open with the same clause structure.

JSON only."""

PLAN_PROMPT = """# Brand
{company}

# Creative angle for this specific ad
{lane_brief}

# What is working in this category
Dominant formats: {formats}
Recurring hooks: {hooks}
Top performers share: {shared}
Nobody is doing: {whitespace}

# Hook shapes you may choose from — these are the only permitted ones
{hook_shapes}

# Task
Return JSON:
{{
  "structure": "name the shape in a few words",
  "device": "the structural trick carrying the film",
  "beat_count": <integer 4-10>,
  "target_seconds": <integer 12-40>,
  "pacing": "accelerating|front-loaded|slow burn|metronomic",
  "opening_move": "what happens in the first 1.5 seconds, before any pitch",
  "turn_at": "where and how the film changes gear",
  "hook_shape": "exactly one key from the list above",
  "rationale": "why this shape suits this angle, under 200 chars"
}}"""

CRITIQUE_SYSTEM = """You are a hostile first viewer. Phone in hand, thumb ready.

You are not being asked to be nice. You are being asked where this loses you.
Be concrete and beat-specific: "beat 3 restates beat 2" is useful, "could be
punchier" is not.

Judge: does the first line stop a scroll? Does any beat repeat another? Is the
middle dead? Does the CTA earn itself? Does it sound like a person or like
marketing?

You are also handed a list of measured grammar faults in the hook. Those are not
opinions — they come from 2,669 ranked TikToks. If the list is non-empty, the
weakest beat is beat 1 and your first note must fix the hook. JSON only."""

CRITIQUE_PROMPT = """# The ad
Hook: {hook}
{beats}
CTA: {cta}

Plan it was meant to execute: {structure} · {device} · {pacing}
Hook shape it was meant to take: {hook_shape}
Measured hook faults: {hook_faults}

# Task
Return JSON:
{{
  "weakest_beat": <1-based index of the beat that hurts it most>,
  "scroll_risk": "the moment a viewer swipes away, and why",
  "verdict": "one blunt sentence",
  "notes": ["specific, actionable fix", ...]
}}
Between 2 and 4 notes."""

REVISE_SYSTEM = """You revise an ad script against a critic's notes.

Apply every note literally. Keep what works — do not rewrite lines the critic
did not flag. Preserve the beat schema exactly, including motion, camera and
delivery on every beat.

If you touch the hook, the rewrite must still be a finite clause of 4-14 words
containing I/we/you and exactly one CAPS word, and must not open on an
imperative, a negation, a demonstrative or the brand name. JSON only."""

REVISE_PROMPT = """# Current script
{script}

# Critic's verdict
{verdict}
Scroll risk: {scroll_risk}
Weakest beat: {weakest}

# Notes to apply
{notes}

# Task
Return the revised script in the SAME JSON shape:
{{"hook": "...", "beats": [{{"t":0.0,"say":"...","show":"...","shot":"...",
   "motion":"stack|punch|slide|pop|banner","camera":"push|pull|pan|punch|hold",
   "delivery":"[tag]"}}],
  "caption":"...", "hashtags":[...], "cta":"...",
  "why_this_works":"...", "grounded_in":[...]}}"""


async def plan(
    company: Company, product: str, lane_brief: str, corpus: CorpusAnalysis
) -> VideoPlan:
    data = await complete_json(
        PLAN_PROMPT.format(
            company=company.context(product),
            lane_brief=lane_brief,
            formats="; ".join(corpus.dominant_formats) or "unknown",
            hooks="; ".join(corpus.recurring_hooks) or "unknown",
            shared=corpus.what_top_performers_share or "unknown",
            whitespace=corpus.whitespace or "unknown",
            hook_shapes="\n".join(f"- {k}: {v}" for k, v in HOOK_SHAPES.items()),
        ),
        system=PLAN_SYSTEM,
        max_tokens=1200,
    )
    p = VideoPlan(
        structure=str(data.get("structure", "")),
        device=str(data.get("device", "")),
        beat_count=max(4, min(10, int(data.get("beat_count", 7) or 7))),
        target_seconds=max(12, min(40, int(data.get("target_seconds", 28) or 28))),
        pacing=str(data.get("pacing", "")),
        opening_move=str(data.get("opening_move", "")),
        turn_at=str(data.get("turn_at", "")),
        # An invented shape is worse than none: the writer would be handed a
        # constraint with no measured backing behind it.
        hook_shape=(lambda s: s if s in HOOK_SHAPES else "")(
            str(data.get("hook_shape", "")).strip()
        ),
        rationale=str(data.get("rationale", "")),
    )
    log.info("plan: %s · %s · %d beats / %ds · %s · hook=%s",
             p.structure, p.device, p.beat_count, p.target_seconds, p.pacing,
             p.hook_shape or "unconstrained")
    return p


def _script_block(remix: Remix) -> str:
    return "\n".join(
        f"  {i + 1}. [{b.motion or '-'}|{b.camera or '-'}|{b.delivery or '-'}] {b.say}"
        for i, b in enumerate(remix.beats)
    )


async def critique(remix: Remix, p: VideoPlan) -> Critique:
    from vira.remix import hook_faults

    faults = hook_faults(remix.hook)
    data = await complete_json(
        CRITIQUE_PROMPT.format(
            hook=remix.hook, beats=_script_block(remix), cta=remix.cta,
            structure=p.structure, device=p.device, pacing=p.pacing,
            hook_shape=p.hook_shape or "unconstrained",
            hook_faults="; ".join(faults) if faults else "none",
        ),
        system=CRITIQUE_SYSTEM,
        max_tokens=1200,
    )
    c = Critique(
        weakest_beat=int(data.get("weakest_beat", 0) or 0),
        verdict=str(data.get("verdict", "")),
        notes=[str(n) for n in data.get("notes", [])],
        scroll_risk=str(data.get("scroll_risk", "")),
    )
    log.info("critique: %s", c.verdict)
    return c


async def revise(remix: Remix, c: Critique, trends: list[Trend]) -> Remix:
    """Apply the critic's notes. Returns the original if the pass fails."""
    import json

    from vira.remix import parse_remix

    try:
        data = await complete_json(
            REVISE_PROMPT.format(
                script=json.dumps(remix.model_dump(mode="json"), indent=2),
                verdict=c.verdict, scroll_risk=c.scroll_risk,
                weakest=c.weakest_beat,
                notes="\n".join(f"- {n}" for n in c.notes),
            ),
            system=REVISE_SYSTEM,
            max_tokens=5000,
        )
        return parse_remix(data, trends)
    except Exception as exc:  # noqa: BLE001 - a failed revision keeps the original
        log.warning("revision failed, keeping original: %s", exc)
        return remix
