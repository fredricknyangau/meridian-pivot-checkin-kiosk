import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://fred:devpass@localhost:5433/solstice_kiosk"
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    RABBITMQ_QUEUE: str = "print_jobs"
    API_BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
