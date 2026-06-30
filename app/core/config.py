from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Goalio API"
    app_env: str = "development"
    firebase_project_id: str = "goalio-c42bc"
    allowed_origins: str = "http://localhost:3000"
    allow_dev_auth: bool = False
    api_football_key: str = ""
    football_season: int = 2026
    football_sync_max_requests: int = 250
    football_request_interval_seconds: float = 6.2
    espn_request_interval_seconds: float = 0.5
    api_football_max_requests: int = 95

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
