# Secrets in this repository

This repo is **public on purpose**, and so are the two services it runs:
`https://vira.ideaplaces.com` and `https://console.ideaplaces.com`.

## What is in here, deliberately

`vira/config.py` hardcodes two values, and neither is a secret:

- `supabase_url` — a public project URL.
- `supabase_key` — the Supabase **publishable** key. RLS-bound and read-only.
  Lovable commits this key to the frontend repo itself and ships it in every
  browser bundle; that is what publishable means. Row Level Security is the
  control, not obscurity — anonymous reads are limited to `trends`,
  `categories` and published `companies`, and the 401 on `profiles` is that
  working.

## What is never in here

Every credential that can spend money or write data comes from the environment
and has **no default in code**:

`GEMINI_API_KEY` · `ELEVENLABS_API_KEY` · `ANTHROPIC_API_KEY` ·
`AZURE_OPENAI_API_KEY` · `AZURE_OPENAI_ENDPOINT` · `STRIPE_SECRET_KEY` ·
`TERAC_API_KEY` · `AGENT_PASSWORD` · `API_DATABASE_URL`

They live in Azure Key Vault `kv-zerohuman-hack` and reach the box as a
`chmod 600` env file written by `deploy/publish.sh`. They never pass through
git.

## Rules for anyone adding code

1. **No credential gets a default in `vira/config.py`.** If it can spend money
   or write data, it is `None` until the environment supplies it.
2. **Nothing secret is written under `out/`.** That whole tree is served
   publicly at `/media`, including `RECIPE.md` and `recipe.json`, which contain
   verbatim prompts.
3. **`.env` files are gitignored** — `.env`, `ui/.env.local`, `deploy/*.env`.
   Only `*.example` files are tracked.
4. Before making any new repo public, scan the **history**, not just the tree:

```bash
git rev-list --all | while read c; do
  git grep -I -n -E "sk-ant-api[0-9]{2}-|sk_live_|sk_test_[A-Za-z0-9]{20,}|rnd_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|eyJhbGciOi[A-Za-z0-9_-]{40,}" "$c" 2>/dev/null
done | sort -u
```

Verified clean across all commits as of 2026-08-15.
