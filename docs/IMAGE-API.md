# Image generation proxy

A public endpoint in front of Google's Gemini image models ("nano banana"). Call
it without holding a Google key — the credential lives on the server and never
reaches the client.

**Base URL:** `https://vira.ideaplaces.com`
**Auth:** none. See "Cost and limits" for why that is not the same as free.

Everything below was verified against the live endpoint.

## Generate an image

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

## What the server accepts

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

## Errors

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

## Cost and limits

**Every call spends real money** on the account behind this service. It is
unauthenticated because this is a hackathon environment and the team needs it
without a credential — but open to the team and open to a script in a loop
should not cost the same, so two guards stand in for auth:

- **20 images per minute** across all callers
- **500 images per day**

Both are per-process and reset when the service restarts. Exceeding either
returns 429 with which one you hit. If you need a bulk run, say so rather than
retrying through the limit.

## Notes

- **Latency** is 8–12s for `flash`. Do not block a UI on it; show a placeholder.
- **Images are ~600–800 KB** at 9:16.
- **Storage is not guaranteed.** Files live on the box's disk under
  `out/generated/`. Nothing expires today, but this is a hackathon service —
  copy anything you need to keep.
- **Prompt quality dominates.** Name the subject, the light, the surface, the
  camera and the framing. "A jar on a counter" and "a glass jar of overnight
  oats on a dark counter at 6am, single warm lamp, shot on a phone, shallow
  depth of field" cost the same and are not remotely the same picture.

## Related

`POST /v1/ads/image` (in progress) does more than proxy: it grounds a concept in
the real TikTok corpus, generates the frame, and burns the hook onto it using the
same caption system as the videos — a finished ad rather than a raw image. This
endpoint stays as the plain pass-through.
