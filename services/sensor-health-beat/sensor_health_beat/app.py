"""
Sensor Health Beat — FastAPI entry point.
Runs as an internal service; the actual beat loop is triggered
by a cron schedule (CronJob) that hits POST /trigger.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .worker import SensorHealthBeat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()
beat = SensorHealthBeat(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await beat.start()
    yield
    await beat.stop()


app = FastAPI(title="sensor-health-beat", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/trigger")
async def trigger():
    """Called by the CronJob schedule to run one health check cycle."""
    asyncio.create_task(beat.run_once())
    return {"status": "triggered"}
