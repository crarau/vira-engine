# vira-engine — working rules

## Speed is the first requirement

This is a video generation engine. Every iteration means waiting for a render,
so latency is not a nice-to-have — it is the thing that decides how many ideas
get tried in an afternoon.

**Before writing any code that does work: ask what runs in parallel.** If the
answer is "nothing", that is a bug in the design, not a property of the task.

### Standing rules

1. **Independent I/O runs concurrently.** Anything network-bound — LLM calls,
   image generation, TTS, URL verification, vision descriptions — goes through
   `asyncio.gather`. Never `for x in items: await f(x)`.
2. **Overlap stages that do not depend on each other.** Voice and imagery both
   need the script and neither needs the other, so they run together. Look for
   this pattern every time a new stage is added.
3. **Bound the parallelism, do not remove it.** CPU work (Remotion) gets a
   semaphore sized to the machine. Network work rarely needs one; API rate
   limits do.
4. **Regenerate the smallest unit that changed.** One frame is 8 seconds; the
   film is 74. `regenerate_frame` exists for this reason. Never redo the whole
   thing to fix one part.
5. **Cache anything paid for.** Props, audio and images are written to the
   versioned output dir. Re-rendering from saved props costs no API calls —
   `npx remotion render AdVideo out.mp4 --props=.../props.json`.
6. **Measure, then optimise.** Print elapsed time on every entry point. "Feels
   slow" is not a bug report.

### What is already parallel

| Work | How |
|---|---|
| 8 image generations | `asyncio.gather` in `imagegen.generate_shots` |
| 20 URL verifications | `asyncio.gather` + semaphore(8) in `verify.verify_all` |
| 8 vision descriptions | `asyncio.gather` in `cohesion.describe_all` |
| 5 variant scripts | `asyncio.gather` in `variants.main` |
| Voice ‖ imagery | `asyncio.gather` in `variants.produce` |
| 5 renders | semaphore(2) × concurrency 4, `asyncio.to_thread` |

### Measured

| Job | Time |
|---|---|
| One video, deterministic | **74s** |
| Five videos, deterministic | **314s** (was ~15 min sequential) |
| One video, agentic crew | ~350s |
| One image | ~8s |
| Re-render from saved props | ~40s, zero API cost |

### The escape hatch

`render_remote.py <slug>` pushes rendering to the 32-core box (`chipdev`) and
brings the mp4s back. Use it when rendering more than two videos — this laptop
has 11 cores and renders two at a time; that machine renders five at once.

## Architecture rules

**The evidence gate is not negotiable.** It runs in Python, after everything
else, and no agent can call it or see its threshold. A fluent model will
otherwise produce a confident, ungrounded ad. If output keeps getting dropped,
fix the grounding — do not lower `EVIDENCE_FLOOR` to make the number go up.

**Timing comes from the synthesiser, never from a model.** Beat and word
timings are derived from ElevenLabs character timestamps. Nothing hand-authors a
frame number. This is what makes copy changes re-time the video for free.

**Nothing ships without a verified `source_url`.** Verify before reasoning:
every source is fetched before it reaches a prompt.

**Every video keeps its recipe.** `RECIPE.md` next to each output holds the
verbatim prompts, the corpus in scope, and the settings in force. If you add a
stage that calls a model, route it through `vira.llm` so it is captured.

## Gotchas that have already cost time

- **Never validate a render in VS Code.** It reported broken audio on a provably
  good file. Use QuickTime, or extract a frame with `ffmpeg` and look at it.
- **A render can succeed and be blank.** Exit code 0, right duration, real
  audio, every caption at `opacity: 0`. Inside a Remotion `<Sequence>`,
  `useCurrentFrame()` is already sequence-relative — subtracting `startFrame`
  again drives springs negative. Always extract a frame after changing the
  composition.
- **Transforms do not affect layout.** A span scaled to 1.12 visually overflows
  its box and eats the flex gap. Use padding for separation.
- **Generated frames are natively 9:16.** Ken Burns above ~1.08 scale crops the
  subject the director asked for straight out of frame.
- **Python 3.12+.** The system python is 3.9 and cannot import the models.
- **PostgREST caps responses at 1000 rows** regardless of `limit`. Use
  `Supa.select_all`, which pages.

## Public by design — do not add auth

