"""Stage 6 — the voice track is the master clock.

Borrowed wholesale from ideaplaces-docs/docs/active-projects/video-as-code:
synthesize the narration first, take the character-level timestamps back from
the same call, and compute every visual timing from them. Frame numbers are
never hand-authored.

The payoff is that a copy change re-times the whole video for free, and the same
pipeline localizes later at no extra engineering cost — a German line is ~30%
longer, its timestamps move, and every beat moves with it.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from vira.config import settings
from vira.models import Remix

log = logging.getLogger(__name__)

# The `with-timestamps` variant returns audio AND per-character alignment.
TTS_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
)


class VoiceError(RuntimeError):
    pass


async def synthesize(remix: Remix, out_dir: Path) -> tuple[Path, float]:
    """Render narration to mp3 and stamp start/end on every beat.

    Returns (mp3_path, total_seconds).
    """
    s = settings()
    if not (s.elevenlabs_api_key and s.elevenlabs_voice_id):
        raise VoiceError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID are not set")

    text = remix.narration()
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            TTS_URL.format(voice_id=s.elevenlabs_voice_id),
            headers={"xi-api-key": s.elevenlabs_api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
            },
        )
        if r.status_code >= 400:
            raise VoiceError(f"ElevenLabs [{r.status_code}]: {r.text[:300]}")
        payload = r.json()

    out_dir.mkdir(parents=True, exist_ok=True)
    mp3 = out_dir / "narration.mp3"
    mp3.write_bytes(base64.b64decode(payload["audio_base64"]))

    alignment = payload.get("alignment") or {}
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not starts:
        raise VoiceError("no character alignment returned — cannot time the beats")

    _stamp_beats(remix, text, starts, ends)
    total = float(ends[-1]) if ends else 0.0
    log.info("narration %.1fs across %d beats", total, len(remix.beats))
    return mp3, total


def _stamp_beats(
    remix: Remix, text: str, starts: list[float], ends: list[float]
) -> None:
    """Map each beat's spoken line onto character offsets in the joined narration.

    `narration()` joins beats with a single space, so offsets are recoverable by
    walking the same construction rather than fuzzy-matching.
    """
    cursor = 0
    for beat in remix.beats:
        line = beat.say.strip()
        if not line:
            continue
        idx = text.find(line, cursor)
        if idx == -1:  # punctuation normalisation by the API; fall back to order
            idx = cursor
        end_idx = min(idx + len(line) - 1, len(starts) - 1)
        beat.start_s = round(float(starts[min(idx, len(starts) - 1)]), 3)
        beat.end_s = round(float(ends[end_idx]), 3)
        beat.t = beat.start_s
        cursor = idx + len(line) + 1
