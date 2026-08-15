# Handoff — everything worth knowing

Written so a new session, on any machine, can pick this up cold. Read this
first, then the doc it points you at.

## Which machine

**Stay on the Mac for iteration. Use chipdev for batches and for hosting.**

| | Mac (M-series, 11 cores) | chipdev (Azure, 32 vCPU) |
|---|---|---|
| One video end to end | **74s** | slower per core |
| Five videos | 314s (2 renders at a time) | faster — fits 5×6 workers |
| Single render | faster per core | slower per core |
| Hosting the API | no | **yes** |

A single video is dominated by network — LLM calls, TTS, image generation — not
CPU. More cores does not make an HTTP round trip faster, and Azure vCPUs are
meaningfully slower per core than Apple Silicon. So the laptop wins the loop you
actually spend your day in.

Batches invert it: rendering is embarrassingly parallel and chipdev fits five at
once where this Mac fits two. `render_remote.py <slug>` pushes only the render
phase there and brings the mp4s back, which is the right split — generate here,
render there.

The API is a separate question and the answer is chipdev, because a service
needs to be always-on and near its own Postgres.

## What exists

| Thing | Where | State |
|---|---|---|
| Engine | `crarau/vira-engine` (public) | working |
| Frontend + corpus | `jp-215/company-essence-lab` → Lovable Cloud | Jesh's |
| Backend spike | `crarau/zero-human-company` (public) | reference only, not running |
| Render worker | `srv-da0c6me1egvs738d33t0` | deployed, 30-min tick |
| Secrets | Azure KV `kv-zerohuman-hack` | 14, Chip-only ACL |
| Tests | `tests/`, 191 | green, offline, 1.1s |

## The pipeline

```
select → verify → analyze → plan → write → critique → revise
       → score → voice ‖ imagery → render
```

Two modes over the same stages:

- **`variants.py`** — deterministic, five creative lanes in parallel. 314s.
- **`agentic_video.py`** — a Director on gpt-5.4 plans, delegates to seven
  specialists, inspects what came back and fixes it. Slower, self-correcting.

Both write versioned output: `out/<slug>/v<NNN>-<timestamp>/`, `latest`
symlinked. Nothing overwrites.

## The rules that must not be relaxed

**The evidence gate is not negotiable.** It runs in Python, after everything
else, and no agent or request parameter can reach it. If output keeps getting
dropped, fix the retrieval — do not lower `EVIDENCE_FLOOR` to make the number go
up. Everything generated so far has been dropped, and
[CONTEXT-RETRIEVAL.md](./CONTEXT-RETRIEVAL.md) explains why that is a retrieval
problem, not a scoring problem.

**Timing comes from the synthesiser.** Beat and word timings derive from
ElevenLabs character timestamps. Nothing hand-authors a frame number. This is
what makes a copy change re-time the whole video for free, and it is why
localisation later costs a translation pass and nothing else.

**Nothing ships without a verified `source_url`.** Verify before reasoning.

**Judges never see the engine's score.** The judge payload has no score field to
populate. Showing a human the machine's grade first anchors them to it and
destroys the independent signal the review exists to collect.

**Lovable owns the corpus; Python owns what it derives.** Never mirror.
See [DATA-BOUNDARY.md](./DATA-BOUNDARY.md).

## Bugs found, and why each mattered

These cost real time. Each is now a test.

**`min(5.0, nan)` returns `5.0`.** NaN compares `False` against everything, so
the score clamp turned an unscored dimension into a *perfect* one — and
`json.loads` accepts bare `NaN`, so it was reachable. A NaN evidence score
walked straight through the evidence gate. The exact failure the gate exists to
prevent, arriving on the one path nothing inspects.

**asyncpg reads `ssl=True` as `verify-full`, not libpq's `require`.** Any
`?sslmode=require` URL — Render, Heroku, the Supabase pooler — would have died
at boot against a self-signed cert. Proved with a TLS container.

**A Remotion render can succeed and be blank.** Exit code 0, correct duration,
real audio, every caption at `opacity: 0`. Inside a `<Sequence>`,
`useCurrentFrame()` is already sequence-relative; subtracting `startFrame` again
drives springs negative. **Always extract a frame and look at it.**

**Single-frame regeneration derived a new style contract each time**, so every
fix broke a neighbour and the cohesion loop chased its tail for six turns. The
contract is now pinned.

