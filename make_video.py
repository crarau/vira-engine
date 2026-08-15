"""Take a saved remix JSON all the way to an mp4.

    python make_video.py out/eli.json

Stages 6 and 7 from SPEC.md: narrate with ElevenLabs (character timestamps come
back in the same call), map those timestamps onto beat frames, then hand the
props to Remotion. No frame number is authored by hand anywhere in here.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from vira.config import settings
from vira.models import Company, Remix
from vira.render import VIDEO_DIR, build_props, render, write_props
from vira.stock import fetch_shots
from vira.voice import synthesize

OUT = Path("out")


async def main(path: str) -> int:
    data = json.loads(Path(path).read_text())
    company = Company(**data["company"])
    remix = Remix(**data["remix"])
    product = data.get("product", company.name)

    print(f"narrating {len(remix.beats)} beats "
          f"({len(remix.narration().split())} words)…")
    mp3, duration = await synthesize(remix, OUT)
    print(f"  audio: {mp3}  ({duration:.1f}s)")
    for b in remix.beats:
        print(f"    {b.start_s:>6.2f}s → {b.end_s:>6.2f}s  {b.say[:56]}")

    # Remotion resolves assets through staticFile(), so audio and images have to
    # live in the composition's public/ directory.
    public = VIDEO_DIR / "public"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy(mp3, public / "narration.mp3")

    print("finding stock footage…")
    shots = await fetch_shots(company, product, remix, public / "shots")
    found = sum(1 for s in shots if s.get("file"))
    print(f"  {found}/{len(shots)} beats have imagery")
    for s in shots:
        if s.get("file"):
            print(f"    {s['file']}  {s['query']!r}  ({s['credit']})")

    props = build_props(
        company, product, remix, audio_path=mp3, duration_s=duration + 2.4, shots=shots
    )
    props_path = write_props(props, OUT)
    print(f"  props: {props_path}  ({props['durationInFrames']} frames @ {props['fps']}fps)")

    out_file = OUT / f"{company.slug}.mp4"
    print("rendering…")
    render(props_path, out_file)
    size = out_file.stat().st_size / 1_000_000
    print(f"\nDONE  {out_file}  ({size:.1f} MB, "
          f"{props['durationInFrames'] / props['fps']:.1f}s, "
          f"{settings().width}x{settings().height})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "out/eli.json")))
