# Terac — the human panel

Terac is the human-labour MCP: you describe a task and an audience, it recruits,
screens, delivers, verifies and pays. Using it is a hard submission requirement
for the hackathon ("All projects must use the Terac MCP"), and it is also the
one thing the engine cannot do for itself. The engine grades its own output on
five dimensions; it has no way to know which of five equally-grounded cuts a
person would actually stop scrolling for.

**The integration is one URL.** `vira/api/routes/reviews.py` already mints a
judge link — unauthenticated, keyed by an unguessable token, showing a stranger
the films with no engine score attached. Terac's `activity` task type takes a
`task_url`. So publishing a review batch to a paid panel is: take the
`judge_url` we already return, and make it the `task_url` of a Terac task.
Everything in this document is scaffolding around that one line.

## Files

| | |
|---|---|
| `vira/terac.py` | The MCP client and the payload builder |
| `vira/api/routes/terac.py` | `/v1/terac/*` and `publish-to-terac` |
| `terac_cli.py` | The demo surface: `status`, `tools`, `publish`, `responses` |
| `tests/test_terac.py`, `tests/test_terac_routes.py` | Transport, payload, spend guards |

`vira/api/app.py` gains one import name and one `include_router` line.
`vira/config.py` gains `terac_api_key` and `terac_project_id`. No schema change:
the batch↔opportunity link is the token inside the `task_url`, which Terac
stores for us and `terac.batch_token_of` reads back.

## The endpoint

| | |
|---|---|
| URL | `https://terac.com/api/mcp` — **a path, not a subdomain.** `mcp.`/`api.`/`docs.terac.com` are all NXDOMAIN, which is why this was hard to find |
| Transport | Streamable HTTP, **POST only** (`GET` → 405) |
| Framing | `text/event-stream`. Even a single JSON-RPC reply arrives as one `data:` frame, so the body must be unwrapped before it is JSON |
| Accept | **`application/json, text/event-stream`** — send only the first and the server refuses before reading the body |
| Session | **Stateless.** No `Mcp-Session-Id`, no `initialize`, no `notifications/initialized`. Every call is an independent POST |
| Auth | `Authorization: Bearer tk_…` or `x-api-key: tk_…`; both work. We send Bearer |
| Rate limit | 100 req/min per key |
| Protocol | `2025-06-18`, `serverInfo: {"name":"Terac","version":"1.0.0"}` |

The key is `TERAC_API_KEY` in `.env`, and in Azure Key Vault `kv-zerohuman-hack`
as `terac-api-key`. It is never logged and never appears in an error message —
`tests/test_terac.py` asserts that.

There is also a plain-JSON REST API at `https://terac.com/api/external/v2`
(OpenAPI at `/openapi.json`) with a `POST /quotes → /quotes/{id}/launch` path
that has no MCP equivalent, plus webhooks. We do not use it: the MCP is the
submission requirement, and polling five submissions does not need a webhook.

## The tools that matter

23 tools, all prefixed `terac_`. `python terac_cli.py tools` lists them live.
Six carry this integration:

| Tool | Why |
|---|---|
| `terac_get_context` | Org, live balance, dashboard URLs, and Terac's own operating playbook. Terac says to call it first, and it is right |
| `terac_list_opportunities` / `terac_get_opportunity` | Read state. Free |
| `terac_create_opportunity` | **Creates a DRAFT. Free. Recruits nobody.** It is also the only way to learn the real price — Terac computes CPI while creating the draft and returns `pricing.total_cost_cents` |
| `terac_launch_draft_opportunity` | **Spends real money. Irreversible.** Not reachable from any HTTP route |
| `terac_get_submissions` / `terac_get_submission` | The human data coming back |

## The flow

```
generate N videos                                               [EXISTS]
  POST /v1/review-batches            → judge_url + public_token [EXISTS]
  POST /v1/review-batches/{id}/publish-to-terac                 [NEW]
      → terac_create_opportunity, task_url = that judge_url
      → a DRAFT. Free. Nobody recruited.
  python terac_cli.py launch <opp> --yes-spend-real-money       [NEW, MANUAL]
      → Terac recruits, screens and pays the panel
  a panellist opens the link with ?teracSubmissionId=…
      → POST /v1/review-batches/{token}/votes                   [EXISTS]
        reviewer_ref = "terac:<teracSubmissionId>"
  GET /v1/review-batches/{id}/results  → human vs engine score  [EXISTS]
  POST /v1/videos/{id}/regenerate      → notes applied          [EXISTS]
  the diff between the two recipes IS the before/after          [EXISTS]
```

**There is no import step for the ratings.** Terac appends `submissionId`,
`teracSubmissionId` and `taskId` to the `task_url` per participant. The judge
page reads `teracSubmissionId` out of the query string and sends it as
`reviewer_ref`, so a panellist's rating lands in `review_votes` already
attributed and already per-video. `review_votes` has a unique index on
`(batch_id, video_id, reviewer_ref)`, so a panel platform's retry updates the
row instead of double-counting the star.

