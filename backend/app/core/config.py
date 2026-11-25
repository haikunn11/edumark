from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_BACKEND_DIR, "data")


class Settings(BaseSettings):
    PROJECT_NAME: str = "EduMark AI - Hệ thống Chấm bài Tự động Thông minh"
    VERSION: str = "0.2.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "local")

    # Security & CORS
    ADMIN_API_TOKEN: str = os.getenv("ADMIN_API_TOKEN", "")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    # AI Model Settings
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")  # "openai" hoặc "gemini"
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEFAULT_OPENAI_MODEL: str = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4o-mini")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_VISION_MODEL: str = os.getenv("DEFAULT_VISION_MODEL", "gemini-2.0-flash")
    DEFAULT_REASONING_MODEL: str = os.getenv("DEFAULT_REASONING_MODEL", "gemini-2.0-flash")

    # Storage, Database & Cache Settings
    UPLOAD_DIR: str = os.path.join(_DATA_DIR, "uploads")
    DB_PATH: str = os.getenv("EDUMARK_DB_PATH", os.path.join(_DATA_DIR, "edumark.db"))
    CACHE_TTL_SECONDS: int = 86400  # 24 giờ
    USE_REDIS: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # Quality Gate thresholds
    BLUR_THRESHOLD: float = 80.0
    MIN_BRIGHTNESS: float = 60.0
    MAX_BRIGHTNESS: float = 248.0
    MIN_CONFIDENCE_AUTO_APPROVE: float = 0.90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(settings.DB_PATH)), exist_ok=True)
