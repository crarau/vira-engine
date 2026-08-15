"""Render a slug's variants on the chipdev box instead of this laptop.

    python render_remote.py sunday-oats

This machine has 11 cores, so it renders two variants at a time. chipdev has 32
and sits idle, so it renders all five at once. The Python stages (LLM, TTS,
image generation) stay local — they are network-bound, not CPU-bound, and
shipping them would buy nothing.

Assets move by rsync rather than git: `video/public/` holds generated images and
narration that are deliberately not committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HOST = "chipdev"
REMOTE = "~/vira-engine"
LOCAL = Path(__file__).resolve().parent


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def main(slug: str, parallel: int, concurrency: int) -> int:
    out = LOCAL / "out" / slug
    props = sorted(out.glob("*/props.json"))
    if not props:
        print(f"no props under {out}/ — run variants.py --no-video first")
        return 1
    names = [p.parent.name for p in props]
    print(f"{len(names)} variants: {', '.join(names)}")

    t0 = time.monotonic()

    # 1. Code. Cheap, and keeps the composition in step with local edits.
    print("syncing composition…")
    sh(["ssh", HOST, f"cd {REMOTE} && git fetch -q origin && git reset -q --hard origin/main"])
    sh(["rsync", "-az", "--delete",
        f"{LOCAL}/video/src/", f"{HOST}:{REMOTE}/video/src/"])

    # 2. Assets — the big one. Images and audio are gitignored by design.
    print("syncing assets…")
    sh(["rsync", "-az", "--delete", f"{LOCAL}/video/public/", f"{HOST}:{REMOTE}/video/public/"])

    # 3. Props, one per variant.
    sh(["ssh", HOST, f"mkdir -p {REMOTE}/out/{slug}"])
    for p in props:
        sh(["ssh", HOST, f"mkdir -p {REMOTE}/out/{slug}/{p.parent.name}"])
        sh(["rsync", "-az", str(p), f"{HOST}:{REMOTE}/out/{slug}/{p.parent.name}/props.json"])

    # 4. Render everything at once. 5 x 6 workers fits comfortably in 32 cores.
    print(f"rendering {len(names)} variants on {HOST} "
          f"({parallel} at a time x {concurrency} workers)…")
    remote_script = f"""
set -e
cd {REMOTE}/video
pids=""
run() {{
  npx remotion render AdVideo "../out/{slug}/$1.mp4" \
    --props="../out/{slug}/$1/props.json" --concurrency={concurrency} \
    > "/tmp/render-$1.log" 2>&1 && echo "  ok   $1" || echo "  FAIL $1"
}}
i=0
for v in {' '.join(names)}; do
  run "$v" &
  pids="$pids $!"
  i=$((i+1))
  if [ $((i % {parallel})) -eq 0 ]; then wait; fi
done
wait
ls -la ../out/{slug}/*.mp4 2>/dev/null | awk '{{printf "  %-46s %6.1f MB\\n", $9, $5/1000000}}'
"""
    proc = subprocess.run(["ssh", HOST, "bash -s"], input=remote_script,
                          text=True, capture_output=True)
    print(proc.stdout.rstrip() or proc.stderr[-1500:])
    if proc.returncode != 0:
        print(f"remote render exited {proc.returncode}")

    # 5. Bring the videos home.
    print("fetching mp4s…")
    sh(["rsync", "-az", "--include=*.mp4", "--exclude=*",
        f"{HOST}:{REMOTE}/out/{slug}/", f"{out}/"])

    got = sorted(out.glob("*.mp4"))
    print(f"\n{len(got)} videos in {time.monotonic() - t0:.0f}s")
    for g in got:
        print(f"  {g.name}  {g.stat().st_size / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("slug")
    p.add_argument("--parallel", type=int, default=5)
    p.add_argument("--concurrency", type=int, default=6)
    a = p.parse_args()
    sys.exit(main(a.slug, a.parallel, a.concurrency))
