"""Stage 7, static — hand one beat to Remotion and get a PNG.

The mirror of `vira.render` for the static ad. Same seam (a props JSON file),
same rule (Python owns the data, Remotion owns the pixels), and the same reason
for it: the composition can be iterated in `npx remotion studio` without
re-running a single model call.

The one thing this module has to get right, and the only thing that is not a
straight copy of the video path, is **which frame to grab**.

`video/src/Captions.tsx` animates a word in on a spring. At frame 0 the spring
has produced nothing: opacity is 0.3, scale is as low as 0.5, and the word is
mid-slide. A still taken there is technically a successful render of a
half-drawn caption — the exact failure CLAUDE.md records under "a render can
succeed and be blank". So the timings are laid out here, the settled frame is
computed here, and the Remotion CLI is told to render that frame and no other.

Word timings for the video come from ElevenLabs. There is no narration on a
static ad, so there is nothing to derive them from — and rather than pretend,
this module lays out a plausible read at the same words-per-second the writer
prompt is built around, purely so the caption's own emphasis logic has something
real to work on. Nothing downstream treats these as measurements.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from vira.brief import WORDS_PER_SECOND
from vira.config import settings
from vira.models import Company, Remix
from vira.render import VIDEO_DIR

log = logging.getLogger(__name__)

# WORDS_PER_SECOND is the writer prompt's own reading speed, imported rather than
# restated. Used here to space the words plausibly, not to claim a duration — a
# still has none.

# How long the stressed word is held, relative to an ordinary one. It has to
# clear the 1.7x median that `emphasisIndex` in Captions.tsx tests for, or the
# still loses the accent colour and the underline that the video gives it.
STRESS_HOLD = 3.4

# Frames after a word's entry at which every spring in SPEC has settled. The
# stiffest runs 12 frames and the softest 16; 18 is past all of them and still
# inside the shortest hold.
SETTLE_FRAMES = 18

_CAPS = re.compile(r"^[A-Z][A-Z'’-]{2,}$")
_PUNCT = re.compile(r"^[.,;:\"'`…—-]+|[.,;:\"'`…]+$")


def stressed_index(words: list[str]) -> int:
    """Which word the poster is built around.

    The hook grammar requires exactly one word in CAPS, on the syllable that
    carries the stress — so on a well-formed hook this is not a heuristic, it is
    reading the writer's own mark. The longest-word fallback is for the hooks
    that break the rule.
    """
    for i, w in enumerate(words):
        if _CAPS.match(_PUNCT.sub("", w)):
            return i
    if not words:
        return -1
    return max(range(len(words)), key=lambda i: len(_PUNCT.sub("", words[i])))


def build_still_props(
    company: Company,
    remix: Remix,
    *,
    image: str | None,
    headline: str | None = None,
) -> dict:
    """Everything AdStill needs, with the settled frame already chosen.

    Returns props carrying `stillFrame`; `render_still` reads it back off the
    props rather than taking it as an argument, so the frame and the timings it
    was derived from cannot drift apart in a caller.
    """
    s = settings()
    line = (headline or remix.hook or (remix.beats[0].say if remix.beats else "")).strip()
    words = line.split()
    per_word = max(int(round(s.fps / WORDS_PER_SECOND)), 6)
    stressed = stressed_index(words)

    timed: list[dict] = []
    cursor = 0
    for i, w in enumerate(words):
        hold = int(per_word * STRESS_HOLD) if i == stressed else per_word
        timed.append({"w": w, "startFrame": cursor, "endFrame": cursor + hold})
        cursor += hold

    # Mid-hold on the stressed word: past every spring, well before the caption
    # band's own 4-frame exit fade.
    still_frame = (timed[stressed]["startFrame"] + SETTLE_FRAMES) if timed else 0
    total = max(cursor + s.fps, still_frame + s.fps)

    beat = remix.beats[0] if remix.beats else None
    return {
        "brand": company.name,
        "headline": line,
        "cta": remix.cta,
        "image": image,
        "fps": s.fps,
        "durationInFrames": total,
        "stillFrame": still_frame,
        "beat": {
            "say": line,
            "show": beat.show if beat else "",
            "shot": beat.shot if beat else "",
            # The writer's own caption call, honoured exactly as the video
            # honours it: "punch" lands in accent, "stack" rises in white.
            "motion": (beat.motion if beat and beat.motion else "punch"),
            "camera": beat.camera if beat else "",
            "startFrame": 0,
            "endFrame": total,
            "words": timed,
        },
    }


def write_still_props(props: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "still-props.json"
    path.write_text(json.dumps(props, indent=2))
    return path


def render_still(
    props_path: Path, out_file: Path, *, composition: str = "AdStill"
) -> Path:
    """Invoke `npx remotion still`. Requires `npm install` inside video/ first."""
    if shutil.which("npx") is None:
        raise RuntimeError("npx not found — install Node to render")
    if not (VIDEO_DIR / "node_modules").exists():
        raise RuntimeError(f"run `npm install` in {VIDEO_DIR} first")

    props = json.loads(Path(props_path).read_text())
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npx", "remotion", "still", composition, str(out_file.resolve()),
        f"--props={Path(props_path).resolve()}",
        # Stated rather than inferred from the extension, so a caller that asks
        # for .jpg cannot end up with a PNG carrying the wrong name.
        f"--image-format={'jpeg' if out_file.suffix.lower() in ('.jpg', '.jpeg') else 'png'}",
        # Not optional. Frame 0 is a caption that has not finished arriving.
        f"--frame={int(props.get('stillFrame', 0))}",
    ]
    log.info("rendering still: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=VIDEO_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"remotion still failed:\n{proc.stderr[-2000:]}")
    if not out_file.exists():
        raise RuntimeError(f"remotion still reported success but wrote no {out_file}")
    return out_file
