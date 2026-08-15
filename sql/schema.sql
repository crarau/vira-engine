-- vira-engine — the engine's OWN database.
--
-- This is not the Lovable Cloud (Supabase) schema. The engine still reads
-- trends from there over PostgREST, but that project exposes no connection
-- string, no service_role, and no migration path — so anything the engine
-- needs to own (jobs, videos, recipes, prompts, human review) lives here, on a
-- plain Postgres it controls. Lovable calls the REST API; the two databases
-- never join.
--
-- Idempotent by construction: this file is executed on every boot by
-- vira.api.db.init_db(), so it must be safe to run against a populated
-- database. Every statement is CREATE ... IF NOT EXISTS. Nothing here drops or
-- rewrites a column; a real change gets a new numbered file next to this one.
--
-- gen_random_uuid() is core Postgres since 13 — no pgcrypto extension, which
-- would need privileges a managed instance may not hand out at boot.

-- ---------------------------------------------------------------------------
-- companies
-- ---------------------------------------------------------------------------
-- The engine's own copy, seeded from Supabase but deliberately NOT a foreign
-- key to it. Cross-database FKs do not exist, and a video must stay explicable
-- long after the row it was generated from has been edited or deleted upstream.
-- `slug` is the join key with Lovable, not `id`: the ids differ per database.

CREATE TABLE IF NOT EXISTS companies (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    bio         text NOT NULL DEFAULT '',
    mission     text NOT NULL DEFAULT '',
    website     text,
    category    text NOT NULL DEFAULT '',
    owner_name  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE companies IS
    'Engine-local copy of a Lovable company. Seeded by slug, never FK-linked.';

-- ---------------------------------------------------------------------------
-- jobs
-- ---------------------------------------------------------------------------
-- One generation request. The REST API returns a job id immediately and the
-- caller polls it, because a video takes 74s deterministic / ~350s agentic and
-- no HTTP client should be asked to hold that open.
--
-- `progress_note` is free text on purpose — it is the human-readable stage
-- ("verifying 20 sources", "rendering demo-first") shown in the Lovable UI. A
-- stage enum would have to be extended every time the pipeline grows one.

CREATE TABLE IF NOT EXISTS jobs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     uuid NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    product        text NOT NULL,
    lane           text,
    mode           text NOT NULL DEFAULT 'fast' CHECK (mode IN ('fast', 'agentic')),
    status         text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'done', 'failed')),
    progress_note  text,
    error          text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz
);

COMMENT ON COLUMN jobs.lane IS
    'Single creative lane, or NULL when the job fans out across all lanes.';

CREATE INDEX IF NOT EXISTS jobs_company_created_idx
    ON jobs (company_id, created_at DESC);

-- The worker's claim query only ever looks at unfinished work, and finished
-- rows are the overwhelming majority — so the index only carries the live ones.
CREATE INDEX IF NOT EXISTS jobs_pending_idx
    ON jobs (created_at)
    WHERE status IN ('queued', 'running');

-- ---------------------------------------------------------------------------
-- videos
-- ---------------------------------------------------------------------------
-- One finished ad. Dropped videos are stored too: "what the engine rejected and
-- why" is a first-class output, not an error path, so `disposition` and
-- `drop_reason` are columns rather than a reason to skip the insert.
--
-- No CHECK on `disposition` — the scorer currently emits surfaced / watchlist /
-- dropped, and those thresholds are expected to move as human review comes back.
-- A constraint here would turn a scoring tweak into a migration.

