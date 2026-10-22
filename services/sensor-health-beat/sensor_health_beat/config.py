"""
Sensor Health Beat — configuration.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    orion_url: str = field(
        default_factory=lambda: os.getenv("ORION_URL", "http://orion-ld-service:1026")
    )
    context_url: str = field(
        default_factory=lambda: os.getenv(
            "CONTEXT_URL",
            "http://api-gateway-service:5000/ngsi-ld-context.json",
        )
    )
    timescale_dsn: str = field(
        default_factory=lambda: os.getenv("TIMESCALE_DSN", "")
    )
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://redis-service:6379/0")
    )
    beat_interval_minutes: int = int(os.getenv("BEAT_INTERVAL_MINUTES", "15"))
    stagnation_query_window_hours: int = int(
        os.getenv("STAGNATION_QUERY_WINDOW_HOURS", "24")
    )
    recovery_valid_count: int = int(os.getenv("RECOVERY_VALID_COUNT", "3"))

    @property
    def is_valid(self) -> bool:
        return bool(self.timescale_dsn)
