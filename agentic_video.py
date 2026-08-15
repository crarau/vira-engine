"""Generate one video with the agent crew instead of the straight-line pipeline.

    python agentic_video.py sunday-oats --product "Cocoa Hazelnut overnight oats" \
        --lane founder-story

The Director plans, delegates, inspects what actually came back, and fixes it.
Grounding and timing stay deterministic: the evidence gate runs after the loop,
in Python, and no agent can reach it.
"""

from __future__ import annotations

import argparse, asyncio, json, logging, shutil, sys, time
from pathlib import Path

from vira.agentic.crew import Production, direct
from vira.analyze import analyze_corpus
from vira.config import settings
from vira.lanes import get as get_lane
from vira.models import Company
from vira.provenance import Recorder
from vira.render import VIDEO_DIR, build_props, render, write_props
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.supa import Supa, get_company
from vira.verify import verify_all


async def main(slug: str, product: str, lane_name: str, do_render: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    t0 = time.monotonic()
    lane = get_lane(lane_name)
    supa = Supa()
    row = await get_company(supa, slug)
    if not row:
        print(f"no company {slug!r}"); return 1
    company = Company.from_row(row)
    print(f"{company.name} · {company.category} · lane={lane.name}\n")

    picked, rejected = await shortlist(supa, company, product)
    picked, dead = await verify_all(picked)
    print(f"corpus: {len(picked)} verified (rejected {sum(rejected.values())}, dead {len(dead)})")
    if not picked:
        print("nothing survived selection"); return 1
    corpus = await analyze_corpus(company, product, picked)

    root = Path("out") / slug
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = len([d for d in root.glob("v*-*") if d.is_dir()]) + 1
    out_dir = root / f"v{n:03d}-{stamp}-agentic" / lane.name
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = root / "latest"
    if latest.is_symlink() or latest.exists(): latest.unlink()
    latest.symlink_to(out_dir.parent.name)
    print(f"→ {out_dir}\n")

    public = VIDEO_DIR / "public"
    (public / "shots").mkdir(parents=True, exist_ok=True)

    async with Recorder(out_dir) as rec:
        rec.note("mode", "agentic")
        rec.note("lane", lane.name)
        prod = Production(company=company, product=product, lane=lane, corpus=corpus,
                          trends=picked, out_dir=out_dir, public_dir=public)
        print("=== DIRECTOR ===")
        closing = await direct(prod)
        print(f"\ndirector closed with: {closing[:200]}\n")

        print("\n=== crew trace ===")
        for line in prod.log:
            print(f"  {line}")
        print()

        if not prod.remix:
            print("no script produced"); return 1

        # Deterministic, after the loop, out of the Director's reach.
        score = await score_remix(company, product, prod.remix, picked)
        dispo, reason = disposition(score)
        print(f"score {score.overall}  evidence {score.evidence}  → {dispo}"
              + (f"  ({reason})" if reason else ""))

        rec.note("crew_log", prod.log)
        rec.note("image_calls", prod.image_calls)
        rec.finish(company=company, product=product, remix=prod.remix, score=score,
                   shots=prod.shots, sources=picked, voice_id=lane.voice_id,
                   settings_snapshot={"mode": "agentic", "model": settings().agent_model})

    if not do_render:
        print(f"\n{time.monotonic()-t0:.0f}s (no render)"); return 0
    if not prod.mp3:
        print("no audio — cannot render"); return 1
    have = sum(1 for sh in prod.shots if sh.get("file"))
    if have == 0:
        # The Director never produced frames. Rendering would 404 on every
        # image and fail after a minute of retries; say so instead.
        print("no imagery — the Director never called make_imagery. Not rendering.")
        return 1

    audio_name = f"narration-{lane.name}.mp3"
    shutil.copy(prod.mp3, public / audio_name)
    props = build_props(company, product, prod.remix, audio_path=public / audio_name,
                        duration_s=prod.duration + 2.4, shots=prod.shots)
    write_props(props, out_dir)
    mp4 = out_dir / f"{slug}-{lane.name}.mp4"
    print("rendering…")
    render(out_dir / "props.json", mp4, concurrency=6)
    print(f"\nDONE  {mp4}  ({mp4.stat().st_size/1_000_000:.1f} MB)  in {time.monotonic()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("slug"); p.add_argument("--product", required=True)
    p.add_argument("--lane", default="founder-story")
    p.add_argument("--no-video", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.slug, a.product, a.lane, not a.no_video)))
