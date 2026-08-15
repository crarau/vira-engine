"""Settings. Everything overridable by env; defaults point at the live project."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Lovable Cloud (Supabase) ---------------------------------------
    # Publishable key: public by design, RLS-bound. Committed by Lovable into
    # company-essence-lab/.env, so it is not a secret we are leaking.
    supabase_url: str = "https://otsqjpmsiysitpkqoejr.supabase.co"
    supabase_key: str = "sb_publishable_kJfcznb5ZUS99xf4ulBw7w_10R62tZs"
    # Agent account used for writes. Reads work without it.
    agent_email: str | None = None
    agent_password: str | None = None

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
    voice_similarity: float = 0.6
    voice_style: float = 0.75

    fps: int = 30
    width: int = 1080
    height: int = 1920


@lru_cache
def settings() -> Settings:
    return Settings()
