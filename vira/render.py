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
) -> dict:
    s = settings()
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
        "beats": [
            {
                "say": b.say,
                "show": b.show,
                "shot": b.shot,
                "startFrame": int((b.start_s if b.start_s is not None else b.t) * s.fps),
                "endFrame": int(
                    (b.end_s if b.end_s is not None else b.t + 3) * s.fps
                ),
            }
            for b in remix.beats
        ],
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
