"""Stage 6 — the voice track is the master clock, and it has to have a pulse.

Two jobs:

1. **Performance.** A flat read makes a great script sound like a subtitle.
   `eleven_v3` accepts inline audio tags — `[excited]`, `[shouting]`,
   `[whispers]` — which are performance direction, not spoken words. The hook
   gets attacked, the middle breathes, the CTA gets shouted. That is the whole
   Billy Mays trick: dynamic range, not volume.

2. **Timing.** Character timestamps come back in the same call, and every frame
   offset downstream is computed from them. Nothing is hand-timed.

The subtlety: audio tags occupy characters in the returned alignment even though
they are never spoken. So word timings are read off the API's own character
array — skipping bracketed spans — rather than off our source text. Matching our
text against the alignment would silently shift every word by the length of the
tags.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx

from vira.config import settings
from vira.models import Remix, Word

log = logging.getLogger(__name__)

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"

# Performance direction per beat position. The shape matters more than the
# individual tags: hit hard, pull back, build, then hit hardest at the CTA.
OPENING = "[excited]"
CLOSING = "[shouting]"
MIDDLE = ["[confident]", "[curious]", "[serious]", "[excited]", "[confident]"]

_TAG = re.compile(r"\[[^\]]*\]")


class VoiceError(RuntimeError):
    pass


def direct(remix: Remix, lane=None) -> str:
    """Insert performance tags. First beat attacks, last beat closes hard.

    A lane supplies its own palette so a founder confession is not delivered in
    the same register as a hard-sell demo.
    """
    opening = lane.opening if lane else OPENING
    closing = lane.closing if lane else CLOSING
    middle = (lane.middle if lane and lane.middle else MIDDLE)

    # Position is counted over the beats that are actually spoken. Counting over
    # every beat means a blank first or last beat silently steals the opening or
    # closing tag, and the ad opens flat or ends without the CTA being hit.
    lines = [b for b in remix.beats if b.say.strip()]
    n = len(lines)
    parts: list[str] = []
    for i, beat in enumerate(lines):
        # The director's own choice wins; the lane palette is the fallback.
        if beat.delivery:
            tag = beat.delivery
        elif i == 0:
            tag = opening
        elif i == n - 1:
            tag = closing
        else:
            tag = middle[(i - 1) % len(middle)]
        parts.append(f"{tag} {beat.say.strip()}")
    return " ".join(parts)


async def synthesize(remix: Remix, out_dir: Path, lane=None) -> tuple[Path, float]:
    """Render narration to mp3 and stamp real timings on every beat and word."""
    s = settings()
    voice_id = (lane.voice_id if lane else None) or s.elevenlabs_voice_id
    if not (s.elevenlabs_api_key and voice_id):
        raise VoiceError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID are not set")

    text = direct(remix, lane) if s.voice_tags else remix.narration()

    body: dict = {"text": text, "model_id": s.elevenlabs_model}
    if s.elevenlabs_model == "eleven_v3":
        # v3 reads stability as a creativity dial, not a consistency one.
        # 0.0 = "Creative": it actually acts on the performance tags. Measured
        # 25% more dynamic range than 1.0 on the same line — which is the whole
        # difference between a read and a performance.
        body["voice_settings"] = {"stability": s.voice_stability_v3}
    elif s.elevenlabs_model == "eleven_multilingual_v2":
        # Only v2 exposes these. Low stability = wide emotional range, which is
        # what stops the read sounding like a screen reader.
        body["voice_settings"] = {
            "stability": s.voice_stability,
            "similarity_boost": s.voice_similarity,
            "style": s.voice_style,
            "use_speaker_boost": True,
        }

    async with httpx.AsyncClient(timeout=240) as c:
        r = await c.post(
            TTS_URL.format(voice_id=voice_id),
            headers={"xi-api-key": s.elevenlabs_api_key, "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code >= 400:
            raise VoiceError(f"ElevenLabs [{r.status_code}]: {r.text[:300]}")
        payload = r.json()

    out_dir.mkdir(parents=True, exist_ok=True)
    mp3 = out_dir / "narration.mp3"
    mp3.write_bytes(base64.b64decode(payload["audio_base64"]))

    align = payload.get("alignment") or {}
    chars = align.get("characters") or []
    starts = align.get("character_start_times_seconds") or []
    ends = align.get("character_end_times_seconds") or []
    if not chars or not starts:
        raise VoiceError("no character alignment returned — cannot time the beats")

    words = _words_from_alignment(chars, starts, ends)
    _assign(remix, words)

    total = float(ends[-1]) if ends else 0.0
    log.info("narration %.1fs · %d words · %s · %s", total, len(words),
             s.elevenlabs_model, (lane.voice_note if lane else voice_id))
    return mp3, total


def _words_from_alignment(
    chars: list[str], starts: list[float], ends: list[float]
) -> list[Word]:
    """Walk the API's own character array, skipping bracketed performance tags."""
    words: list[Word] = []
    buf: list[str] = []
    w_start = 0.0
    in_tag = False

    for i, ch in enumerate(chars):
        if ch == "[":
            in_tag = True
            continue
        if ch == "]":
            in_tag = False
            continue
        if in_tag:
            continue

        if ch.isspace():
            if buf:
                words.append(Word(w="".join(buf), start=round(w_start, 3),
                                  end=round(float(ends[i - 1]) if i else w_start, 3)))
                buf = []
            continue

        if not buf:
            w_start = float(starts[i])
        buf.append(ch)

    if buf:
        words.append(Word(w="".join(buf), start=round(w_start, 3),
                          end=round(float(ends[-1]), 3)))
    return words


def _assign(remix: Remix, words: list[Word]) -> None:
    """Hand the timed words back to the beats that produced them, in order.

    Matching by position rather than by string: the synthesiser normalises
    punctuation, so `"don't"` in our text can come back as `"don’t"`, and a
    string match would silently drop the word and shift the rest.
    """
    cursor = 0
    for beat in remix.beats:
        n = len([t for t in beat.say.strip().split(" ") if t])
        chunk = words[cursor : cursor + n]
        cursor += n
        if not chunk:
            continue
        beat.words = chunk
        beat.start_s = chunk[0].start
        beat.end_s = chunk[-1].end
        beat.t = beat.start_s