CREATE TABLE IF NOT EXISTS videos (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id           uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    lane             text NOT NULL DEFAULT '',
    hook             text NOT NULL DEFAULT '',
    cta              text NOT NULL DEFAULT '',
    caption          text NOT NULL DEFAULT '',
    hashtags         text[] NOT NULL DEFAULT '{}',
    duration_s       numeric,
    mp4_path         text,
    score            numeric,
    score_breakdown  jsonb NOT NULL DEFAULT '{}'::jsonb,
    disposition      text,
    drop_reason      text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN videos.score IS
    'The overall (mean of five dimensions). score_breakdown keeps the five.';
COMMENT ON COLUMN videos.mp4_path IS
    'NULL until the render lands, and stays NULL for a video that was scored '
    'and dropped before rendering.';

CREATE INDEX IF NOT EXISTS videos_job_idx ON videos (job_id);
CREATE INDEX IF NOT EXISTS videos_created_idx ON videos (created_at DESC);
CREATE INDEX IF NOT EXISTS videos_disposition_idx ON videos (disposition);

-- ---------------------------------------------------------------------------
-- recipes
-- ---------------------------------------------------------------------------
-- The tweakable record: everything needed to re-make this exact video, or to
-- make a different one by changing one field. One row per video (UNIQUE), which
-- is what lets the API expose it as /videos/{id}/recipe.
--
-- Kept as jsonb rather than normalised because the shape is authored by the
-- pipeline and read whole. Splitting beats into rows would buy a query nobody
-- runs and cost a join on every read.

CREATE TABLE IF NOT EXISTS recipes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id    uuid NOT NULL UNIQUE REFERENCES videos (id) ON DELETE CASCADE,
    plan        jsonb NOT NULL DEFAULT '{}'::jsonb,
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
    corpus      jsonb NOT NULL DEFAULT '[]'::jsonb,
    beats       jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN recipes.plan IS
    'The authored-intent layer: the director plan, the critique, the lane brief '
    'and look — everything a human edits before a re-run.';
COMMENT ON COLUMN recipes.settings IS
    'Thresholds and model ids in force, plus voice_id and the git commit of the '
    'code that ran. Without these a "re-run" reproduces something else.';
COMMENT ON COLUMN recipes.corpus IS
    'The verified source videos that were in scope. The ad was told to borrow '
    'from these and nothing else, so provenance claims are checkable.';

-- ---------------------------------------------------------------------------
-- llm_calls
-- ---------------------------------------------------------------------------
-- Verbatim prompts, in order. This is the difference between "the model wrote
-- a bad hook" and "this exact system prompt over this exact corpus wrote a bad
-- hook" — the second one is fixable.
--
-- Deliberately unbounded text: truncating a prompt to save space destroys the
-- only property this table has.

CREATE TABLE IF NOT EXISTS llm_calls (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id       uuid NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    n              int NOT NULL,
    stage          text NOT NULL DEFAULT '',
    model          text NOT NULL DEFAULT '',
    max_tokens     int,
    stop_reason    text,
    system_prompt  text NOT NULL DEFAULT '',
    user_prompt    text NOT NULL DEFAULT '',
    response       text NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Additive, so it stays in this file rather than becoming a numbered migration
-- init_db() would never read: the CREATE above covers a fresh database and this
-- covers one created before verbose mode. Neither drops nor rewrites anything.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS stage text NOT NULL DEFAULT '';

COMMENT ON COLUMN llm_calls.n IS
    'Call order within the video, 1-based, as recorded by vira.provenance.';
COMMENT ON COLUMN llm_calls.stage IS
    'Pipeline stage that made the call — plan, write, critique, score. Empty '
    'for a CLI run, which tracks no stage.';

-- Every read of this table is "all calls for one video, in order".
CREATE INDEX IF NOT EXISTS llm_calls_video_n_idx ON llm_calls (video_id, n);

-- ---------------------------------------------------------------------------
-- assets
-- ---------------------------------------------------------------------------
-- The images and audio a video is made of, with both sides of each frame: the
-- `prompt` that asked for it and the `description` of what a vision model says
-- it ACTUALLY shows. Those two disagreeing is the continuity bug the cohesion
-- checker exists to find, so both are stored rather than reconciled away.

CREATE TABLE IF NOT EXISTS assets (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id     uuid NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    beat_index   int,
    kind         text NOT NULL CHECK (kind IN ('image', 'audio')),
    path         text,
    prompt       text,
    credit       text,
    description  text
);

COMMENT ON COLUMN assets.beat_index IS
    'Beat this asset belongs to, 0-based. NULL for whole-video assets such as '
    'the narration track.';
COMMENT ON COLUMN assets.credit IS
    'Attribution line burned into the render — a licence for stock, or the '
    'generator for a synthesised frame.';
COMMENT ON COLUMN assets.description IS
    'What a vision model reports the asset actually shows, which is not always '
    'what the prompt asked for.';

CREATE INDEX IF NOT EXISTS assets_video_beat_idx ON assets (video_id, beat_index);

-- ---------------------------------------------------------------------------
-- review_batches / review_batch_videos / review_votes
-- ---------------------------------------------------------------------------
-- A batch of videos put in front of human judges. Judges arrive from an
-- external panel platform with no account here, so the batch is addressed by an
-- unguessable `public_token` rather than by its id — the token is the whole
-- auth story, which is why it is UNIQUE and indexed.

CREATE TABLE IF NOT EXISTS review_batches (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_token  text NOT NULL UNIQUE,
    title         text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    closed_at     timestamptz
);

COMMENT ON COLUMN review_batches.closed_at IS
    'Set when the panel stops accepting votes. NULL means open.';

-- Position is the presentation order. It is stored rather than derived because
-- ranking five lanes only means something if every judge sees the same order.
CREATE TABLE IF NOT EXISTS review_batch_videos (
    batch_id  uuid NOT NULL REFERENCES review_batches (id) ON DELETE CASCADE,
    video_id  uuid NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    position  int NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_id, video_id)
);

CREATE INDEX IF NOT EXISTS review_batch_videos_order_idx
    ON review_batch_videos (batch_id, position);

CREATE TABLE IF NOT EXISTS review_votes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id      uuid NOT NULL REFERENCES review_batches (id) ON DELETE CASCADE,
    video_id      uuid NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    reviewer_ref  text NOT NULL,
    rating        int CHECK (rating BETWEEN 1 AND 5),
    picked        boolean NOT NULL DEFAULT false,
    comment       text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN review_votes.reviewer_ref IS
    'Opaque id from the judge platform. Never an email or a name — the panel is '
    'recruited elsewhere and we keep no identity for it.';
COMMENT ON COLUMN review_votes.picked IS
    'Judge chose this video as the best of the batch. Independent of rating: a '
    'batch of five can have five 4-star ratings and exactly one pick.';

-- One vote per judge per video. Panel platforms retry, and a double-submitted
-- 5-star rating would quietly bias the aggregate this whole table exists to
-- produce; the constraint turns the retry into an idempotent update instead.
CREATE UNIQUE INDEX IF NOT EXISTS review_votes_one_per_reviewer_idx
    ON review_votes (batch_id, video_id, reviewer_ref);

CREATE INDEX IF NOT EXISTS review_votes_batch_idx ON review_votes (batch_id);
