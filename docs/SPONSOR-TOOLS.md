# Sponsor tools — what they are, what they cost us, what to do

Research for the Zero-Human Company Hackathon (Terac, Humanmade SF, 2026-08-15,
submissions close 21:00 PT).

Everything marked **VERIFIED** came back from a live HTTP request made while
writing this doc, using our own key. Everything else is labelled.

**The short version.** The Terac MCP is found, our key works, and the endpoint is
`https://terac.com/api/mcp`. It is a two-hour integration and we already own the
hard half. Between Pioneer and Band, **do Pioneer** — it is a `base_url` change
against a wrapper we already have, where Band is a rearchitecture of the crew
that the prize criteria explicitly refuses to award for a shallow version.

---

## 1. Terac MCP — the submission gate. SOLVED.

> "All projects must use the Terac MCP" … "Using the Terac MCP is required to
> submit your project!" — guidebook

This was the blocker in [HANDOFF.md](./HANDOFF.md) item 2. It is no longer
blocked. Everything below is VERIFIED against `terac.com` with
`TERAC_API_KEY` (in `.env`, and in Azure KV `kv-zerohuman-hack` as
`terac-api-key`).

### The endpoint

| | |
|---|---|
| **URL** | `https://terac.com/api/mcp` |
| **Transport** | Streamable HTTP, **POST only** (`GET` → 405; there is no SSE transport) |
| **Response framing** | `text/event-stream`, one `data:` frame per response — you must send `Accept: application/json, text/event-stream` or it rejects you |
| **Session** | **Stateless.** No `Mcp-Session-Id` is returned, and no `notifications/initialized` is required. Every call is an independent POST. |
| **Protocol** | `2025-06-18`. `serverInfo: {"name":"Terac","version":"1.0.0"}` |
| **Auth** | `Authorization: Bearer tk_...` **or** `x-api-key: tk_...` — both verified working |
| **Rate limit** | 100 req/min per key |

There is no `mcp.terac.com`, `api.terac.com` or `docs.terac.com` — all NXDOMAIN.
Everything is on `terac.com`, behind Vercel. **This is why the endpoint was never
found**: every guess pointed at a subdomain, and the answer was a path.

The unauthenticated 401 tells you the whole auth story, which is how it was
found:

```
www-authenticate: Bearer error="invalid_token", ...
{"error":{"code":"UNAUTHORIZED","message":"Authentication required.
 Use an API key (x-api-key header or Authorization: Bearer tk_...) or OAuth session."}}
```

### Ready-to-paste, works right now

```bash
curl -s -X POST https://terac.com/api/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TERAC_API_KEY" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call",
           "params":{"name":"terac_get_context","arguments":{}}}'
```

### What our account already is

`terac_get_context` returns live state. Ours:

| | |
|---|---|
| Organization | **Vira** — `bllks7wg95umnyg8n384pvg4`, slug `vira-msuo4fry` |
| Balance | **$25.00** |
| Default project | `cf60f5gh587n98a0fwosrzs6` |
| Opportunities | **2, both DRAFT** — created 2026-08-15T18:03Z |
| Dashboard | `https://terac.com/vira-msuo4fry` |
| Add credit | `https://terac.com/vira-msuo4fry/settings/finance` |

