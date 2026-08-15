"""Settings. Everything overridable by env; defaults point at the live project."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Lovable Cloud (Supabase) ---------------------------------------
    # Publishable key, and it stays in a public repo on purpose. It is
    # RLS-bound, read-only, and Lovable itself commits it to the frontend repo
    # and ships it in every browser bundle — that is what "publishable" means.
    # The keys that DO matter (Gemini, ElevenLabs, Anthropic, Azure, Stripe,
    # the agent password) have no defaults here and come from the environment.
    # If this ever needs to change, rotate it in Lovable, not here.
    supabase_url: str = "https://otsqjpmsiysitpkqoejr.supabase.co"
    supabase_key: str = "sb_publishable_kJfcznb5ZUS99xf4ulBw7w_10R62tZs"
    # Agent account used for writes. Reads work without it.
    agent_email: str | None = None
    agent_password: str | None = None
    # The agent's auth.users id — RLS matches it against companies.owner_id.
    agent_user_id: str | None = None

    # --- this service's own Postgres -------------------------------------
    # Not Lovable Cloud. The REST API owns its own database (jobs, videos,
    # recipes, review); Lovable Cloud stays read-only and reachable only over
    # PostgREST. Unset means the API layer is not configured — the pipeline
    # itself runs without it.
    api_database_url: str | None = None

    # --- selection ------------------------------------------------------
    # A 2021 mop video is not a trend, however many views it has.
    max_age_days: int = 90
    shortlist_size: int = 20
    # Cap per format so the shortlist isn't six unboxings in a row.
    max_per_format: int = 4
    english_only: bool = True

    # --- scoring --------------------------------------------------------
    surface_threshold: float = 4.5
    watchlist_threshold: float = 3.5
    evidence_floor: float = 3.0

    # --- model ----------------------------------------------------------
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5"

    # --- agentic crew -----------------------------------------------------
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    agent_model: str = "gpt-5.4"

    # --- imagery ---------------------------------------------------------
    # "gemini" generates the written frame; "stock" searches Openverse for an
    # approximation of it. Generation wins on relevance by a wide margin.
    image_source: str = "gemini"
    gemini_api_key: str | None = None
    image_model: str = "gemini-3.1-flash-image"
    # Vision, for COHESION: reads back what the generated frames ACTUALLY show.
    vision_model: str = "gemini-3.5-flash"

    # --- voice / render --------------------------------------------------
    elevenlabs_api_key: str | None = None
    # Liam — "Energetic, Social Media Creator". A pitchman, not a narrator.
    elevenlabs_voice_id: str | None = "TX3LPaxmHKxFdv7VOQHJ"
    # v3 understands inline performance tags; v2 exposes stability/style knobs.
    elevenlabs_model: str = "eleven_v3"
    voice_tags: bool = True
    # Low stability is deliberate: it widens emotional range. High stability
    # produces the flat, mechanical read that makes an ad sound like a subtitle.
    voice_stability: float = 0.25
    # v3 only: 0.0 Creative / 0.5 Natural / 1.0 Robust.
    voice_stability_v3: float = 0.0
    voice_similarity: float = 0.6
    voice_style: float = 0.75

    # Burn director camera notes into the frame — a shooting guide, not an ad.
    show_shot_notes: bool = False
    fps: int = 30
    width: int = 1080
    height: int = 1920


@lru_cache
def settings() -> Settings:
    return Settings()
