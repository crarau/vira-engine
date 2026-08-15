"""Generate N distinct ad variants for one company, then render each to video.

    python variants.py sunday-oats --product "Cocoa Hazelnut overnight oats" -n 5

Why variants rather than one "best" ad: the Terac track wants real human input
that measurably improves the project. One ad gives a panel nothing to compare.
Five ads built from deliberately different angles gives them a ranking task,
and a ranking is a signal you can feed back into the scoring weights.

Each variant is forced down a different creative lane so the five are genuinely
different ads rather than five rewrites of the same one. They share a corpus and
a corpus analysis — only the angle changes — so a human ranking them is ranking
the *angle*, not noise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

# 11 cores here: 2 renders x 4 workers leaves headroom for the OS and the
# still-running image/TTS calls. Raise RENDER_PARALLEL on a bigger machine.
RENDER_PARALLEL = 2
RENDER_CONCURRENCY = 4

from vira.analyze import analyze_corpus
from vira.director import critique, plan as make_plan, revise
from vira.lanes import LANES
from vira.config import settings
from vira.models import Company, Remix
from vira.provenance import Recorder
from vira.remix import build_remix
from vira.render import VIDEO_DIR, build_props, render, write_props
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.shots import fetch_or_generate
from vira.supa import Supa, get_company
from vira.verify import verify_all
from vira.voice import synthesize

async def build_variant(
    company: Company, product: str, picked, corpus, lane, out_dir: Path
) -> tuple[str, Remix, object, tuple[str, str | None]]:
    """Build one variant, recording every prompt that produced it.

    The Recorder is what makes a variant tweakable later: RECIPE.md next to the
    video holds the verbatim system and user prompts, the corpus in scope, and
    the settings in force. Change a prompt there, re-run, get a different ad.
    """
    name, brief = lane.name, lane.brief
    steered = Company(**{**company.model_dump(),
                         "mission": f"{company.mission}\n\nCREATIVE DIRECTION FOR THIS AD: {brief}"})

    async with Recorder(out_dir / name) as rec:
        rec.note("lane", name)
        rec.note("lane_brief", brief)
        rec.note("voice", lane.voice_note)
        rec.note("look", lane.look)

        # plan the shape → write it → have a hostile viewer read it → revise
        vp = await make_plan(company, product, brief, corpus)
        rec.note("plan", vp.model_dump())
        remix = await build_remix(steered, product, picked, corpus, vp)

        crit = await critique(remix, vp)
        rec.note("critique", crit.model_dump())
        if crit.notes:
            remix = await revise(remix, crit, picked)

        score = await score_remix(company, product, remix, picked)
        s = settings()
        rec.finish(
            company=company, product=product, remix=remix, score=score, sources=picked,
            voice_id=s.elevenlabs_voice_id,
            settings_snapshot={
                "llm_model": s.llm_model,
                "max_age_days": s.max_age_days,
                "shortlist_size": s.shortlist_size,
                "max_per_format": s.max_per_format,
                "english_only": s.english_only,
                "surface_threshold": s.surface_threshold,
                "watchlist_threshold": s.watchlist_threshold,
                "evidence_floor": s.evidence_floor,
            },
        )
    return name, remix, score, disposition(score)


async def main(slug: str, product: str, n: int, render_video: bool,
               only: list[str] | None, stamp: str) -> int:
    supa = Supa()
    row = await get_company(supa, slug)
    if not row:
        print(f"no company with slug {slug!r}")
        return 1
    company = Company.from_row(row)
    print(f"{company.name} · {company.category}\n")

    picked, rejected = await shortlist(supa, company, product)
    picked, dead = await verify_all(picked)
    print(f"corpus: {len(picked)} verified sources "
          f"(rejected {sum(rejected.values())} at selection, {len(dead)} at verification)")
    if not picked:
        print("nothing survived selection")
        return 1

    corpus = await analyze_corpus(company, product, picked)
    print(f"whitespace: {corpus.whitespace}\n")

    # Versioned so every run is inspectable and nothing is overwritten.
    # `out/<slug>/latest` always points at the newest.
    root = Path("out") / slug
    root.mkdir(parents=True, exist_ok=True)
    existing = [d for d in root.glob("v*-*") if d.is_dir()]
    version = f"v{len(existing) + 1:03d}-{stamp}"
    out_dir = root / version
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(out_dir.name)
    print(f"→ {out_dir}  (also out/{slug}/latest)\n")

    lanes = LANES[:n] if not only else [l for l in LANES if l.name in only]
    results = await asyncio.gather(
        *(build_variant(company, product, picked, corpus, lane, out_dir) for lane in lanes),
        return_exceptions=True,
    )

    manifest = []

    for lane, res in zip(lanes, results):
        if isinstance(res, BaseException):
            print(f"  {lane.name:15} FAILED: {res}")
            continue
        name, remix, score, (dispo, reason) = res
        print(f"  {name:15} score {score.overall:<5} evidence {score.evidence:<4} → {dispo}"
              + (f"  ({reason})" if reason else ""))
        print(f"    \"{remix.hook}\"")
        payload = {
            "variant": name,
            "company": company.model_dump(mode="json"),
            "product": product,
            "remix": remix.model_dump(mode="json"),
            "score": score.model_dump(),
            "disposition": dispo,
            "drop_reason": reason,
            "sources": [t.model_dump(mode="json") for t in picked],
        }
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2))
        manifest.append({"variant": name, "json": str(path), "hook": remix.hook,
                         "score": score.overall, "disposition": dispo,
                         "voice": next(l.voice_note for l in LANES if l.name == name)})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest)} variants to {out_dir}/")

    if not render_video:
        return 0

    # --- rendering, parallelised -------------------------------------
    #
    # This used to be a sequential for-loop, which made five videos cost five
    # times one video. Three changes fix that:
    #
    #   1. Assets are namespaced per variant (shots/<variant>/, narration-<v>.mp3)
    #      so concurrent renders cannot read each other's files.
    #   2. Within a variant, TTS and image generation run concurrently — they
    #      both depend on the remix but not on each other.
    #   3. Renders run RENDER_PARALLEL at a time, each capped at RENDER_CONCURRENCY
    #      workers. Five renders each grabbing six workers would thrash 11 cores.
    public = VIDEO_DIR / "public"
    sem = asyncio.Semaphore(RENDER_PARALLEL)

    async def produce(entry: dict) -> None:
        name = entry["variant"]
        data = json.loads(Path(entry["json"]).read_text())
        remix = Remix(**data["remix"])
        vdir = out_dir / name
        try:
            # Voice and imagery are independent; overlap them.
            lane = next(l for l in LANES if l.name == name)
            mp3, shots = await asyncio.gather(
                synthesize(remix, vdir, lane),
                fetch_or_generate(company, product, remix,
                                  public / "shots" / name, lane.look),
            )
            mp3_path, duration = mp3

            # Namespace the audio into public/ and the image paths into props.
            audio_name = f"narration-{name}.mp3"
            shutil.copy(mp3_path, public / audio_name)
            for shot in shots:
                if shot.get("file"):
                    shot["file"] = f"{name}/{shot['file']}"

            props = build_props(company, product, remix, audio_path=public / audio_name,
                                duration_s=duration + 2.4, shots=shots)
            write_props(props, vdir)

            mp4 = out_dir / f"{slug}-{name}.mp4"
            async with sem:
                print(f"  rendering {name}…")
                await asyncio.to_thread(
                    render, vdir / "props.json", mp4, concurrency=RENDER_CONCURRENCY
                )
            entry["mp4"] = str(mp4)
            print(f"  done {name}  ({mp4.stat().st_size / 1_000_000:.1f} MB, {duration:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - one bad variant must not stop the rest
            print(f"  FAILED {name}: {exc}")
            entry["mp4"] = None

    t0 = time.monotonic()
    await asyncio.gather(*(produce(e) for e in manifest))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rendered = sum(1 for e in manifest if e.get("mp4"))
    print(f"\n{rendered}/{len(manifest)} videos in {time.monotonic() - t0:.0f}s → {out_dir}/")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("slug")
    p.add_argument("--product", required=True)
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--only", nargs="*", default=None,
                   help="render just these lanes, e.g. --only founder-story")
    a = p.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sys.exit(asyncio.run(main(a.slug, a.product, a.n, not a.no_video, a.only, stamp)))
