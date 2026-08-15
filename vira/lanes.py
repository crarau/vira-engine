"""Creative lanes — one coherent identity per variant.

Five ads that differ only in wording are five versions of the same ad. A
founder confession and a hard-sell demo should not share a voice, a colour
palette, or a delivery. So a lane owns all three:

    copy direction  → what the script does
    voice + tags    → who says it and how
    look            → what it looks like

That is what makes a human ranking meaningful. A judge picking "founder-story"
over "contrarian" is choosing a whole creative direction, not a turn of phrase.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lane:
    name: str
    brief: str
    voice_id: str
    voice_note: str
    opening: str
    closing: str
    middle: list[str] = field(default_factory=list)
    look: str = ""


LANES: list[Lane] = [
    Lane(
        name="problem-first",
        brief=(
            "Open on the FRUSTRATION the product removes. Name the pain in the "
            "first three words. The product does not appear until the midpoint."
        ),
        voice_id="iP95p4xoKVk53GoZ742B",  # Chris — Charming, Down-to-Earth
        voice_note="Chris · down-to-earth, wry, sounds like a friend complaining",
        opening="[tired]",
        closing="[confident]",
        middle=["[annoyed]", "[sighs]", "[deadpan]", "[warmer]"],
        look=(
            "Cool, slightly drab morning light. Cluttered, lived-in interiors. "
            "Handheld framing, mild motion blur, muted desaturated palette that "
            "warms up only in the final frames."
        ),
    ),
    Lane(
        name="demo-first",
        brief=(
            "Open mid-demonstration, product already in hand and in use. No setup, "
            "no context. Show the thing working before you explain anything."
        ),
        voice_id="TX3LPaxmHKxFdv7VOQHJ",  # Liam — Energetic, Social Media Creator
        voice_note="Liam · high-energy creator, pitchman cadence",
        opening="[excited]",
        closing="[shouting]",
        middle=["[confident]", "[excited]", "[emphatic]", "[quickly]"],
        look=(
            "Bright, crisp, high-key. Clean surfaces, saturated colour, product "
            "hero framing with strong specular highlights. Punchy contrast, "
            "everything in sharp focus."
        ),
    ),
    Lane(
        name="founder-story",
        brief=(
            "First person, founder voice. Why this exists, what was broken, what "
            "you changed. Intimate and unpolished, shot like a confession to camera."
        ),
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # George — Warm, Captivating Storyteller
        voice_note="George · warm storyteller, unhurried, confiding",
        opening="[softly]",
        closing="[sincere]",
        middle=["[thoughtful]", "[quietly]", "[warm]", "[reflective]"],
        look=(
            "Warm golden low light, deep shadow, very shallow depth of field. One "
            "person, often partially out of frame. Grainy, intimate, close. "
            "Looks like it was shot at dusk by someone who lives there."
        ),
    ),
    Lane(
        name="social-proof",
        brief=(
            "Lead with other people's reactions and results. Rapid, specific, "
            "quotable. The brand speaks last and briefly."
        ),
        voice_id="FGY2WhTYpPnrIDTdsKH5",  # Laura — Enthusiast, Quirky Attitude
        voice_note="Laura · quirky enthusiast, gossipy, fast",
        opening="[excited]",
        closing="[laughs] [excited]",
        middle=["[amused]", "[surprised]", "[excited]", "[conspiratorial]"],
        look=(
            "Candid snapshot energy, direct on-camera flash, slightly overexposed. "
            "Multiple people, hands, phones, social settings. Busy backgrounds, "
            "high saturation, imperfect framing."
        ),
    ),
    Lane(
        name="contrarian",
        brief=(
            "Open by disagreeing with the accepted wisdom in this category. State "
            "the popular advice, reject it, then prove the rejection with the product."
        ),
        voice_id="pNInz6obpgDQGcFmaJgB",  # Adam — Dominant, Firm
        voice_note="Adam · firm, declarative, dares you to disagree",
        opening="[serious]",
        closing="[emphatic]",
        middle=["[confident]", "[dismissive]", "[serious]", "[pointed]"],
        look=(
            "High contrast, stark, dramatic single-source light with hard shadow. "
            "Minimal, almost empty frames. Near-monochrome with one accent colour. "
            "Bold, graphic, uncomfortable negative space."
        ),
    ),
]

BY_NAME = {lane.name: lane for lane in LANES}


def get(name: str) -> Lane:
    return BY_NAME[name]