`https://vira.ideaplaces.com` and `https://console.ideaplaces.com` are open on
purpose. This is a hackathon: the team, the judges and anyone we hand the link
to should be able to hit it without a credential. **Do not add authentication,
rate limiting or an allowlist unless explicitly asked** — "the API has no auth"
is a decision, not an oversight, and flagging it every session wastes time.

What that does mean:

- `POST /v1/videos` spends real money on every call (Gemini, ElevenLabs,
  Anthropic, Azure). Share the URL with people, not in public places.
- Everything under `out/` is world-readable via `/media`, **including
  `RECIPE.md` and `recipe.json` with verbatim prompts**. Never write anything
  secret there.
- Secrets live in Azure Key Vault `kv-zerohuman-hack` and reach the box in a
  `chmod 600` env file. They never go through git and never into `out/`.

Revisit only when this stops being a hackathon.

## The API is live — publish every stable change

`https://vira.ideaplaces.com` is a public URL that other people (and Lovable)
call. The box behind it must not drift from what is stable locally.

**Whenever the REST API changes and the change is stable, publish it:**

```bash
deploy/publish.sh                 # ship what is already committed
deploy/publish.sh -m "message"    # commit everything first, then ship
```

Never `git push` an API change and consider it done — pushing updates GitHub,
not the machine. Nothing serves the new code until `publish.sh` restarts
uvicorn on `chipdev`.

The script is deliberately conservative and already handles the dangerous
parts: it refuses a dirty tree without a commit message, runs the tests and
aborts if they fail, applies the idempotent schema, health-checks on the box,
**rolls back to the previous commit if the new one will not start**, then
verifies through the tunnel.

Check whether the machine is current before assuming it is:

```bash
git rev-parse --short HEAD
ssh chipdev 'cd $HOME/vira-engine && git rev-parse --short HEAD'
curl -s -o /dev/null -w '%{http_code}\n' https://vira.ideaplaces.com/healthz
```

Stable means: tests pass, and the endpoint has been exercised at least once
locally. A half-finished endpoint stays on the laptop — the public URL is not a
staging environment.

`deploy/publish.sh` does **not** touch the tunnel or DNS. Those are Terraform in
`ideaplaces-devops`, and a dashboard edit gets reverted by the next apply.

## Layout

```
vira/
├── select.py verify.py analyze.py   corpus → verified shortlist
├── director.py remix.py score.py    plan → write → critique → gate
├── voice.py imagegen.py stock.py    performance and frames
├── lanes.py                         creative profiles (voice + look + brief)
├── render.py provenance.py          Remotion props, recipes
└── agentic/crew.py cohesion.py      the crew and the continuity checker
variants.py       five lanes, parallel
agentic_video.py  one video, Director-led
render_remote.py  offload rendering to chipdev
```

Output is versioned: `out/<slug>/v<NNN>-<timestamp>/`, with `latest` symlinked
to the newest. Never overwrite a previous run.

## Design tokens

Style guide source: Styleguide conversation `1fc85745-6499-4412-bd76-cbd6a9c119c4`,
version 3 / `c8c1c195-ea40-4379-b9fc-13fb7099b4da`, palette **Forensic Teal**,
selected 2026-08-17. Generated via `/styleguide-generate` against
`styleguide.ideaplaces.com/api/v1`.

Chosen over the other three because they all used a blue primary — the SaaS
default — while deep teal is the near-complement of the render accent `#F5C518`,
so the web UI and the video frames read as one system. Its state language
(copper vs moss) also carries kept-versus-cut without green/red form validation.

Files: `design/{tokens.json,variables.css,tailwind.config.js,colors.ts}` and
`docs/style-guide.html` (standalone preview). `tokens.json` is the source the
other formats re-derive from.

**Two hand-written additions live at the bottom of `design/variables.css` and are
NOT in the export — re-apply them after any regenerate:**

1. **Dark tokens.** The bundle ships light only. `#0F6B6D` on the dark ground
   measures **2.95:1**, so the dark primary is raised to `#2E9B9D` (5.54:1).
   Full parity: 18 colour tokens in each theme.
2. **Render tokens** (`--render-*`). The video palette is fixed in
   `video/src/Captions.tsx` and `AdVideo.tsx` and is deliberately **not**
   theme-aware — an exported mp4 has one appearance forever.

Fonts are Inter + IBM Plex Mono, both freely licensed. No substitution needed.
