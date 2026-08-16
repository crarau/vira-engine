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


def _hook_shape(plan) -> str:
    """Spell the director's chosen shape out in full for the writer.

    The plan carries a key; the writer needs the rule behind it. Importing here
    rather than at module scope because director imports remix.parse_remix.
    """
    from vira.director import HOOK_SHAPES

    key = getattr(plan, "hook_shape", "") or ""
    if key in HOOK_SHAPES:
        return f"{key} — {HOOK_SHAPES[key]}"
    return "director's choice, but every rule in HOOK GRAMMAR still binds"

SYSTEM = """You are a short-form ad director. You write ads a two-person brand \
can shoot on a phone today.

Rules:
- Borrow a MECHANISM from the reference videos, never their surface. If a video \
works because it withholds the result until the midpoint, steal that structure, \
not its subject.
- Every beat is filmable with a phone, the product, and one person. No studios, \
no actors, no drone shots.
- Spoken lines are for saying out loud. Short. Contractions. No brochure copy.
- Hit the target length the director set. Budget 2.2 words per second — measured
  over 40 tagged renders, median 2.19, never above 2.3. The old 2.6 figure
  overshot every film by about 15%, because performance tags spend real time.
- grounded_in must list the trend keys you actually borrowed from.

# HOOK GRAMMAR — measured, not stylistic. See docs/HOOK-CRAFT.md.
Derived from 2,669 English TikToks with >=10k views, ranked by engagement rate.
These are hard constraints on the `hook`, which is also the first beat's `say`.

REQUIRED
1. The hook is a FINITE CLAUSE — it has a subject and a tensed verb. A label is
   not a hook. "Ten seconds. That's it." is a label. "I gave it ten seconds."
   is a hook. Verbless fragments are 56% of the worst-performing hooks and 44%
   of the best.
2. A PERSON appears in it: I, we, my, our, or you. Hooks carrying none of these
   run 7% below the corpus median and the effect is one of the most reliable
   measured. Opening ON "I" or "we" is the single strongest opening class.
3. 4 to 14 words. Median winner is 9. Do NOT chase brevity for its own sake —
   under-6-word hooks are over-represented at the BOTTOM, not the top.
4. Contractions: don't, I've, it's, you'll. Present in 21% of top hooks, 14% of
   bottom.
5. Exactly ONE word in CAPS, on the word that carries the stress. Not the brand,
   not the whole line. An acronym that is always capitalised — SPF, UV, SKU —
   does not count as your CAPS word; pick a real one as well. This is both the
   strongest engagement signal in the corpus and the only reliable way to move
   the synthesiser off a monotone.
6. Name the product or brand in the first sentence when it is the SUBJECT doing
   something. Hooks that name a brand run 28% above median. A brand used as a
   label ("Introducing X") is the opposite and is banned by rule 9.

BANNED OPENINGS — each measured below the corpus median
7. Imperative verb first: "Stop", "Try", "Watch", "Look", "Grab", "Meet".
8. Negation word first: "Stop", "Don't", "Never", "No", "Nobody". Negation
   INSIDE the clause is fine and often good; it is the first position that
   fails.
9. Demonstrative first: "This", "That", "These", "Here's", "It's". Worst
   over-representation in the bottom cohort of any opening class.
10. Brand or product name as the very first word, used as a label.
11. Throat-clearing: "Hey", "So", "Okay", "Guys", "Welcome", "Let me tell you".
12. Positive superlatives anywhere: best, amazing, incredible, ultimate,
    revolutionary, game-changing, must-have, perfect.
13. Trailing "..." on the hook. Over-represented at the bottom, and it produces
    no pause in the read.

SPECIFICITY — one anchor, one gap
14. Give the hook exactly ONE concrete anchor (a number, a named span of time,
    one named thing) and exactly ONE thing withheld that the viewer must keep
    watching to learn. Two anchors over-explains and measurably costs more than
    being too vague. Zero anchors gives nothing to hold. A number is not
    required — numerals show no measured advantage in this corpus.

# WRITING FOR THE MOUTH — every `say` line, not just the hook
The synthesiser reads the punctuation. Measured against the live API:
- An em dash — like this — and a word in CAPS widen the delivery. Both together
  add 30% more variation in word stress than plain prose.
- "..." does NOT produce a pause. Do not use it to mean one. Use a full stop.
- One beat somewhere in the middle should carry an aside or a self-correction,
  set off by em dashes. That is what separates a person talking from narration.

You also DIRECT the video, beat by beat. For each beat choose:
- motion: how the caption behaves. One of stack (words rise, calm),
  punch (one huge line, hard emphasis), slide (left bar wipe, listing a fact),
  pop (words snap in scattered, chaotic energy), banner (solid slab, a claim).
- camera: push (slow in, building), pull (out, revealing), pan (drifting),
  punch (fast in, a hit), hold (static, let it land).
- delivery: an ElevenLabs performance tag for the line, e.g. [excited],
  [whispers], [deadpan], [frustrated], [confident], [laughs], [serious]. Tags
  are what create pauses and phrasing — measured at more than double the pause
  variation of an untagged read. Non-verbal tags ([sighs], [exhales],
  [laughs], [scoffs]) widen it further; use one where the line earns it.

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
Hook shape — the grammatical form the first line MUST take: {hook_shape}

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
  "hook": "the first line, spoken. A finite clause of 4-14 words carrying I/we/you and exactly one CAPS word. Obeys the hook shape above and every rule in HOOK GRAMMAR.",
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
            hook_shape=_hook_shape(plan),
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

    for fault in hook_faults(remix.hook):
        log.warning("hook: %s — %r", fault, remix.hook)

    words = len(remix.narration().split())
    if words > 95:
        log.warning("narration is %d words — likely over 32s, consider a re-roll", words)
    return remix


# The banned openings from HOOK GRAMMAR, as data. A prompt rule nobody checks is
# a suggestion; this turns each one into a line in the log that a human can act
# on. Deliberately warn-only — a hook that breaks one rule and lands is still
# better than a re-roll, and the scorer is the gate, not this.
_BANNED_FIRST = {
    "imperative": {"stop", "try", "watch", "look", "grab", "meet", "get", "see",
                   "listen", "buy", "shop", "check", "swipe", "tap", "click",
                   "imagine", "picture", "forget", "introducing"},
    "negation": {"stop", "don't", "dont", "never", "no", "not", "nobody", "nothing"},
    "demonstrative": {"this", "that", "these", "those", "here", "here's", "heres",
                      "there", "there's", "it", "it's", "its"},
    "throat-clearing": {"hey", "so", "okay", "ok", "guys", "welcome", "hi", "hello",
                        "yo", "listen", "look", "alright", "well", "um"},
}
_SUPERLATIVES = {"best", "amazing", "incredible", "ultimate", "revolutionary",
                 "game-changing", "gamechanging", "must-have", "perfect",
                 "greatest", "insane", "unreal", "premium", "luxurious"}
_PERSON = {"i", "i'm", "im", "i've", "ive", "i'll", "my", "me", "mine",
           "we", "we're", "our", "us", "ours", "we've",
           "you", "your", "you're", "youre", "yours", "you'll"}
_FINITE = {"is", "are", "am", "was", "were", "'s", "'re", "'m", "isn't", "aren't",
           "wasn't", "weren't", "have", "has", "had", "'ve", "haven't", "hasn't",
           "do", "does", "did", "don't", "doesn't", "didn't", "can", "could",
           "will", "would", "should", "must", "might", "'ll", "'d", "can't",
           "won't", "wouldn't", "shouldn't",
           # Irregular pasts. Without these the -ed/-s inflection test calls
           # "I gave up on it" a fragment, which is the exact shape we want most.
           "gave", "got", "went", "took", "came", "saw", "said", "told", "found",
           "kept", "ran", "drank", "ate", "felt", "thought", "knew", "brought",
           "sent", "bought", "sold", "left", "held", "wore", "built", "broke",
           "wrote", "read", "paid", "spent", "lost", "won", "quit", "put", "made",
           "hid", "threw", "swore", "began", "chose", "grew", "stood", "sat"}


# A word after one of these is a noun, so its trailing -s is a plural, not a
# conjugation. Without this the fragment test passes anything with a plural in it.
_NOT_A_SUBJECT = {"a", "an", "the", "my", "your", "our", "their", "his", "her",
                  "its", "of", "some", "all", "these", "those", "many", "few",
                  "no", "every", "each", "both", "two", "three", "four", "five",
                  "six", "seven", "eight", "nine", "ten", "ten's", "thirty",
                  "twenty", "fifty", "hundred", "more", "other", "own", "same"}


def hook_faults(hook: str) -> list[str]:
    """Every HOOK GRAMMAR rule this hook breaks. Empty list means it conforms."""
    import re

    h = hook.strip()
    if not h:
        return ["empty"]
    toks = re.findall(r"[A-Za-z0-9'’\-]+", h)
    low = [t.lower().replace("’", "'") for t in toks]
    if not low:
        return ["no words"]

    faults: list[str] = []
    first = low[0]
    for label, banned in _BANNED_FIRST.items():
        if first in banned:
            faults.append(f"opens on a banned {label} ({first!r})")

    if not (4 <= len(low) <= 14):
        faults.append(f"{len(low)} words, outside 4-14")
    if not any(t in _PERSON for t in low):
        faults.append("no I/we/you — impersonal hooks under-perform")

    # A tensed verb, an inflected verb, or a contracted auxiliary. Crude, but it
    # separates "I gave it ten seconds" from "Ten seconds. That's it."
    #
    # The inflection test must not fire on a plural noun, or "Ten SECONDS of my
    # morning" reads as a clause and the rule this checker exists to enforce is
    # the one it fails to enforce. A determiner or a numeral in front means the
    # -s belongs to a noun.
    inflected = any(
        re.search(r"(ed|es|s)$", t) and len(t) > 3 and low[i - 1] not in _NOT_A_SUBJECT
        for i, t in enumerate(low) if i
    )
    if not (any(t in _FINITE for t in low) or "'" in h or inflected):
        faults.append("reads as a verbless fragment, not a clause")

    # SPF, UV, SKU — an acronym is spelled in caps whatever the stress is, so it
    # is not evidence of emphasis. Vowel-free short tokens are the cheap tell.
    caps = [t for t in toks
            if t.isupper() and len(t) > 1
            and not (len(t) <= 4 and not set(t) & set("AEIOU"))]
    # The measured bucket was "one or two words in caps" (index 110 vs 98 for
    # none); a wholly capitalised line indexes higher but that cohort is
    # wholesale spam, so it is capped rather than rewarded.
    if not caps:
        faults.append("no CAPS word — nothing marks the stress")
    elif len(caps) > 2:
        faults.append(f"{len(caps)} CAPS words, want 1 (2 tolerated)")

    if hit := _SUPERLATIVES & set(low):
        faults.append(f"positive superlative: {', '.join(sorted(hit))}")
    if h.endswith("...") or h.endswith("…"):
        faults.append("trailing ellipsis")
    return faults
