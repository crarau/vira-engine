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
from pathlib import Path

from vira.analyze import analyze_corpus
from vira.config import settings
from vira.models import Company, Remix
from vira.provenance import Recorder
from vira.remix import build_remix
from vira.render import VIDEO_DIR, build_props, render, write_props
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.stock import fetch_shots
from vira.supa import Supa, get_company
from vira.verify import verify_all
from vira.voice import synthesize

# Five distinct creative lanes. Each is appended to the remix brief so the model
# commits to one angle instead of averaging them into mush.
LANES: list[tuple[str, str]] = [
    ("problem-first",
     "Open on the FRUSTRATION the product removes. Name the pain in the first "
     "three words. The product does not appear until the midpoint."),
    ("demo-first",
     "Open mid-demonstration, product already in hand and in use. No setup, no "
     "context. Show the thing working before you explain anything."),
    ("founder-story",
     "First person, founder voice. Why this exists, what was broken, what you "
     "changed. Intimate and unpolished, shot like a confession to camera."),
    ("social-proof",
     "Lead with other people's reactions and results. Rapid, specific, "
     "quotable. The brand speaks last and briefly."),
    ("contrarian",
     "Open by disagreeing with the accepted wisdom in this category. State the "
     "popular advice, reject it, then prove the rejection with the product."),
]


async def build_variant(
    company: Company, product: str, picked, corpus, lane: tuple[str, str], out_dir: Path
) -> tuple[str, Remix, object, tuple[str, str | None]]:
    """Build one variant, recording every prompt that produced it.

    The Recorder is what makes a variant tweakable later: RECIPE.md next to the
    video holds the verbatim system and user prompts, the corpus in scope, and
    the settings in force. Change a prompt there, re-run, get a different ad.
    """
    name, brief = lane
    steered = Company(**{**company.model_dump(),
                         "mission": f"{company.mission}\n\nCREATIVE DIRECTION FOR THIS AD: {brief}"})

    async with Recorder(out_dir / name) as rec:
        rec.note("lane", name)
        rec.note("lane_brief", brief)
        remix = await build_remix(steered, product, picked, corpus)
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


async def main(slug: str, product: str, n: int, render_video: bool) -> int:
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

    out_dir = Path("out") / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    lanes = LANES[:n]
    results = await asyncio.gather(
        *(build_variant(company, product, picked, corpus, lane, out_dir) for lane in lanes),
        return_exceptions=True,
    )

    manifest = []

    for lane, res in zip(lanes, results):
        if isinstance(res, BaseException):
            print(f"  {lane[0]:15} FAILED: {res}")
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
                         "score": score.overall, "disposition": dispo})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest)} variants to {out_dir}/")

    if not render_video:
        return 0

    public = VIDEO_DIR / "public"
    for entry in manifest:
        name = entry["variant"]
        print(f"\n=== rendering {name} ===")
        data = json.loads(Path(entry["json"]).read_text())
        remix = Remix(**data["remix"])
        try:
            mp3, duration = await synthesize(remix, out_dir / name)
            shutil.copy(mp3, public / "narration.mp3")
            shots = await fetch_shots(company, product, remix, public / "shots")
            props = build_props(company, product, remix, audio_path=mp3,
                                duration_s=duration + 2.4, shots=shots)
            write_props(props, out_dir / name)
            mp4 = out_dir / f"{slug}-{name}.mp4"
            render(out_dir / name / "props.json", mp4)
            size = mp4.stat().st_size / 1_000_000
            print(f"  {mp4}  ({size:.1f} MB, {duration:.1f}s)")
            entry["mp4"] = str(mp4)
        except Exception as exc:  # noqa: BLE001 - one bad variant must not stop the rest
            print(f"  FAILED: {exc}")
            entry["mp4"] = None

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rendered = sum(1 for e in manifest if e.get("mp4"))
    print(f"\n{rendered}/{len(manifest)} videos rendered into {out_dir}/")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("slug")
    p.add_argument("--product", required=True)
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--no-video", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.slug, a.product, a.n, not a.no_video)))
