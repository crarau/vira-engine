"""Stage 7 — hand the timed script to Remotion and get an mp4.

Python owns the data; Remotion owns the pixels. The seam between them is a props
JSON file, which means you can iterate on the composition in `npx remotion
studio` without re-running the Python pipeline.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from vira.config import settings
from vira.models import Company, Remix

log = logging.getLogger(__name__)

VIDEO_DIR = Path(__file__).resolve().parent.parent / "video"


def build_props(
    company: Company,
    product: str,
    remix: Remix,
    *,
    audio_path: Path | None,
    duration_s: float,
    shots: list[dict] | None = None,
) -> dict:
    """Everything the composition needs, with all timing already resolved to frames.

    Remotion does no timing maths of its own — frame numbers here come from
    ElevenLabs character timestamps, so a copy change re-times the video without
    anyone touching the composition.
    """
    s = settings()
    shots = shots or []

    beats = []
    for i, b in enumerate(remix.beats):
        start = b.start_s if b.start_s is not None else b.t
        end = b.end_s if b.end_s is not None else b.t + 3
        shot_meta = shots[i] if i < len(shots) else {}
        beats.append(
            {
                "say": b.say,
                "show": b.show,
                "shot": b.shot,
                "startFrame": int(start * s.fps),
                "endFrame": int(end * s.fps),
                "image": shot_meta.get("file"),
                "credit": shot_meta.get("credit"),
                # Word timings drive the karaoke highlight.
                "words": [
                    {
                        "w": w.w,
                        "startFrame": int(w.start * s.fps),
                        "endFrame": int(w.end * s.fps),
                    }
                    for w in b.words
                ],
            }
        )

    return {
        "brand": company.name,
        "product": product,
        "hook": remix.hook,
        "cta": remix.cta,
        "caption": remix.caption,
        "hashtags": remix.hashtags,
        "audioSrc": str(audio_path.resolve()) if audio_path else None,
        "durationInFrames": max(int(duration_s * s.fps), s.fps),
        "fps": s.fps,
        "beats": beats,
    }


def write_props(props: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "props.json"
    path.write_text(json.dumps(props, indent=2))
    return path


def render(props_path: Path, out_file: Path, *, composition: str = "AdVideo") -> Path:
    """Invoke the Remotion CLI. Requires `npm install` inside video/ first."""
    if shutil.which("npx") is None:
        raise RuntimeError("npx not found — install Node to render")
    if not (VIDEO_DIR / "node_modules").exists():
        raise RuntimeError(f"run `npm install` in {VIDEO_DIR} first")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npx", "remotion", "render", composition, str(out_file.resolve()),
        f"--props={props_path.resolve()}",
    ]
    log.info("rendering: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=VIDEO_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"remotion render failed:\n{proc.stderr[-2000:]}")
    return out_file
