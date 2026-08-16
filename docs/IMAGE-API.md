# Image generation

Two endpoints. They sound similar and they are not.

| | `POST /v1/image` | `POST /v1/ads/image` |
|---|---|---|
| What you send | a prompt | a brand and a product |
| What you get | the picture you described | a finished ad |
| Who decides the picture | you | the engine |
| Grounded in the TikTok corpus | no | yes |
| Text on the image | suppressed | the hook, burned on |
| Time | ~9s | ~35s |
| Status | **live** | building — spec below is the contract |

The first is a credential proxy: it exists so you can call Gemini without
holding a Gemini key. The second is the image sibling of `POST /v1/videos` —
same corpus, same hook rules, same caption look, same evidence gate, one frame
instead of thirty per second.

If you know what you want to see, use the first. If you know what you want to
*sell*, use the second.

**Base URL:** `https://vira.ideaplaces.com`
**Auth:** none. See "Cost and limits".

---

# 1. `POST /v1/image` — the proxy

A public endpoint in front of Google's Gemini image models ("nano banana"). The
credential lives on the server and never reaches the client. The request shape
deliberately mirrors the upstream one, because a pass-through with opinions is
just a worse API.

Everything in this section was verified against the live endpoint.

```bash
curl -X POST https://vira.ideaplaces.com/v1/image \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "A glass jar of cocoa hazelnut overnight oats on a dark kitchen counter at 6am, one hand closing the lid, single warm lamp, shot on a phone",
        "aspect_ratio": "9:16"
      }'
```

```json
{
  "url": "https://vira.ideaplaces.com/media/generated/c7d60747e2004fa8944199dc8879f81f.jpg",
  "prompt": "A glass jar of cocoa hazelnut overnight oats on a dark kitchen counter…",
  "model": "gemini-3.1-flash-image",
  "aspect_ratio": "9:16",
  "bytes": 670323,
  "elapsed_ms": 9525
}
```

The URL is permanent, absolute, and CORS-open — drop it straight into an
`<img src>` from any origin.

### Request fields

| field | default | notes |
|---|---|---|
| `prompt` | required | 3–4000 chars. Under 3 returns **422**. |
| `aspect_ratio` | `"9:16"` | one of `9:16 16:9 1:1 4:3 3:4 2:3 3:2`. Anything else returns **422**. |
| `model` | `"flash"` | `flash`, `flash-lite`, `pro` — or pass a full Gemini model id. |
| `allow_text` | `false` | see below |

### `allow_text` — off on purpose

By default the server appends *"No text, no lettering, no captions, no logos, no
watermarks, no signage."* to your prompt.

Two reasons, both learned the hard way: image models render text badly, and any
lettering collides with captions overlaid later. If you genuinely want a sign in
the frame, set `allow_text: true`.

### Bytes instead of a URL

```bash
curl -X POST 'https://vira.ideaplaces.com/v1/image?raw=true' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a single ripe strawberry on slate, hard side light","aspect_ratio":"1:1"}' \
  --output strawberry.jpg
```

Returns `image/jpeg` directly. Nothing is stored server-side on this path.

### What the server accepts

```bash
curl https://vira.ideaplaces.com/v1/image/models
```

```json
{
  "models": {
    "flash":      "gemini-3.1-flash-image",
    "flash-lite": "gemini-3.1-flash-lite-image",
    "pro":        "gemini-3-pro-image"
  },
  "default": "flash",
  "aspect_ratios": ["1:1","16:9","2:3","3:2","3:4","4:3","9:16"],
  "limits": { "burst_per_minute": 20, "daily_max": 500 }
}
```

`flash` is the default because it returns in about 9 seconds and the quality
difference only shows on complex compositions. Reach for `pro` when a frame has
several interacting subjects.

### Errors

The upstream status is passed through rather than flattened into a 500 — a rate
limit from Google is not a bug in this service, and you deserve to know which
one you hit.

| status | meaning |
|---|---|
| **422** | bad `aspect_ratio`, prompt too short, or the model returned no image (usually a safety filter) |
| **429** | you hit the burst or daily limit here |
| **502** | Google unreachable |
| **503** | the server has no Gemini key configured |
| other 4xx/5xx | forwarded from Google, with its message |

---

# 2. `POST /v1/ads/image` — the generated ad

*Building now. This section is the contract, not a description of running code —
if the implementation and this document disagree, the document is right and the
code is a bug.*

You do not describe a picture. You name a brand and a product, and the engine
does what it does for video: pulls the real TikToks in that category, decides
what the ad should say, generates the frame, and burns the hook onto it in the
same one-word bottom-third style the videos use.

```bash
curl -X POST https://vira.ideaplaces.com/v1/ads/image \
  -H 'Content-Type: application/json' \
  -d '{
        "brand": "Sunday Oats",
        "product": "cocoa hazelnut overnight oats",
        "lane": "founder-story"
      }'
```

