# Image generation

One endpoint: `POST /v1/ads/image`. You do not describe a picture — you name a
brand and a product, and the engine does what it does for video. It pulls the
real TikToks in that category, decides what the ad should say, generates the
frame, and burns the hook onto it in the same one-word bottom-third style the
videos use.

It is the image sibling of `POST /v1/videos`: same corpus, same creative lanes,
same hook rules, same caption look, same evidence gate. One frame instead of
thirty per second.

**Base URL:** `https://vira.ideaplaces.com`
**Auth:** none by default. See "Cost and limits", and `BRIEFS.md` §8 if
`VIRA_ENGINE_TOKEN` is ever set.

> **The raw Gemini proxy has been withdrawn.** `POST /v1/image` and
> `GET /v1/image/models` used to pass a prompt straight through to Gemini and
> hand back the picture. They now return **404**. If you were calling them, move
> to the endpoint below, or call Google directly with your own key — this
> service is no longer a way to borrow ours.

---

## `POST /v1/ads/image`

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

Both URLs are permanent, absolute and CORS-open — drop either straight into an
`<img src>` from any origin.

### Request fields

| field | default | notes |
|---|---|---|
| `brand` | required | 2–80 chars |
| `product` | required | 2–200 chars. Be specific: "cocoa hazelnut overnight oats" beats "oats". |
| `lane` | auto | one of the five creative lanes — see `GET /v1/lanes`. Omitted, the engine picks deterministically on (brand, product), so a recipe re-runs identically. |
| `category` | inferred | which slice of the corpus to ground in |
| `headline` | auto | supply one to skip the writer. It is still checked against the hook rules and **rejected with 422 if it breaks them**, before anything is spent. |
| `aspect_ratio` | `"9:16"` | `9:16 16:9 1:1 4:3 3:4 2:3 3:2`. `burn_text` requires 9:16 — the caption band is derived from a 1920px height. |
| `burn_text` | `true` | `false` returns the clean frame only and skips Remotion |

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
ranked TikToks (see `HOOK-CRAFT.md`): a finite clause of 4–14 words containing
I/we/you, exactly one word in CAPS, and never opening on an imperative, a
negation, a demonstrative or the brand name. Verbless fragments — "Ten seconds.
That's it." — are the single most common failure in the bottom cohort and are
rejected outright.

### The evidence gate applies

Same gate as video, same threshold, and it runs in Python where no model can see
it. An ad that cannot point at real corpus rows scores low on evidence and is
dropped regardless of how good the copy reads.

| status | meaning |
|---|---|
| **200** | ad generated |
| **404** | you called the withdrawn proxy — see the note above |
| **422** | bad lane or aspect ratio, or a supplied `headline` that breaks the hook rules; the response names which rule |
| **409** | generated but dropped on evidence; the body carries `drop_reason` and the full attempt |
| **429** | rate limited |
| **503** | a required upstream (Gemini, Azure) is not configured |

A 409 is not a failure of the service. It means the corpus had nothing to stand
on for that brand and product, and shipping the ad anyway would be inventing
evidence.

## Cost and limits

**Every call spends real money** on the account behind this service. It is
unauthenticated because this is a hackathon environment and the team needs it
without a credential — but open to the team and open to a script in a loop
should not cost the same, so two guards stand in for auth:

- **20 images per minute**
- **500 images per day**

Both live in `vira/api/imagelimit.py`, are per-process, and reset when the
service restarts. Exceeding either returns 429 saying which one you hit.

## Notes

- **Latency** is ~35s. Do not block a UI on it; show a placeholder.
- **Images are ~600–800 KB** at 9:16.
- **Storage is not guaranteed.** Files live on the box's disk. Nothing expires
  today, but this is a hackathon service — copy anything you need to keep.
- **Recipes are public.** Every generated ad writes a `RECIPE.md` next to it with
  the verbatim prompts. That is deliberate — it is how a judge checks the work —
  so never put anything secret in a brand or product name.

## Related

- `POST /v1/videos` — the video sibling. Same corpus, same lanes, same gate.
- `POST /v1/briefs` — the richer brief payload, with curated `references[]`.
- `GET /v1/lanes` — the five creative lanes, with briefs.
- `HOOK-CRAFT.md` — where the hook rules come from, with the corpus numbers.
- `API.md` — everything else this service exposes.
