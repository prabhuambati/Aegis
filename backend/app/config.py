from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("OBS_DB_PATH", "./data/observability.db")
    storage_backend: str = os.getenv("STORAGE_BACKEND", "sqlite").lower()
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql://aegis:aegis@postgres:5432/aegis")
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "")
    tempo_url: str = os.getenv("TEMPO_URL", "")
    llm_provider: str = os.getenv("LLM_PROVIDER", "local").lower()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    demo_window_minutes: int = int(os.getenv("DEMO_WINDOW_MINUTES", "30"))


settings = Settings()