```json
{
  "id": "img_7f3a91",
  "url": "https://vira.ideaplaces.com/media/sunday-oats/v012-.../founder-story/ad.jpg",
  "image_url": "https://vira.ideaplaces.com/media/sunday-oats/v012-.../founder-story/shot00.jpg",
  "headline": "I stopped eating breakfast for two YEARS",
  "cta": "Tap to try the first batch",
  "lane": "founder-story",
  "hook_shape": "first-person-admission",
  "grounded_in": [
    {"url": "https://www.tiktok.com/@…/video/…", "views": 412000}
  ],
  "recipe_url": "https://vira.ideaplaces.com/media/sunday-oats/v012-.../RECIPE.md",
  "elapsed_ms": 34120
}
```

`url` is the finished ad — image plus burned-on headline. `image_url` is the
clean frame underneath it, for when you want to lay out your own text.

### Request fields

| field | default | notes |
|---|---|---|
| `brand` | required | 2–80 chars |
| `product` | required | 2–200 chars. Be specific: "cocoa hazelnut overnight oats" beats "oats". |
| `lane` | auto | one of the five creative lanes — see `GET /v1/lanes`. Omit and the engine picks. |
| `category` | inferred | which slice of the corpus to ground in |
| `headline` | auto | supply one to skip the writer. It is still checked against the hook rules and **rejected with 422 if it breaks them.** |
| `aspect_ratio` | `"9:16"` | same set as the proxy |
| `burn_text` | `true` | `false` returns the clean frame only |

### What "grounded" buys you

The lane is not a filter on wording. Each of the five owns a copy brief, a visual
grade and a hook grammar, so `founder-story` and `demo-first` produce ads that
differ in what they look like, not just what they say:

| lane | look |
|---|---|
| `problem-first` | cool drab morning light, cluttered interiors, handheld |
| `demo-first` | bright high-key, saturated, product hero, sharp |
| `founder-story` | warm low light, deep shadow, grainy, very shallow focus |
| `social-proof` | direct flash, slightly overexposed, candid, busy |
| `contrarian` | hard single-source light, near-monochrome, stark negative space |

The headline obeys the same rules the video hooks do, measured against 2,669
ranked TikToks (see `docs/HOOK-CRAFT.md`): a finite clause of 4–14 words
containing I/we/you, exactly one word in CAPS, and never opening on an
imperative, a negation, a demonstrative or the brand name. Verbless fragments —
"Ten seconds. That's it." — are the single most common failure in the bottom
cohort and are rejected outright.

### The evidence gate applies

Same gate as video, same threshold, and it runs in Python where no model can see
it. An ad that cannot point at real corpus rows scores low on evidence and is
dropped regardless of how good the copy reads.

| status | meaning |
|---|---|
| **200** | ad generated |
| **422** | bad lane, or a supplied `headline` that breaks the hook rules — the response names which rule |
| **409** | generated but dropped on evidence; body carries `drop_reason` and the best attempt |
| **429** | rate limited, shared with the proxy |
| **503** | a required upstream (Gemini, Azure) is not configured |

A 409 is not a failure of the service. It means the corpus had nothing to stand
on for that brand and product, and shipping the ad anyway would be inventing
evidence.

---

## Cost and limits

**Every call to either endpoint spends real money** on the account behind this
service. It is unauthenticated because this is a hackathon environment and the
team needs it without a credential — but open to the team and open to a script
in a loop should not cost the same, so two guards stand in for auth:

- **20 images per minute** across all callers
- **500 images per day**

Both are per-process, shared by both endpoints, and reset when the service
restarts. Exceeding either returns 429 with which one you hit. If you need a
bulk run, say so rather than retrying through the limit.

## Notes

- **Latency** is 8–12s for the proxy, ~35s for a generated ad. Do not block a UI
  on either; show a placeholder.
- **Images are ~600–800 KB** at 9:16.
- **Storage is not guaranteed.** Files live on the box's disk. Nothing expires
  today, but this is a hackathon service — copy anything you need to keep.
- **Prompt quality dominates** on the proxy. Name the subject, the light, the
  surface, the camera and the framing. "A jar on a counter" and "a glass jar of
  overnight oats on a dark counter at 6am, single warm lamp, shot on a phone,
  shallow depth of field" cost the same and are not remotely the same picture.
- **Recipes are public.** Every generated ad writes a `RECIPE.md` next to it with
  the verbatim prompts. That is deliberate — it is how a judge checks the work —
  so never put anything secret in a brand or product name.

## Related

- `POST /v1/videos` — the video sibling. Same corpus, same lanes, same gate.
- `GET /v1/lanes` — the five creative lanes, with briefs.
- `docs/HOOK-CRAFT.md` — where the hook rules come from, with the corpus numbers.
- `docs/API.md` — everything else this service exposes.