The two existing drafts are `bo55vs90u6wuxi3sk9uwxa8t` ("Consumer Products Ad &
Video Generation Expert Review", 50 participants) and `kdn9avar00lmd0mg5ajhpnwi`
("Marketing Workflow Research Study", 50 participants). Both unpriced and
unlaunched. **Someone started this and stopped.** Check with the team before
creating a third — and note 50 participants is almost certainly more than $25
buys.

### The 23 tools

All prefixed `terac_`. `*` = required argument.

**Context and projects**
- `terac_get_context` — no args. The server says **call this FIRST**; it returns
  org, balance, dashboard URLs and a long operating playbook.
- `terac_list_projects`, `terac_create_project` (`name*`),
  `terac_get_project` / `terac_update_project` (`projectId*`)

**Feasibility and pricing**
- `terac_request_feasibility` — `taskDescription*`, `panelDescription*`,
  `submissionCount`, `timelineHours`. Does **not** return a price; a human at
  Terac prices it, typically within ~1 hour.
- `terac_get_feasibility_request` (`requestId*`) — poll until
  `status == RESPONDED` and `costPerParticipant` is set
- `terac_list_feasibility_requests` — `status` ∈ `RECEIVED|RESPONDED|WON|LOST`

**Opportunities** — an "opportunity" is what the guidebook calls a study
- `terac_create_opportunity` — `title*`, `project_id*`, `num_participants*`
  (1–1000), `business_type*` (`b2c|b2b`), `tasks*`, plus `description`,
  `filters`, `unrestricted_audience`, `screening_questions`, `quotas`,
  `cross_quotas`, `device_types`, `expected_days_to_complete` (min 5, default 7),
  `feasibility_request_id`. **Creates a DRAFT — free, recruits nobody.**
  - `tasks[]`: `sequence*`, `task_type*` ∈ `interview|file_upload|activity`,
    `review_type*` ∈ `auto_approve|manual_review|self_report`, `task_url`,
    `title`, `description`, `duration_minutes` (drives pricing)
- `terac_update_opportunity` (DRAFT only; array fields **replace**, never merge)
- `terac_launch_draft_opportunity` (`opportunityId*`) — **spends real money**
- `terac_delete_opportunity`, `terac_list_opportunities`, `terac_get_opportunity`
- `terac_pause_opportunity`, `terac_resume_opportunity`,
  `terac_stop_opportunity` (irreversible, refunds unused budget)

**Submissions — the human data**
- `terac_get_submissions` (`opportunityId*`, `status` ∈
  `screen_passed|screened_out|in_progress|awaiting_review|approved|rejected`)
- `terac_get_submission` (`submissionId*`) — returns `screening_answers` and
  per-task results
- `terac_approve_submission` (`submissionId*`) — **pays the expert**
- `terac_reject_submission` — `rejection_category` ∈
  `low_quality|failed_instructions|incomplete|suspicious_patterns|other`

**Targeting**
- `terac_list_filters`, `terac_get_filter_options` (`filter_slug*`, `search`,
  `country_id`, `state_id`). Slugs are shaped `integer--age`,
  `multi_select--country`, `--language`, `--gender`, and so on.

### REST API — the simpler fallback

There is a plain-JSON REST API with no SSE parsing, and it exposes a shortcut
the MCP does not.

- Base: `https://terac.com/api/external/v2` (v2, beta)
- Spec: `https://terac.com/api/external/v2/openapi.json` (145 KB, "Terac
  External API 2.0.0"), auth `Authorization: Bearer <key>`
- 45 endpoints, mirroring the tools above, plus:
  **`POST /quotes` → `GET /quotes/{id}` → `POST /quotes/{id}/launch`** —
  price-then-launch in three calls, with **no MCP tool equivalent**. Likely the
  fastest path on a one-day clock.
- Webhooks: `GET|POST /hooks/subscriptions`, with signing secrets and event
  types. This is how you avoid polling for submissions.
- Docs: `https://terac.com/docs/developers`. Feed an agent
  `https://terac.com/docs/developers/llms-full.txt` (209 KB, whole docs in one
  file).

**No SDK exists.** npm search `terac` → 0 results; `@terac/mcp`, `terac-mcp`,
`terac`, `@terac/sdk` all 404. PyPI `terac`, `terac-mcp`, `terac-sdk` all 404.
Call HTTP directly. (GitHub org is `github.com/TeracAI`.)

### Client shape for this codebase

New file, `vira/terac.py`, ~60 lines. Nothing else has to change.

```python
"""Terac — the human labour MCP. Recruits the panel that judges our ads."""
import json
import httpx
from vira.config import settings

MCP_URL = "https://terac.com/api/mcp"


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {settings().terac_api_key}",
    }


async def call_tool(name: str, arguments: dict) -> str:
    """One stateless MCP tool call. No session, no initialize handshake."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(MCP_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        for line in r.text.splitlines():          # SSE-framed, single frame
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                res = msg["result"]
                if res.get("isError"):
                    raise RuntimeError(res)
                return "\n".join(c.get("text", "") for c in res.get("content", []))
    raise RuntimeError("no data frame in Terac MCP response")
```

`settings()` needs one line added in `vira/config.py`:
`terac_api_key: str | None = None` (the env var `TERAC_API_KEY` is already set,
and `deploy/README.md` already maps the vault secret).

### Why this is a ~2 hour job and not a ~10 hour one

**We already built the hard half and did not realise it.** `vira/api/routes/reviews.py`
is a complete, unauthenticated, token-keyed judge surface — judges rate 1–5, pick
favourites, comment, and never see the engine's own score. The Terac track wants
exactly this and we have it already:

```
generate N videos
  → POST /v1/review-batches       → judge_url with an unguessable token   [EXISTS]
  → terac_create_opportunity      → task_url = that judge_url             [NEW, ~1h]
  → terac_launch_draft_opportunity → Terac recruits and pays the panel    [NEW]
  → terac_get_submissions         → poll, or use the REST webhook         [NEW]
  → GET /v1/review-batches/{id}/results  → aggregated ratings             [EXISTS]
  → POST /v1/videos/{id}/regenerate      → human notes applied            [EXISTS]
  → the diff between the two recipes IS the required before/after         [EXISTS]
```

The `task_url` on an `activity` task is the whole integration surface. Our judge
link is a URL a stranger can use with no account — which is precisely what an
`auto_approve` activity task needs.

`docs/API.md` already documents this loop and names Terac in it. The only missing
piece was the recruiting call.

### Operational gotchas, from Terac's own `terac_get_context`

- **Drafts cost nothing.** Only `terac_launch_draft_opportunity` spends the $25.
  Cost is CPI × `num_participants` + platform fee. **50 participants will not fit
  in $25** — budget the count down, or add credit, before launching.
- **A screener triggers a mandatory AI voice interview** with every applicant who
  passes the form. It costs no money and a lot of **time**. On a one-day clock,
  **launch with no `screening_questions`** unless the panel genuinely needs
  filtering. Rating an ad does not.
- **"General Population"** (what the guidebook asks for) = omit `filters`
  entirely, or set `unrestricted_audience: true`. That is the fastest fill.
- **Never construct a dashboard URL.** Responses carry absolute `dashboard_url`
  strings and the routes are not uniform; a tidied-up URL lands on nothing.
- Array fields on `terac_update_opportunity` **replace wholesale**. Read current
  state first, or you will silently drop the tasks you did not resend.

---

## 2. Pioneer vs Band — the decision

### Recommendation: **Pioneer. Do not attempt Band.**

Not close. Pioneer is a `base_url` string against wrappers we already have. Band
is a rearchitecture of `vira/agentic/crew.py` whose prize criteria explicitly
disqualifies the cheap version.

### Pioneer (by Fastino Labs)

**What it actually is.** Not a router *layer* bolted onto other people's
accounts — a managed platform that fine-tunes, hosts and serves models, with a
router in front. Two things live under one key: a catalogue of hosted models
(open-weight *and* frontier), and a LoRA fine-tuning pipeline for open-weight
decoders and GLiNER encoders.

| | |
|---|---|
| Base URL | `https://api.pioneer.ai` (compat surface at `/v1`) |
| Auth | `X-API-Key: <key>` — no OAuth, no refresh |
| **OpenAI-compatible** | **Yes** — `POST /v1/chat/completions`, "drop-in replacement for the OpenAI SDK", streaming works |
| **Anthropic-compatible** | **Yes** — `POST /v1/messages`, drop-in for the Anthropic SDK |
| Native | `POST /inference` — schema-based extraction (entities, classifications, structures, relations) |
| Fine-tuning | `POST /felix/training-jobs`, LoRA, poll `GET /felix/training-jobs/:id` |
| SDKs | None of its own — you use the **OpenAI or Anthropic SDK** with a changed `base_url`. A CLI exists. |
| Docs | `docs.pioneer.ai`, `llms.txt` and `openapi.json` both published |
| Signup | `https://agent.pioneer.ai`, promo `ZeroHumanHack0826` → free Pro + inference credits (Billing → Get Pro → code at Stripe checkout) |
| Models | Open-weight: Nemotron 3.5 Lightning 30B, Fastino Finance/Healthcare variants, GLiNER2 (Base/Large/Multi), GLiGuard, GLiNER2-PII. Serverless frontier: DeepSeek V4 Flash, GLM 5.2, Claude 5 family, GPT-5.5/5.6. Router alias `pioneer/auto`. |

**Usable today: yes, unambiguously.** Public docs, public OpenAPI, no SDK to
install because it reuses two SDKs already in `requirements.txt`, and a hackathon
promo code that grants credits at Stripe checkout. No approval queue.

**How it slots in.** Both integration points are one argument each.

`vira/llm.py:30` — the whole pipeline's writing and scoring:

```python
client = AsyncAnthropic(api_key=s.anthropic_api_key)
# becomes
client = AsyncAnthropic(api_key=s.pioneer_api_key,
                        base_url="https://api.pioneer.ai/v1")
```

`vira/agentic/crew.py:401` — the Director's tool-calling loop:

```python
client = AsyncAzureOpenAI(azure_endpoint=..., api_key=..., api_version=...)
# becomes
client = AsyncOpenAI(api_key=s.pioneer_api_key,
                     base_url="https://api.pioneer.ai/v1")
```

Both wrappers were already built to make exactly this swap cheap —
`vira/llm.py`'s own docstring is *"One place to swap providers"*. Add
`pioneer_api_key` to `vira/config.py`, and gate it so `LLM_PROVIDER=pioneer`
flips it and nothing else changes. Provenance, the event bus and the recipes all
keep working, because they capture `s.llm_model` as a string and do not care who
served it.

**Real work: 1–2 hours**, and most of that is not the swap.

**The catch that decides how you spend those hours.** The prize criteria is
*"Use open-weight model(s) on Pioneer to build a compelling product"*, with bonus
points for Fastino models, GLiNER2/GLiGuard/GLiNER2-PII, or the fine-tuning API.
**Proxying Claude through Pioneer does not satisfy that track** — Claude is not
open-weight, and a `base_url` change is not a product. To actually compete you
need an open-weight model doing load-bearing work.

Two candidates, and the first is genuinely the best idea in this document:

1. **GLiNER2 for the evidence gate.** [CONTEXT-RETRIEVAL.md](./CONTEXT-RETRIEVAL.md)
   says every variant is currently dropped on evidence, and that this is a
   *retrieval* problem, not a scoring one. GLiNER2 is a schema-based structured
   extractor — give it a claim and a verified source and it pulls entities and
   relations deterministically, at encoder speed and encoder cost. That is a real
   fix for our live blocker, not a demo. It also reads as exactly what the track
   asks for: an open-weight specialist model doing work a frontier model was
   doing badly.
2. **GLiGuard as a pre-publish safety gate.** An autonomous ad company that
   publishes without a moderation pass is a story with an obvious hole in it.
   GLiGuard is a 300M open-weight safety SLM; running generated copy through it
   before render is ~20 lines, and it makes the "zero human" claim defensible
   rather than reckless. Judges will ask this question.

Either one, plus `pioneer/auto` serving the Director, is a credible Pioneer
entry. Do (2) if time is short — it is smaller and it closes a narrative gap.
Do (1) if there is an hour, because it fixes something that is actually broken.

### Band

**What it actually is.** Interaction infrastructure: agents from any framework
join persistent chat rooms, address each other with `@mentions`, and coordinate
over a WebSocket, with contacts and permissions as a governance layer. It is a
real, complete product — the docs are excellent and the adapter list is long.

| | |
|---|---|
| REST base | `https://app.band.ai/api/v1/agent` (`/me`, `/peers`, `/chats`, `/chats/{id}/messages`, `/chats/{id}/events`) |
| WebSocket | `wss://app.band.ai/api/v1/socket/websocket?api_key=…&vsn=2.0.0`, Phoenix Channels, read-only |
| SDKs | **PyPI `band-sdk` 1.6.0** (requires Python ≥3.11), **npm `@band-ai/sdk` 0.1.10** |
| Auth | **Per agent**: an `agent_id` UUID plus an `api_key`, from the dashboard. Not one key for the app — one pair per agent. |
| OpenAI-compatible | N/A — different category of thing |
| Adapters | LangGraph, Anthropic, ClaudeSDK, CrewAI, PydanticAI, Gemini, Codex, Strands, Agno, Letta, + Slack/A2A/ACP bridges |
| MCP | Yes, Band also exposes its own MCP server for platform automation |
| Docs | `docs.band.ai`, `llms.txt`, plus `https://www.band.ai/hacker-guide` |
| Signup | `https://www.band.ai/`, code `HACKBANDAUG26` → free month of Pro. Free tier already allows 10 agents. |

**Usable today: yes.** Packages are public and current, signup is self-serve, the
hacker guide is a working quickstart. Nothing about Band is vapour. That is not
the problem.

**How it would slot in — and why that is expensive.** Band would replace the
in-process dispatch in `crew.py:490`, where the Director's tool calls run as
`asyncio.gather` over seven local Python functions. Converting means:

- Each specialist becomes **its own long-lived process** running
  `await agent.run()`, registered in the Band dashboard, with its own
  `agent_id` + `api_key` pair. Seven specialists ≈ seven registrations and seven
  processes to supervise.
- **The shared `Production` dataclass dies.** Every tool in `crew.py` reads and
  mutates one object — `p.shots`, `p.style_contract`, `p.descriptions`,
  `p.remix`, `p.mp3`, and `out_dir` / `public_dir` **paths on local disk**.
  Separate processes cannot share a mutable dataclass or a local filesystem. You
  would need an object store and a serialisation layer for state that is
  currently a field access.
- **`regenerate_frame` is the specific casualty.** It depends on the pinned
  `p.style_contract`, the existing `p.shots[i]`, and writing
  `shot{i:02d}.jpg` into a directory the cohesion checker then reads. That
  surgical single-frame fix — the capability [HANDOFF.md](./HANDOFF.md) calls
  the thing the straight-line pipeline lacks, and which cost real debugging to
  get right — is the hardest part to move across a process boundary.
- Coordination becomes chat messages over a WebSocket instead of a function call
  in the same event loop. That is **slower**, and this repo's first working rule
  is that speed is the first requirement.

**Real work: a day, minimum, and it is a rewrite rather than an integration.**

**And the cheap version is explicitly disqualified.** The criteria:

> "it's when the coordination between agents happens in Band, and taking Band out
> breaks the project… remove the room and the app should break, not keep working
> the same way."

They have pre-empted the shallow integration. Mirroring our existing crew chatter
into a Band room for show earns nothing — by their own wording, if the app still
works with the room removed, it does not qualify. So there is no cheap partial
credit to collect here.

### The verdict, plainly

| | Pioneer | Band |
|---|---|---|
| Files touched | `llm.py`, `crew.py` (1 line each), `config.py` | `crew.py` rewritten, 7 new processes, new state layer |
| Hours | **1–2** | **8+** |
| Prize | $500 | $500 |
| Adds capability we lack | **Yes** — open-weight extraction/safety, and a shot at the evidence blocker | No — we already orchestrate 7 specialists in 7 turns / 250s |
| Costs capability we have | No | **Yes** — in-process speed, shared state, `regenerate_frame` |
| Risk of shipping nothing | Low | High |

Band is a good product aimed at a problem we do not have. Our Director already
converges in 7 turns and 250 seconds by calling functions in one event loop;
putting a WebSocket and seven processes between them buys ceremony and spends the
one budget we cannot refill today. **Pioneer, and use the saved hours on Terac.**

---

## 3. Every sponsor tool, and what it would do for us

From the guidebook's "Sponsors & Partners" and "Credits & Subscriptions"
sections. "Have it" = already in the stack.

| Sponsor | What it is | For a video-ad engine | Prize | Status |
|---|---|---|---|---|
| **Terac** (host) | 180k+ vetted experts, human labour on demand via API & MCP | **Recruits the judging panel** for `/v1/review-batches`. The before/after the track wants | — (**mandatory**) | Key works, **not integrated** |
| **Stripe** | Payments | Personal account + payment link = the revenue proof for the $2500 agent-run-company track | gates **$2500** | **Test keys only** |
| **Lovable** | AI app builder | The frontend and the corpus | — | Have it |
| **Render** | Managed cloud | Hosting — but the prize needs **Render Workflows** specifically | $500/$300/$100 credits | Hosting yes, **Workflows no** |
| **Linq** | Real phone number on iMessage/SMS, rich media, **iMessage Apps**, tapbacks, webhooks, Agent Pay | Deliver the finished ad into a client's iMessage; a 👍 tapback becomes a vote. Genuinely the best creative fit of any sponsor | **$1500** / $1000 | Not started; sandbox signup needs approval |
| **Replay** | AI QA that explores a web app, finds bugs, reports root causes | Point it at the Lovable console + our API. No test-writing | **$1000** / $500 (+$50 per false positive) | Not started; code `HACKATHON` at qa.replay.io |
| **Superserve** | Pausable/resumable VM sandboxes for agents | Where a Remotion render or ffmpeg could run. We already have chipdev and Render | **$1000** / $500 | Not started; no payment info needed |
| **Pioneer** | Fine-tune + serve open models; OpenAI- and Anthropic-compatible | `base_url` swap; GLiNER2 for the evidence gate, GLiGuard for pre-publish safety | $500 | Not started; promo `ZeroHumanHack0826` |
| **Band** | Cross-framework agent-to-agent rooms | Would replace in-process crew dispatch. See §2 | $500 | Not started; promo `HACKBANDAUG26` |
| **Whop** | End-to-end API for running an internet business | Storefront/checkout for the ad product | — | No |
| **Dodo Payments** | Merchant-of-record billing | Alternative to Stripe — but Stripe is the one the prize names | — | Skip |
| **Egoist Machines** | "AI Passport", portable user context | Carry a brand's tone/preferences across sessions. Interesting, unproven | — | No |
| **sandbox0** | Open-source agent sandboxes | Same slot as Superserve, no prize attached | — | Skip |
| **Solari** (Pinetree) | Headless browsers, sandboxes, desktops | Could drive the Apify/TikTok corpus scrape | — | Promo `ZEROHUMANHACK-SWSYP3XJ` |
| **Interview Cake** | Interview prep | Nothing | — | Irrelevant |
| **Nucleate** | Bio-innovator community | Nothing | — | Irrelevant |

### Ranked by (value to us) ÷ (hours to integrate)

**Rank 0 — Terac. Not on the list because it is not optional.** ~2 hours, and it
is the difference between submitting and not submitting. Do it first, before
anything below. We own the judge surface already; we are wiring a recruiter to a
URL that exists.

Then, in order:

**1. Stripe — best ratio on the board.** ~40 minutes of dashboard clicking
(personal account → payment link with "customer chooses price" → restricted
`rk_` key with Balance+Charges read → submit team name, link, key) and it is the
sole gate on the **$2500** Best Overall Agent-Run Company track. We have test
keys, which count for nothing here; the track is judged on *real revenue earned
during the day*. Nothing else converts an hour into $2500 of eligibility. The
engine does not need to change — the agent just needs a link to send.

**2. Pioneer — ~1.5 hours for $500 and a fix to something actually broken.**
The swap is one line in each of two files. Spend the remaining time on GLiGuard
as a pre-publish safety gate (small, closes the obvious hole in a "zero human"
pitch) or GLiNER2 against the evidence blocker (bigger, but it is the live
problem in this repo). Full reasoning in §2.

**3. Replay — ~1 hour of our attention for $1000.** Sign up at `qa.replay.io`,
code `HACKATHON` on the billing page, point it at the console and the API, let it
crawl while we work on Terac, fix what it finds. The criteria is just "build a
great app and get a clean report after fixing the bugs it finds". It runs
unattended, so its real cost is wall-clock, not developer hours — which makes it
almost free on a day when we are blocked on other things anyway. Best
prize-per-hour after Stripe.

**Just outside:** **Render Workflows** ($500 credits, ~2h) — we are already on
Render with a render worker on a 30-minute tick, so converting that tick into a
Workflow is a plausible afternoon, but it is credits rather than cash and it
touches deploy infrastructure on a deadline. **Linq** has the biggest single
prize ($1500) and by far the best creative fit — a tapback as a vote is a lovely
idea, and it would pair with Terac — but sandbox signup requires approval we do
not control, which is an unacceptable dependency this late.

### Suggested order for the remaining hours

1. **Terac** — mandatory. Two hours. Reuse `/v1/review-batches`; launch
   screener-less at General Population with a participant count that fits $25.
2. **Stripe** — forty minutes of clicking. Unlocks the largest track.
3. **Pioneer** — the two-line swap, then GLiGuard.
4. **Replay** — start the crawl early so it runs while everything else happens.
5. Stop. Band and Linq are good products and the wrong bets today.
