"""One-shot CLI entry point for the sensor health beat.

Run by the CronJob (`python3 -m sensor_health_beat`): executes a single
health-check cycle and exits. This replaces the earlier misconfiguration where
the CronJob started the FastAPI server (`app:app`) — a long-lived process that
never exits, so with `concurrencyPolicy: Forbid` a single run blocked every
subsequent scheduled run indefinitely.

`app.py` still exposes the same cycle via `POST /trigger` for a long-lived
server deployment; the CronJob uses this one-shot runner instead.
"""

import asyncio
import logging

from .config import Settings
from .worker import SensorHealthBeat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _run() -> None:
    beat = SensorHealthBeat(Settings())
    await beat.start()
    try:
        await beat.run_once()
    finally:
        await beat.stop()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
