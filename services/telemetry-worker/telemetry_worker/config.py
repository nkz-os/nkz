"""
Application settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_prefix: str = "/api"
    postgres_url: str
    redis_url: str = "redis://redis-service:6379/0"
    enable_queue: bool = True
    queue_dsn: Optional[str] = None
    log_level: str = "INFO"
    orion_url: str = "http://orion-ld-service:1026"
    context_url: str = "http://api-gateway-service:5000/ngsi-ld-context.json"

    model_config = {"env_prefix": "", "case_sensitive": False}


@lru_cache()
def get_settings() -> Settings:
    return Settings()