`POST /v1/terac/opportunities/{id}/sync` is reconciliation, not import. It
matches Terac's submissions against `review_votes` and returns anything
unmatched — a panellist Terac paid whose vote never reached us is the failure
worth seeing. It **does not invent ratings.** A submission's free-text narrative
belongs to the batch, not to one video, so it is imported only when an operator
names a video to hang it on (`?attach_to_video_id=…`), as a comment-only vote.

## The judge page — `VIRA_JUDGE_BASE_URL`

The `task_url` is the entire integration, so where it lands is not a detail.

```
VIRA_JUDGE_BASE_URL=https://console.ideaplaces.com/judge
```

**Set this in the box's env file.** `vira/api/routes/reviews.py` reads it from
the environment at import time and falls back to this API's own JSON endpoint
when it is empty — which is a live, correct, and completely useless link for a
human being. A panellist Terac has already been paid for arrives at
`https://vira.ideaplaces.com/v1/review-batches/<token>` and is shown a JSON
blob. Nothing errors; the money is just gone.

Because it is read at import, **the value only takes effect after uvicorn
restarts**, and because `judge_url` is computed when the batch is created, a
batch minted before the restart keeps the old URL. Order of operations on the
box: set the variable → restart → `POST /v1/review-batches` → publish *that*
batch to Terac.

Verify it took:

```bash
curl -s -X POST https://vira.ideaplaces.com/v1/review-batches \
  -H 'content-type: application/json' \
  -d '{"title":"smoke","video_ids":["<id>","<id>"]}' | jq -r .judge_url
# https://console.ideaplaces.com/judge/<public_token>   ← not /v1/review-batches/
```

The dry-run payload printed further up in this document shows the fallback
form, because it was captured with the variable unset.

Changing the base URL does not break reconciliation: `terac.batch_token_of`
takes the **last path segment** of `task_url`, and `/judge/<token>` ends in the
token exactly as `/v1/review-batches/<token>` did. Query parameters are not
part of the path, so the ones Terac appends per participant are ignored by it.

### What is at that URL

`ui/app/judge/[token]/page.tsx` in the Next.js console — public, no auth, and
deliberately outside the console's own chrome (`ui/app/judge/layout.tsx` covers
the operator nav rather than the judge being shown links to Corpus and
Library). It calls exactly two endpoints, both of them the public ones:
`GET /v1/review-batches/{token}` and `POST /v1/review-batches/{token}/votes`.

It never asks for a score. The judge payload omits `score`, `disposition` and
`lane`, and `getJudgeBatch` in `ui/lib/api.ts` whitelists the five fields it
reads on top of that, so a field added to that response later cannot reach a
panellist's eyes by accident.

### How `reviewer_ref` is resolved

| Arrives with | `reviewer_ref` sent | Reconciles via sync |
|---|---|---|
| `?teracSubmissionId=abc123` | `terac:abc123` | yes |
| `?submissionId=abc123` and no `teracSubmissionId` | `terac:abc123` | yes, if Terac's two ids agree |
| neither | `anon:<uuid>`, persisted in `localStorage` | no — and should not |

`teracSubmissionId` is the contract, and it is the one that matches
`terac.submission_ref`, which stores a submission under `terac:<submission id>`.
`submissionId` is consulted **only** when the first is absent: Terac appends
both, and an anonymous ref for someone who was paid is a worse failure than a
ref an operator has to match up by hand.

Anyone else — the team, a judge at a demo table, anyone handed the link — gets
a random ref kept in `localStorage` so a reload is still the same person, under
an `anon:` prefix so the two populations stay separable in `review_votes`
forever after. An unpaid opinion must never be counted as panel data.

### Two things the page has to work around

- **`rating` is required.** `VoteRequest.rating` is `ge=1, le=5` with no
  default, so a pick or a comment on its own has nowhere to go. The page keeps
  both in the draft and says "give it a rating and this gets saved" rather than
  posting a 422 into the void. (`store.record_vote` accepts `rating=None` — the
  sync route uses it for comment-only rows — but the HTTP schema does not.)
- **There is no read-back of one's own votes.** The judge payload is one-way
  and `/results` is keyed by batch id, not by the public token, precisely so
  holding the judge link does not hand you the running tally. So "saved" after
  a reload comes from a local receipt of what posted successfully, not from
  the server.

Votes post on every tap rather than at the end, and re-answering is an edit,
not a duplicate — `review_votes` is unique on `(batch_id, video_id,
reviewer_ref)` and the insert upserts. The page says "this replaced your
earlier answer" out loud so a panellist who changes their mind does not think
they have double-voted and abandon.

## Four choices in the payload

- **`activity`, not `interview`.** Both take a `task_url`; `interview` frames it
  as a session the participant books into. Rating five films in a browser tab is
  an activity.
