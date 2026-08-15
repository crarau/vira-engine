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