**Regenerating beat N deleted beat 0's image.** `generate_shots` names by
position, so a single-beat call always produced `shot00.jpg`, which was then
moved onto `shotNN.jpg`. The Director noticed before I did.

**A blank first or last beat stole the opening/closing performance tag**, so the
CTA lost `[shouting]` and the ad flattened at the end.

**`company_trends()` returns 100% stale rows.** The RPC caps at 200 ordered by
`trend_score`, and since that score is half reach, the window fills with old
megaviral clips — every row it returned was over 90 days old while 56% of the
corpus was under 90. Filter `posted_at` in the database, before the cap.
**Jesh's UI has the same bug.**

**Enrichment never scrapes.** `company_insights` returns polished positioning
with `sources: []` and `website: null` — signup never captures a URL, so the
170-line scraper has never run. The "enrichment" is an LLM paraphrase of the
user's own two sentences.

**VS Code lies about media.** It reported no audio on a provably good file.
Never validate a render there. See [vscode-video-audio.md](./vscode-video-audio.md).

## Craft notes that are not obvious

- **v3 `stability: 0.0`** is a *creativity* dial, not a consistency one.
  Measured 25% more dynamic range than 1.0 on the same line. This is the
  difference between a read and a performance.
- **Gemini frames are natively 9:16.** Ken Burns above ~1.08 crops the subject
  the director asked for straight out of frame.
- **Transforms do not affect layout.** A span scaled to 1.12 visually overflows
  its box and eats the flex gap. Separate with padding.
- **Stock search answers "what exists?"** The director already wrote the frame;
  generating it beats hunting for an approximation. Openverse CC is mostly
  amateur Flickr and scanned documents.
- **A lane is a full creative profile** — copy brief, voice, performance-tag
  palette, and visual grade. Five ads that differ only in wording are one ad in
  five costumes.

## Operational

```bash
# one video, fast lane
.venv/bin/python variants.py <slug> --product "…" --only founder-story

# one video, agent crew
.venv/bin/python agentic_video.py <slug> --product "…" --lane contrarian

# re-render from saved props — no API cost
cd video && npx remotion render AdVideo out.mp4 --props=../out/<slug>/latest/<lane>/props.json

# batch render on chipdev
python render_remote.py <slug>

# local API database
sql/dev-db.sh start        # podman/docker, port 55432

# tests
.venv/bin/python -m pytest -q
```

**Python 3.12+.** The system python is 3.9 and cannot import the models.
Secrets: `az keyvault secret show --vault-name kv-zerohuman-hack --name <n> --query value -o tsv`.

## Still open

1. **Retrieval is the live problem.** Every variant is dropped on evidence
   because sources are selected by category alone. This is the highest-value
   fix and the plan is in [CONTEXT-RETRIEVAL.md](./CONTEXT-RETRIEVAL.md).
2. **Terac MCP** — a hard submission requirement for the hackathon, still not
   integrated. Key is in the vault.
3. **No auth on the API.** The tunnel would publish it open, and the whole
   `out/` tree is readable via `/media`, including recipes with verbatim
   prompts.
4. **`/media` URLs must come from `X-Forwarded-Host`.** Behind a tunnel
   `request.url` resolves to the internal bind. C3 hit this exact bug.
5. **Regenerate lineage** lives in the recipe jsonb, not in columns. Add
   `jobs.source_video_id` and `jobs.notes` to query it properly.
6. **`company_knowledge` is empty** — the pgvector RAG has never been populated.

## Doc index

| Doc | Read it when |
|---|---|
| [BUILD-LOG.md](./BUILD-LOG.md) | you need the full history and architecture |
| [CONTEXT-RETRIEVAL.md](./CONTEXT-RETRIEVAL.md) | fixing why everything gets dropped |
| [DATA-BOUNDARY.md](./DATA-BOUNDARY.md) | deciding what goes in which database |
| [AGENTIC-VIDEO-SYSTEM.md](./AGENTIC-VIDEO-SYSTEM.md) | working on the crew |
| [API.md](./API.md) | working on the REST service |
| [RECIPES.md](./RECIPES.md) | tracing or tweaking a generated video |
| [vscode-video-audio.md](./vscode-video-audio.md) | media appears broken |
| [../CLAUDE.md](../CLAUDE.md) | before writing any code here |
| [../deploy/README.md](../deploy/README.md) | deploying to chipdev |
