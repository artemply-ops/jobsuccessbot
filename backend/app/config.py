"""Настройки приложения. Все секреты только из окружения или .env, в коде их нет."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"  # dev | prod
    database_url: str = f"sqlite+aiosqlite:///{(ROOT / 'data' / 'jobpilot.db').as_posix()}"
    files_dir: Path = ROOT / "data" / "files"
    web_dist: Path = ROOT / "web" / "dist"
    public_url: str = "http://localhost:8000"

    # Сессии веб-клиента
    jwt_secret: str = "dev-only-change-me-this-is-not-a-secret-000"
    jwt_ttl_hours: int = 24 * 14

    # Telegram
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    admin_tg_ids: list[int] = []  # доступ к /api/admin/*
    run_scheduler: bool = False  # ежедневное обновление каталога и вакансий в процессе API

    # LLM. Во внешнюю модель уходит только обезличенный текст (см. services/pii.py)
    anthropic_model_main: str = "claude-opus-5"  # профиль, досье, CV и письмо
    anthropic_model_scoring: str = "claude-sonnet-5"  # массовый скоринг вакансий
    llm_enabled: bool = True

    # Распознавание речи: голос это ПДн, поэтому только российский провайдер или локально
    stt_provider: str = "yandex"  # yandex | none
    yandex_api_key: str = ""
    yandex_folder_id: str = ""

    # Источники вакансий
    superjob_app_id: str = ""  # без ключа коннектор SuperJob выключен
    http_user_agent: str = "JobPilotBot/0.1 (+https://jobpilot.example; contact: admin)"

    # Скоринг
    prefilter_top_n: int = 50
    notify_threshold: int = 75


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == "prod" and (s.jwt_secret.startswith("dev-only") or len(s.jwt_secret) < 32):
        raise RuntimeError("В prod задайте JWT_SECRET длиной от 32 символов")
    s.files_dir.mkdir(parents=True, exist_ok=True)
    return s