- **No `screening_questions`.** A screener triggers a mandatory AI voice
  interview with *every* applicant who passes the form. It costs no money and a
  lot of hours. Rating an ad needs no filtering.
- **`unrestricted_audience: true`, no `filters`.** "General Population" is the
  fastest fill, and Terac **rejects** a create that carries neither.
- **`review_type: manual_review`.** `auto_approve` only pays correctly when the
  task URL's provider redirects to `https://terac.com/api/external/callback`.
  Until the judge page performs that redirect, manual review is the mode where a
  junk submission can still be refused.

## Budget arithmetic

Balance is **$25.00**, and it is real money.

```
cost to launch = CPI × num_participants + platform fee
```

CPI is derived from `num_participants` and `tasks[].duration_minutes`; we do not
set it. Two consequences:

1. **You cannot know the price without creating the draft.** Creating one is
   free and deletable (`terac_delete_opportunity`), and the response carries
   `pricing.total_cost_cents`. Read it, do not estimate it.
2. **Duration drives the price**, so a guessed duration is an invented budget.
   Five 30-second videos is a genuine 5 minutes; that is the default.

`terac_request_feasibility` gets a *human*-confirmed CPI instead of a machine
estimate, but a human at Terac has to answer it (~1 hour). Not on a one-day clock.

**The route caps `num_participants` at 25 and defaults to 5**, because a publish
that quietly asks for 50 is how a $25 balance disappears. For scale: the two
pre-existing drafts in this org are both sized at 50 participants and would not
fit the balance.

## Do not spend by accident

Three guards, all tested:

- `publish-to-terac` defaults to **`dry_run: true`** and returns the payload it
  *would* send. Creating even a free draft takes an explicit `dry_run: false`.
- **No HTTP route can launch anything.** `test_no_route_anywhere_can_launch_an_opportunity`
  asserts no path in the app contains "launch".
- `terac.launch_draft` refuses unless called with
  `i_understand_this_spends_real_money=True`, and the CLI wraps that in
  `--yes-spend-real-money`. A launch is a thing a human types twice.

`terac_stop_opportunity` refunds only the *unused* remainder. A launch is not undoable.

## Prove it to a judge

Four commands, all read-only, all against the live MCP:

```bash
python terac_cli.py status     # org Vira, balance $25.00, 23 tools, live opportunities
python terac_cli.py tools      # the 23 terac_* tools, straight from the server
curl -s https://vira.ideaplaces.com/v1/terac/status | jq
curl -s https://vira.ideaplaces.com/v1/terac/tools  | jq '.count'
```

Then show the integration itself — the dry run prints the exact tool call
without making it:

```bash
python terac_cli.py publish <batch_id>
```

```json
{
  "tool": "terac_create_opportunity",
  "arguments": {
    "title": "Rate five AI-generated video ads",
    "internal_title": "vira · review batch <batch_id>",
    "project_id": "cf60f5gh587n98a0fwosrzs6",
    "num_participants": 5,
    "business_type": "b2c",
    "unrestricted_audience": true,
    "tasks": [{
      "sequence": 1,
      "task_type": "activity",
      "review_type": "manual_review",
      "task_url": "https://vira.ideaplaces.com/v1/review-batches/<public_token>",
      "title": "Rate the ads",
      "duration_minutes": 5
    }]
  }
}
```

That `task_url` is the whole point: it is the same link
`POST /v1/review-batches` hands out, and a Terac panellist opens it with no
account, sees no engine score, and rates the films.

## Going live, when someone decides to

```bash
python terac_cli.py publish <batch_id> --create        # free draft; prints the real price
python terac_cli.py show <opportunity_id>              # read it back before paying
python terac_cli.py launch <opportunity_id> --yes-spend-real-money   # SPENDS
python terac_cli.py responses <opportunity_id>         # submissions as they land
curl -X POST https://vira.ideaplaces.com/v1/terac/opportunities/<id>/sync
curl https://vira.ideaplaces.com/v1/review-batches/<batch_id>/results
```

Check the balance against the draft's price before the third line. If it falls
short, top up at `https://terac.com/vira-msuo4fry/settings/finance` — and never
assemble a Terac dashboard URL by pattern; the routes are not uniform and every
response carries an absolute one.

## Existing state in this org (2026-08-15)

Two DRAFT opportunities pre-date this integration, both created 18:03Z, both at
50 participants, both unpriced, neither launched, **neither carrying a
`task_url`** — so neither is connected to a review batch.

| id | title | note |
|---|---|---|
| `bo55vs90u6wuxi3sk9uwxa8t` | Consumer Products Ad & Video Generation Expert Review | b2b, US/18+/en filters, `interview` task, LinkedIn required |
| `kdn9avar00lmd0mg5ajhpnwi` | Marketing Workflow Research Study | b2b, two-question screener, `activity` task, 20 min |

Leave them alone. At 50 participants neither fits $25, and the second one's
screener would trigger an AI voice interview per applicant.
