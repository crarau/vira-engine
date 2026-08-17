"""The claim gate — cut what cannot be supported, keep the film.

V1 scored a whole video on an `evidence` dimension and dropped the entire thing
below a floor. That is right for a judged contest and wrong for a product: a user
who waited a minute gets nothing, and the one sentence that caused it is never
named. Worse, the sources were scraped TikToks, which cannot possibly know
whether someone's oats contain 12g of protein — so the gate rejected everything
it was ever shown (docs/V2-SPEC.md §2).

Here the unit is the **sentence**, the source is **the user's own pages**, and the
consequence is a **cut** rather than a rejection.

Three properties that make this safe to trust:

**It runs in Python.** No model can call it, read its threshold, or argue with it.
A fluent model asked to check its own work will approve it — the prototype proved
that repeatedly.

**Cutting is free.** Timing comes from ElevenLabs character timestamps, so
removing a sentence and re-deriving frames costs one TTS call, not a re-plan.
This is the payoff for the "voice track is the master clock" decision.

**Only checkable assertions are gated.** "Mornings are hard" is subjective and
passes untouched. "12g of protein" and "cheaper than Huel" do not. Gating opinion
would strip every film to nothing and teach users the tool is broken.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from vira.models import Beat, Remix
from vira.reader import Fact

log = logging.getLogger(__name__)

# --- what counts as a claim needing a source -----------------------------

FACTUAL = "factual"
COMPARATIVE = "comparative"
SUBJECTIVE = "subjective"
PROMOTIONAL = "promotional"
ANECDOTE = "anecdote"

# Numbers, money, units, percentages. Spoken scripts spell numbers out, so the
# word forms matter as much as the digits — "twelve grams of protein" is the same
# claim as "12g of protein" and the prototype's digit-only check missed it.
_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand|dozen|half"
)

_HAS_QUANTITY = re.compile(
    rf"(\d|[$£€]|\b(?:{_NUMBER_WORDS})\b)", re.IGNORECASE
)

# A comparative asserts a relationship to something outside the product, which
# needs a source even when no number appears.
_COMPARATIVE = re.compile(
    r"\b("
    r"more|less|fewer|better|worse|faster|slower|cheaper|stronger|higher|lower|"
    r"best|worst|highest|lowest|cheapest|fastest|strongest|first|only|most|"
    r"outperforms?|beats?|leading"
    r")\b",
    re.IGNORECASE,
)

# Regulated or evidence-implying language. These are cut hard when unsupported,
# because they are the ones that draw a letter rather than a bad review.
_REGULATED = re.compile(
    r"\b("
    r"clinically|scientifically|medically|doctor[- ]recommended|FDA|proven|"
    r"guaranteed|certified|cures?|treats?|prevents?|diagnos\w+|"
    r"studies? show|research shows"
    r")\b",
    re.IGNORECASE,
)

# First-person experience. "I skipped breakfast for two YEARS" contains a number
# and would otherwise classify as FACTUAL — so the gate would CUT it, and
# `first-person-admission` is a hook shape the writer is REQUIRED to produce
# (director.HOOK_SHAPES). A gate that deletes the opening line it just asked for
# is worse than no gate.
#
# The narrowing that keeps this safe: the quantities must all be durations. "I
# spent two years on this" is autobiography and no page can confirm it. "I packed
# 50g of protein in" asserts a product attribute wearing a first-person hat, and
# is still gated.
_FIRST_PERSON_PAST = re.compile(
    r"\bI\s+(?:\w+ed|was|were|had|did|got|went|gave|quit|stopped|spent|skipped|"
    r"tried|used|thought|believed|kept|lost|found|made|took|left|ate|drank)\b",
    re.IGNORECASE,
)

_TIME_UNIT = re.compile(
    r"\b(seconds?|minutes?|hours?|days?|weeks?|months?|years?|decades?|"
    r"mornings?|nights?|times?)\b",
    re.IGNORECASE,
)

# Product-attribute units. Their presence means a claim is about the THING, not
# about the speaker's history, whoever the grammatical subject is.
_ATTRIBUTE_UNIT = re.compile(
    r"(\b(g|mg|kg|grams?|ml|l|litres?|liters?|oz|ounces?|lbs?|pounds?|calories|"
    r"cals?|percent|servings?|jars?|packs?|boxes|bottles)\b|[$£€%])",
    re.IGNORECASE,
)

# Marketing intensifiers that assert nothing checkable. Present in almost every
# generated script; gating them would empty the film.
_PUFFERY = re.compile(
    r"\b(amazing|incredible|delicious|beautiful|lovely|gorgeous|perfect|"
    r"favourite|favorite|obsessed|love|hate|feels?|think|honestly)\b",
    re.IGNORECASE,
)


@dataclass
class Claim:
    """One assertion in the script, and what happened to it."""

    text: str                       # the sentence, as spoken
    beat_index: int
    kind: str
    fact: Fact | None = None        # the supporting sentence, when found
    verdict: str = "pending"        # kept | cut
    reason: str = ""                # why it was cut, in words a user can act on

    @property
    def needs_source(self) -> bool:
        return self.kind in (FACTUAL, COMPARATIVE)


@dataclass
class GateResult:
    remix: Remix                    # the script AFTER cuts
    claims: list[Claim] = field(default_factory=list)
    failed: bool = False            # too little left to be a film
    failure_reason: str = ""

    @property
    def kept(self) -> list[Claim]:
        return [c for c in self.claims if c.verdict == "kept"]

    @property
    def cut(self) -> list[Claim]:
        return [c for c in self.claims if c.verdict == "cut"]


# --- classification -------------------------------------------------------


def classify(sentence: str) -> str:
    """What kind of assertion is this?

    Order matters. Regulated language is promotional-and-dangerous and is checked
    first, because "clinically proven to taste amazing" contains puffery and must
    still be gated. Puffery is checked last for the same reason.
    """
    s = sentence.strip()
    if not s:
        return SUBJECTIVE
    if _REGULATED.search(s):
        return FACTUAL
    # Checked before COMPARATIVE and FACTUAL, but AFTER regulated language — "I
    # was clinically diagnosed" is not something we let through as anecdote.
    if (
        _FIRST_PERSON_PAST.search(s)
        and not _ATTRIBUTE_UNIT.search(s)
        and (_TIME_UNIT.search(s) or not _HAS_QUANTITY.search(s))
    ):
        return ANECDOTE
    if _COMPARATIVE.search(s):
        return COMPARATIVE
    if _HAS_QUANTITY.search(s):
        return FACTUAL
    if _PUFFERY.search(s):
        return SUBJECTIVE
    return PROMOTIONAL


# --- support checking -----------------------------------------------------

# Spoken copy paraphrases its source; it never quotes it. So substring matching
# is useless and the check has to tolerate rewording while still refusing an
# invented number. Two independent tests, both must pass:
#
#   1. every quantity in the claim appears in the fact (normalised across word
#      and digit forms), and
#   2. the claim and fact overlap enough lexically to be about the same thing.
#
# Test 1 alone would accept "twelve grams of caffeine" against "12g of protein".
# Test 2 alone would accept "fifty grams of protein" against "12g of protein".

_WORD_TO_DIGIT = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100", "thousand": "1000", "dozen": "12",
}

_STOP = {
    "a", "an", "and", "the", "of", "in", "on", "to", "for", "is", "are", "was",
    "it", "its", "this", "that", "with", "you", "your", "we", "our", "i", "my",
    "at", "by", "from", "as", "be", "been", "has", "have", "had", "per", "each",
    "every", "so", "but", "or", "not", "no", "just", "only", "all",
}


def quantities(text: str) -> set[str]:
    """Every number in the text, normalised to digits.

    "three twenty-five" in speech is "$3.25" on a page, so hyphenated pairs are
    joined as well as taken separately — a claim is supported if EITHER reading
    matches, since we cannot know which the writer meant.
    """
    out: set[str] = set()
    lowered = text.lower()

    for m in re.finditer(r"\d+(?:[.,]\d+)?", lowered):
        out.add(m.group(0).replace(",", ""))

    words = re.findall(r"[a-z]+", lowered)
    for w in words:
        if w in _WORD_TO_DIGIT:
            out.add(_WORD_TO_DIGIT[w])

    # "twenty-five" -> 25, "three twenty-five" -> 325 as well as {3, 20, 5}
    for a, b in re.findall(r"([a-z]+)-([a-z]+)", lowered):
        if a in _WORD_TO_DIGIT and b in _WORD_TO_DIGIT:
            out.add(str(int(_WORD_TO_DIGIT[a]) + int(_WORD_TO_DIGIT[b])))

    return out


def _content_words(text: str) -> set[str]:
    """Subject words only.

    Number words and unit words are excluded because `quantities()` already
    verified them, and counting them again double-penalises a correct claim:
    "twelve grams of protein" against "contains 12g of plant protein" scored
    0.33 — a miss on `twelve` and `grams` — because "12g" tokenises to "g".
    Stripping both leaves {protein} vs {jar, contains, plant, protein} and the
    claim resolves. What remains measures whether the two sentences are about
    the same THING, which is the only job left for this test.
    """
    return {
        w for w in re.findall(r"[a-z]+", text.lower())
        if w not in _STOP
        and len(w) > 2
        and w not in _WORD_TO_DIGIT
        and not _ATTRIBUTE_UNIT.fullmatch(w)
        and not _TIME_UNIT.fullmatch(w)
    }


def supports(claim: str, fact_text: str, *, min_overlap: float = 0.34) -> bool:
    """Does `fact_text` support `claim`?

    Deliberately conservative: an unsupported claim that slips through is a false
    statement in a published ad, while a supported claim wrongly cut is a
    sentence the user can add back. The costs are not symmetric, so the check
    leans toward cutting.
    """
    cq, fq = quantities(claim), quantities(fact_text)
    if cq and not cq <= fq:
        # A number the source does not contain. This is the invented-fact case
        # and there is no overlap score high enough to excuse it.
        return False

    cw, fw = _content_words(claim), _content_words(fact_text)
    if not cw:
        return False

    overlap = len(cw & fw) / len(cw)
    if overlap >= min_overlap:
        return True

    # Fall back to sequence similarity for heavy paraphrase ("a kitchen smaller
    # than your bedroom" vs "a 400 square foot kitchen") where shared vocabulary
    # is thin but the subject is plainly the same.
    return SequenceMatcher(None, claim.lower(), fact_text.lower()).ratio() >= 0.55


def find_support(claim: str, facts: list[Fact]) -> Fact | None:
    """The best supporting fact, or None. Prefers the most quantity overlap so a
    recipe cites the most specific source available."""
    best: tuple[int, Fact] | None = None
    cq = quantities(claim)
    for f in facts:
        if not supports(claim, f.text):
            continue
        rank = len(cq & quantities(f.text))
        if best is None or rank > best[0]:
            best = (rank, f)
    return best[1] if best else None


# --- the gate -------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# Below this a film is not a film. Two beats is the floor the director already
# treats as minimum viable, so cutting past it fails the generation rather than
# shipping a fragment.
MIN_BEATS = 2


def _reason_for(kind: str, sentence: str) -> str:
    """Written for the user, not for a log. Every reason names what would fix
    it, because a cut the user cannot act on is just a mystery."""
    if _REGULATED.search(sentence):
        return (
            "This implies clinical or scientific backing and none of your pages "
            "mention a study. Link the study and I will use it."
        )
    if kind == COMPARATIVE:
        return (
            "This compares you to something else, and your pages do not make "
            "that comparison. Add a page with the comparison, or drop the claim."
        )
    return (
        "Nothing on the pages you gave me supports this. Add a page that states "
        "it, or let it go."
    )


def gate(remix: Remix, facts: list[Fact]) -> GateResult:
    """Check every claim in the script. Cut what cannot be supported.

    Returns a NEW Remix — the input is left alone so a caller can show the
    before-and-after, which is the whole point of the cut panel.
    """
    claims: list[Claim] = []
    new_beats: list[Beat] = []

    for i, beat in enumerate(remix.beats):
        keep: list[str] = []

        for sentence in (s.strip() for s in _SENTENCE.split(beat.say) if s.strip()):
            kind = classify(sentence)
            claim = Claim(text=sentence, beat_index=i, kind=kind)

            if not claim.needs_source:
                claim.verdict = "kept"
                claims.append(claim)
                keep.append(sentence)
                continue

            fact = find_support(sentence, facts)
            if fact is None:
                claim.verdict = "cut"
                claim.reason = _reason_for(kind, sentence)
                claims.append(claim)
                log.info("cut claim in beat %d: %s", i + 1, sentence)
                continue

            claim.fact = fact
            claim.verdict = "kept"
            claims.append(claim)
            keep.append(sentence)

        # A beat whose every sentence was cut disappears. Keeping it with empty
        # `say` would render a silent shot with a caption track of nothing —
        # and the voice stage would produce timings for a beat with no words,
        # which is how the prototype got blank frames.
        if keep:
            new_beats.append(beat.model_copy(update={"say": " ".join(keep)}))

    result = GateResult(
        remix=remix.model_copy(update={"beats": new_beats}),
        claims=claims,
    )

    if len(new_beats) < MIN_BEATS:
        result.failed = True
        result.failure_reason = (
            f"only {len(new_beats)} beat(s) survived the claim check — the pages "
            "you gave me do not support enough of what this ad needs to say"
        )
        log.warning("gate failed: %s", result.failure_reason)

    log.info(
        "claim gate: %d kept, %d cut, %d/%d beats survive",
        len(result.kept), len(result.cut), len(new_beats), len(remix.beats),
    )
    return result
