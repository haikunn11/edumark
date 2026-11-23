from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "EduMark AI - Hệ thống Chấm bài Tự động Thông minh"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"

    # AI Model Settings
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")  # "openai" hoặc "gemini"
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEFAULT_OPENAI_MODEL: str = "gpt-4o-mini"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_VISION_MODEL: str = "gemini-1.5-flash"
    DEFAULT_REASONING_MODEL: str = "gemini-1.5-flash"

    # Storage & Cache Settings
    UPLOAD_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "uploads")
    CACHE_TTL_SECONDS: int = 86400  # 24 giờ
    USE_REDIS: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # Quality Gate thresholds
    BLUR_THRESHOLD: float = 80.0
    MIN_CONFIDENCE_AUTO_APPROVE: float = 0.90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
